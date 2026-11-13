"""DuckDB-yhteys, skeeman alustus ja kaikki SQL-kyselyt.

Muut moduulit eivät kirjoita SQL:ää itse, vaan käyttävät tätä luokkaa.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from importlib.resources import files
from pathlib import Path

import duckdb
import polars as pl

from forecaster.config import BenchmarkConfig
from forecaster.db.models import AliasRow, EvaluationRow, ModelRow, PriceRow

SCHEMA_VERSION = 1
EPSILON = 1e-3  # määrittely 4.1


class SchemaVersionError(RuntimeError):
    pass


def normalize_score(raw: float, raw_min: float, raw_max: float, eps: float = EPSILON) -> float:
    """Määrittely 4.1: skaalaus välille [eps, 1-eps].

    Arvausrajaa EI vähennetä tässä – se on IRT-mallin parametri c_j (ks. A8).
    """
    if raw_max <= raw_min:
        raise ValueError(f"raw_max ({raw_max}) <= raw_min ({raw_min})")
    y = (raw - raw_min) / (raw_max - raw_min)
    return min(max(y, eps), 1.0 - eps)


class Repository:
    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self.conn = conn

    # --- Yhteys ja skeema ---------------------------------------------------

    @classmethod
    def connect(cls, path: Path | str = ":memory:", *, read_only: bool = False) -> Repository:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        return cls(duckdb.connect(str(path), read_only=read_only))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Repository:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # DuckDB 2.x vaatii, että jokaisen lauseen tulos luetaan loppuun ennen seuraavaa
    # työtä samalla yhteydellä. Kaikki SQL kulkee näiden apufunktioiden kautta.

    def _run(self, sql: str, params: Sequence | None = None) -> list[tuple]:
        return self.conn.execute(sql, params or []).fetchall()

    def _one(self, sql: str, params: Sequence | None = None) -> tuple | None:
        rows = self._run(sql, params)
        return rows[0] if rows else None

    def _pl(self, sql: str, params: Sequence | None = None) -> pl.DataFrame:
        return self.conn.sql(sql, params=list(params) if params else None).pl()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Kaikki tai ei mitään: virhe perii koko erän (esim. puolikas ingest-ajo)."""
        self._run("BEGIN TRANSACTION")
        try:
            yield
        except BaseException:
            self._run("ROLLBACK")
            raise
        self._run("COMMIT")

    def existing_ids(self, table: str, column: str) -> set[str]:
        if table not in {"models", "benchmarks"} or column not in {"model_id", "benchmark_id"}:
            raise ValueError(f"Ei sallittu: {table}.{column}")
        return {r[0] for r in self._run(f"SELECT {column} FROM {table}")}

    def schema_version(self) -> int | None:
        exists = self._one(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'schema_version'"
        )[0]
        if not exists:
            return None
        return self._one("SELECT max(version) FROM schema_version")[0]

    def init_db(self) -> int:
        """Luo skeeman, jos sitä ei ole. Idempotentti. Palauttaa skeemaversion."""
        current = self.schema_version()
        if current is not None and current > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"Tietokannan skeemaversio {current} on uudempi kuin koodin {SCHEMA_VERSION}"
            )
        if current is not None and current < SCHEMA_VERSION:
            raise SchemaVersionError(
                f"Tietokanta on versiossa {current}, koodi {SCHEMA_VERSION}: migraatio puuttuu"
            )
        sql = files("forecaster.db").joinpath("schema.sql").read_text(encoding="utf-8")
        self._run(sql)
        if current is None:
            self._run("INSERT INTO schema_version (version) VALUES (?)", [SCHEMA_VERSION])
        return SCHEMA_VERSION

    # --- Upsertit -----------------------------------------------------------

    def _upsert(self, table: str, df: pl.DataFrame, key: Sequence[str]) -> int:
        if df.is_empty():
            return 0
        dupes = df.group_by(list(key)).len().filter(pl.col("len") > 1)
        if dupes.height:
            raise ValueError(f"{table}: toistuvia avaimia samassa erässä: {dupes.head(5).rows()}")
        cols = df.columns
        updates = [c for c in cols if c not in key]
        conflict = (
            f"DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in updates)}"
            if updates
            else "DO NOTHING"
        )
        self.conn.register("_incoming", df.to_arrow())
        try:
            self._run(
                f"INSERT INTO {table} ({', '.join(cols)}) "
                f"SELECT {', '.join(cols)} FROM _incoming "
                f"ON CONFLICT ({', '.join(key)}) {conflict}"
            )
        finally:
            self.conn.unregister("_incoming")
        return df.height

    def upsert_benchmarks(self, benchmarks: Iterable[BenchmarkConfig]) -> int:
        df = pl.DataFrame([b.model_dump() for b in benchmarks])
        return self._upsert("benchmarks", df, ["benchmark_id"])

    def upsert_models(self, rows: Iterable[ModelRow]) -> int:
        return self._upsert("models", pl.DataFrame([r.model_dump() for r in rows]), ["model_id"])

    def upsert_aliases(self, rows: Iterable[AliasRow]) -> int:
        df = pl.DataFrame([r.model_dump() for r in rows])
        return self._upsert("model_aliases", df, ["source_name", "alias"])

    def upsert_prices(self, rows: Iterable[PriceRow]) -> int:
        df = pl.DataFrame([r.model_dump() for r in rows])
        return self._upsert("model_prices", df, ["model_id", "valid_from", "source_name"])

    def upsert_evaluations(self, rows: Iterable[EvaluationRow]) -> int:
        """Laskee normalized_score-arvon tietokannan benchmark-rajoista ja upserttaa.

        is_outlier-lippua ei ylikirjoiteta: se on IRT-ajon tulos, ei lähdedataa.
        """
        rows = list(rows)
        if not rows:
            return 0
        ranges = {
            bid: (lo, hi)
            for bid, lo, hi in self._run("SELECT benchmark_id, raw_min, raw_max FROM benchmarks")
        }
        unknown = sorted({r.benchmark_id for r in rows} - ranges.keys())
        if unknown:
            raise ValueError(f"Tuntemattomat benchmark_id:t (lisää benchmarks.yaml:iin): {unknown}")
        df = pl.DataFrame(
            [
                {
                    "eval_id": r.eval_id,
                    **r.model_dump(),
                    "normalized_score": normalize_score(r.raw_score, *ranges[r.benchmark_id]),
                }
                for r in rows
            ]
        )
        return self._upsert("evaluations", df, ["eval_id"])

    # --- Ingest-loki ----------------------------------------------------------

    def start_ingest_run(self, run_id: str, source_name: str) -> None:
        self._run(
            "INSERT INTO ingest_runs (run_id, source_name, started_at, status) "
            "VALUES (?, ?, ?, 'running')",
            [run_id, source_name, datetime.now()],
        )

    def finish_ingest_run(
        self,
        run_id: str,
        *,
        status: str,
        rows_fetched: int = 0,
        rows_upserted: int = 0,
        rows_quarantined: int = 0,
        message: str | None = None,
    ) -> None:
        if status not in {"ok", "failed"}:
            raise ValueError(f"Tuntematon status: {status}")
        self._run(
            "UPDATE ingest_runs SET finished_at = ?, status = ?, rows_fetched = ?, "
            "rows_upserted = ?, rows_quarantined = ?, message = ? WHERE run_id = ?",
            [
                datetime.now(),
                status,
                rows_fetched,
                rows_upserted,
                rows_quarantined,
                message,
                run_id,
            ],
        )

    def quarantine(self, run_id: str, source_name: str, record: dict, reason: str) -> None:
        self._run(
            "INSERT INTO ingest_quarantine (run_id, source_name, record_json, reason) "
            "VALUES (?, ?, ?, ?)",
            [run_id, source_name, json.dumps(record, default=str, ensure_ascii=False), reason],
        )

    # --- Kyselyt ----------------------------------------------------------------

    def resolve_aliases(
        self, source_name: str, names: Iterable[str]
    ) -> tuple[dict[str, str], list[str]]:
        """Palauttaa (alias → model_id, tuntemattomat nimet). Tuntemattomia ei hylätä hiljaa."""
        names = sorted(set(names))
        if not names:
            return {}, []
        found = dict(
            self._run(
                "SELECT alias, model_id FROM model_aliases "
                "WHERE source_name = ? AND alias IN (SELECT unnest(?))",
                [source_name, names],
            )
        )
        return found, [n for n in names if n not in found]

    def get_observation_matrix(
        self, domain: str | None = None, *, exclude_outliers: bool = True
    ) -> pl.DataFrame:
        """IRT:n syöte: yksi rivi per (malli, benchmark).

        Aggregointi (A9): versioiden paras tulos lähteen sisällä, sitten lähteiden keskiarvo.
        Sarakkeet: model_id, benchmark_id, y, weight, guessing_floor, n_sources.
        """
        where = ["TRUE"]
        params: list = []
        if exclude_outliers:
            where.append("NOT e.is_outlier")
        if domain is not None and domain != "overall":
            where.append("b.domain = ?")
            params.append(domain)
        return self._pl(
            f"""
            WITH per_source AS (
                SELECT e.model_id, e.benchmark_id, e.source_name,
                       max(e.normalized_score) AS y
                FROM evaluations e JOIN benchmarks b USING (benchmark_id)
                WHERE {" AND ".join(where)}
                GROUP BY ALL
            )
            SELECT p.model_id, p.benchmark_id, avg(p.y) AS y,
                   any_value(b.weight) AS weight,
                   any_value(b.guessing_floor) AS guessing_floor,
                   count(*) AS n_sources
            FROM per_source p JOIN benchmarks b USING (benchmark_id)
            GROUP BY p.model_id, p.benchmark_id
            ORDER BY p.model_id, p.benchmark_id
            """,
            params,
        )

    def set_outliers(self, pairs: Sequence[tuple[str, str]]) -> int:
        """Nollaa kaikki poikkeamaliput ja liputtaa annetut (model_id, benchmark_id) -parit
        (kaikki niiden versiot ja lähteet). Palauttaa liputettujen rivien määrän."""
        self._run("UPDATE evaluations SET is_outlier = FALSE WHERE is_outlier")
        if not pairs:
            return 0
        df = pl.DataFrame(list(pairs), schema=["model_id", "benchmark_id"], orient="row")
        self.conn.register("_outliers", df.to_arrow())
        try:
            self._run(
                "UPDATE evaluations SET is_outlier = TRUE FROM _outliers o "
                "WHERE evaluations.model_id = o.model_id "
                "AND evaluations.benchmark_id = o.benchmark_id"
            )
        finally:
            self.conn.unregister("_outliers")
        return self._one("SELECT count(*) FROM evaluations WHERE is_outlier")[0]

    def latest_run_id(self, domain: str = "overall") -> str | None:
        row = self._one(
            "SELECT run_id FROM irt_runs WHERE domain = ? ORDER BY created_at DESC, run_id DESC "
            "LIMIT 1",
            [domain],
        )
        return row[0] if row else None

    def get_pareto_frontier(self, run_id: str, domain: str = "overall") -> pl.DataFrame:
        """Määrittely 5.1: mallit, joiden θ ylittää kaikkien aiemmin julkaistujen mallien θ:n.

        Saman päivän julkaisuista rintamaan voi päästä vain päivän paras (A11).
        """
        return self._pl(
            """
            WITH s AS (
                SELECT c.model_id, m.release_date, c.theta, c.theta_se
                FROM capability_scores c JOIN models m USING (model_id)
                WHERE c.run_id = ? AND c.domain = ?
            ),
            w AS (
                SELECT *,
                    max(theta) OVER (
                        ORDER BY release_date
                        RANGE BETWEEN UNBOUNDED PRECEDING AND INTERVAL 1 DAY PRECEDING
                    ) AS prev_max,
                    max(theta) OVER (PARTITION BY release_date) AS day_max
                FROM s
            )
            SELECT model_id, release_date, theta, theta_se, prev_max
            FROM w
            WHERE (prev_max IS NULL OR theta > prev_max) AND theta = day_max
            ORDER BY release_date, model_id
            """,
            [run_id, domain],
        )

    def get_price_at(self, model_id: str, at: date) -> dict | None:
        """Viimeisin hinta, joka on voimassa päivänä `at`. Saman päivän lähteistä halvin."""
        row = self._one(
            """
            SELECT model_id, valid_from, source_name,
                   input_cost_per_1m, output_cost_per_1m, blended_cost_per_1m
            FROM model_prices
            WHERE model_id = ? AND valid_from <= ?
            ORDER BY valid_from DESC, blended_cost_per_1m ASC
            LIMIT 1
            """,
            [model_id, at],
        )
        if row is None:
            return None
        cols = ["model_id", "valid_from", "source_name", "input", "output", "blended"]
        return dict(zip(cols, row, strict=True))

    def table_counts(self) -> dict[str, int]:
        tables = [
            r[0]
            for r in self._run(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            )
        ]
        return {t: self._one(f"SELECT count(*) FROM {t}")[0] for t in tables}
