"""DuckDB-yhteys, skeeman alustus ja kaikki SQL-kyselyt.

Muut moduulit eivät kirjoita SQL:ää itse, vaan käyttävät tätä luokkaa.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime  # noqa: F401  (date käytössä tyyppivihjeissä)
from importlib.resources import files
from pathlib import Path

import duckdb
import polars as pl

from forecaster.config import BenchmarkConfig
from forecaster.db.models import AliasRow, EvaluationRow, ModelRow, PriceRow

SCHEMA_VERSION = 3
# Versiot, joista päivitys onnistuu ajamalla schema.sql uudelleen (vain uusia tauluja,
# kaikki IF NOT EXISTS). 1 → 2: ingest_unknown_names. 2 → 3: pipeline_runs.
ADDITIVE_MIGRATIONS_FROM = {1, 2}
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
    def connect(
        cls, path: Path | str = ":memory:", *, read_only: bool = False, wait_seconds: float = 0.0
    ) -> Repository:
        """wait_seconds > 0: yritä uudelleen, jos toinen prosessi pitää kantaa auki.
        DuckDB-tiedoston lukko on prosessien välillä poissulkeva myös lukutilassa (Windows),
        joten esim. ajastettu päivitys odottaa dashboardin lyhyiden lukujen ohi."""
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + wait_seconds
        delay = 0.2
        while True:
            try:
                return cls(duckdb.connect(str(path), read_only=read_only))
            except duckdb.IOException:
                if time.monotonic() + delay > deadline:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 5.0)

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
        if (
            current is not None
            and current < SCHEMA_VERSION
            and (current not in ADDITIVE_MIGRATIONS_FROM)
        ):
            raise SchemaVersionError(
                f"Tietokanta on versiossa {current}, koodi {SCHEMA_VERSION}: migraatio puuttuu"
            )
        sql = files("forecaster.db").joinpath("schema.sql").read_text(encoding="utf-8")
        self._run(sql)
        if current != SCHEMA_VERSION:
            self._run("INSERT INTO schema_version (version) VALUES (?)", [SCHEMA_VERSION])
        return SCHEMA_VERSION

    def has_table(self, name: str) -> bool:
        return bool(
            self._one(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
            )[0]
        )

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

    def save_unknown_names(
        self, run_id: str, source_name: str, kind: str, names: Iterable[str]
    ) -> None:
        names = sorted(set(names))
        if not names:
            return
        df = pl.DataFrame({"run_id": run_id, "source_name": source_name, "kind": kind,
                           "name": names})  # fmt: skip
        self._upsert("ingest_unknown_names", df, ["run_id", "kind", "name"])

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

    def get_evaluation_keys(self) -> pl.DataFrame:
        """Tulosten tunnisteet ajankohtaan sidottua dataa varten (6.2): eval_id, model_id,
        benchmark_id, model_version, source_name, eval_date, release_date (mallin)."""
        return self._pl(
            "SELECT e.eval_id, e.model_id, e.benchmark_id, e.model_version, e.source_name, "
            "e.eval_date, m.release_date FROM evaluations e JOIN models m USING (model_id) "
            "ORDER BY e.eval_id"
        )

    def get_observation_matrix(
        self,
        domain: str | None = None,
        *,
        exclude_outliers: bool = True,
        eval_ids: Sequence[str] | None = None,
    ) -> pl.DataFrame:
        """IRT:n syöte: yksi rivi per (malli, benchmark).

        Aggregointi (A9): versioiden paras tulos lähteen sisällä, sitten lähteiden keskiarvo.
        Sarakkeet: model_id, benchmark_id, y, weight, guessing_floor, n_sources.
        eval_ids: vain nämä tulokset (ajankohtaan sidottu data, 6.2).
        """
        where = ["TRUE"]
        params: list = []
        if eval_ids is not None:
            where.append("list_contains(?, e.eval_id)")
            params.append(list(eval_ids))
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

    def rebuild_price_backfill(self) -> int:
        """Rakentaa lähteen `openrouter_backfill` uudelleen: malleille, joilla on OpenRouter-hinta
        mutta ei llm_prices-historiaa, varhaisin OpenRouter-hinta voimaan julkaisupäivästä.
        Palauttaa täydennettyjen mallien määrän."""
        self._run("DELETE FROM model_prices WHERE source_name = 'openrouter_backfill'")
        self._run(
            """
            INSERT INTO model_prices (model_id, valid_from, source_name, input_cost_per_1m,
                                      output_cost_per_1m, blended_cost_per_1m)
            SELECT p.model_id, m.release_date, 'openrouter_backfill',
                   arg_min(p.input_cost_per_1m, p.valid_from),
                   arg_min(p.output_cost_per_1m, p.valid_from),
                   arg_min(p.blended_cost_per_1m, p.valid_from)
            FROM model_prices p JOIN models m USING (model_id)
            WHERE p.source_name = 'openrouter'
              AND p.model_id NOT IN (SELECT model_id FROM model_prices
                                     WHERE source_name IN ('llm_prices', 'manual'))
              AND m.release_date < (SELECT min(valid_from) FROM model_prices q
                                    WHERE q.model_id = p.model_id AND q.source_name = 'openrouter')
            GROUP BY p.model_id, m.release_date
            """
        )
        return self._one(
            "SELECT count(*) FROM model_prices WHERE source_name = 'openrouter_backfill'"
        )[0]

    def get_capability_with_dates(self, run_id: str, domain: str = "overall") -> pl.DataFrame:
        """model_id, release_date, theta, theta_se – ennusteiden syöte."""
        return self._pl(
            """
            SELECT c.model_id, m.release_date, c.theta, coalesce(c.theta_se, 0.0) AS theta_se
            FROM capability_scores c JOIN models m USING (model_id)
            WHERE c.run_id = ? AND c.domain = ?
            ORDER BY m.release_date, c.model_id
            """,
            [run_id, domain],
        )

    def get_price_at_dates(self, dates: Sequence[date]) -> pl.DataFrame:
        """Kaikkien mallien voimassa oleva blended-hinta annettuina päivinä (ASOF).
        Saman päivän lähteistä halvin. Sarakkeet: at, model_id, blended."""
        if not dates:
            return pl.DataFrame(schema={"at": pl.Date, "model_id": pl.String,
                                        "blended": pl.Float64})  # fmt: skip
        self.conn.register("_dates", pl.DataFrame({"at": list(dates)}).to_arrow())
        try:
            return self._pl(
                """
                WITH p AS (
                    SELECT model_id, valid_from, min(blended_cost_per_1m) AS blended
                    FROM model_prices GROUP BY ALL
                ),
                grid AS (SELECT d.at, m.model_id FROM _dates d CROSS JOIN
                         (SELECT DISTINCT model_id FROM p) m)
                SELECT g.at, g.model_id, p.blended
                FROM grid g ASOF JOIN p ON g.model_id = p.model_id AND g.at >= p.valid_from
                ORDER BY g.at, g.model_id
                """
            )
        finally:
            self.conn.unregister("_dates")

    def save_forecast(
        self, run_id: str, irt_run_id: str, kind: str, params: dict, result: dict
    ) -> None:
        self._run(
            "INSERT INTO forecast_runs (run_id, irt_run_id, kind, params_json, result_json) "
            "VALUES (?, ?, ?, ?, ?)",
            [run_id, irt_run_id, kind, json.dumps(params, default=str),
             json.dumps(result, default=str)],
        )  # fmt: skip

    # --- Dashboard (K6) ------------------------------------------------------------

    def latest_runs_by_domain(self) -> dict[str, str]:
        """domain → uusimman IRT-ajon run_id."""
        rows = self._run(
            "SELECT domain, arg_max(run_id, created_at) FROM irt_runs GROUP BY domain ORDER BY 1"
        )
        return dict(rows)

    def get_run_info(self, run_id: str) -> dict | None:
        row = self._one(
            "SELECT run_id, created_at, domain, anchor_mode, n_models, n_benchmarks, "
            "n_observations, converged, params_json FROM irt_runs WHERE run_id = ?",
            [run_id],
        )
        if row is None:
            return None
        cols = ["run_id", "created_at", "domain", "anchor_mode", "n_models", "n_benchmarks",
                "n_observations", "converged", "params_json"]  # fmt: skip
        return dict(zip(cols, row, strict=True))

    def get_models_overview(self, run_id: str, domain: str = "overall") -> pl.DataFrame:
        """Mallit ajossa: nimi, tarjoaja, julkaisu, θ, SE, havaintojen määrä."""
        return self._pl(
            """
            SELECT c.model_id, m.display_name, m.provider, m.release_date,
                   m.is_open_weights, m.is_anchor_model, c.theta, c.theta_se,
                   (SELECT count(DISTINCT e.benchmark_id) FROM evaluations e
                    WHERE e.model_id = c.model_id) AS n_benchmarks
            FROM capability_scores c JOIN models m USING (model_id)
            WHERE c.run_id = ? AND c.domain = ?
            ORDER BY c.theta DESC
            """,
            [run_id, domain],
        )

    def get_benchmark_params(self, run_id: str) -> pl.DataFrame:
        """IRT-parametrit + metatiedot + kattavuus (mallien määrä) + liputetut poikkeamat."""
        return self._pl(
            """
            WITH cov AS (
                SELECT benchmark_id, count(DISTINCT model_id) AS n_models,
                       count(DISTINCT model_id) FILTER (WHERE is_outlier) AS n_outliers
                FROM evaluations GROUP BY benchmark_id
            )
            SELECT p.benchmark_id, b.name, b.domain, b.guessing_floor, b.weight,
                   b.contamination_risk, b.release_date, p.difficulty_b, p.discrimination_a,
                   coalesce(cov.n_models, 0) AS n_models,
                   coalesce(cov.n_outliers, 0) AS n_outliers
            FROM benchmark_irt_params p JOIN benchmarks b USING (benchmark_id)
            LEFT JOIN cov USING (benchmark_id)
            WHERE p.run_id = ?
            ORDER BY p.difficulty_b
            """,
            [run_id],
        )

    def get_outliers(self) -> pl.DataFrame:
        """Sovituksesta poistetut (model_id, benchmark_id) -parit ja niiden paras tulos."""
        return self._pl(
            """
            SELECT model_id, benchmark_id, max(normalized_score) AS y
            FROM evaluations WHERE is_outlier
            GROUP BY ALL ORDER BY benchmark_id, model_id
            """
        )

    def get_ingest_runs(self, limit: int = 50) -> pl.DataFrame:
        return self._pl(
            "SELECT run_id, source_name, started_at, finished_at, status, rows_fetched, "
            "rows_upserted, rows_quarantined, message FROM ingest_runs "
            "ORDER BY started_at DESC LIMIT ?",
            [limit],
        )

    def get_quarantine(self, limit: int = 200) -> pl.DataFrame:
        return self._pl(
            "SELECT run_id, source_name, reason, record_json, created_at FROM ingest_quarantine "
            "ORDER BY created_at DESC LIMIT ?",
            [limit],
        )

    def get_models_with_price_status(self, run_id: str) -> pl.DataFrame:
        """Ajon θ-mallit metatietoineen ja tieto siitä, onko mallilla mitään hintaa
        (aliasten kuratointi: ehdokkaat ensisijaisesti malleille, joilta hinta puuttuu)."""
        return self._pl(
            """
            SELECT m.model_id, m.display_name, m.provider, m.release_date, c.theta,
                   c.theta_se,
                   EXISTS (SELECT 1 FROM model_prices p WHERE p.model_id = m.model_id)
                       AS has_price
            FROM capability_scores c JOIN models m USING (model_id)
            WHERE c.run_id = ? AND c.domain = 'overall'
            ORDER BY m.release_date DESC, m.model_id
            """,
            [run_id],
        )

    def get_unknown_names(self) -> pl.DataFrame:
        """Kunkin lähteen UUSIMMAN onnistuneen ajon tuntemattomat nimet."""
        if not self.has_table("ingest_unknown_names"):
            return pl.DataFrame(schema={"source_name": pl.String, "kind": pl.String,
                                        "name": pl.String, "run_id": pl.String})  # fmt: skip
        return self._pl(
            """
            WITH latest AS (
                SELECT source_name, arg_max(run_id, started_at) AS run_id
                FROM ingest_runs WHERE status = 'ok' GROUP BY source_name
            )
            SELECT u.source_name, u.kind, u.name, u.run_id
            FROM ingest_unknown_names u JOIN latest USING (source_name, run_id)
            ORDER BY u.source_name, u.kind, u.name
            """
        )

    def get_capability_forecast_history(self) -> list[dict]:
        """Kaikki tallennetut kyvykkyysennusteet vanhimmasta uusimpaan (A30):
        run_id, irt_run_id, created_at, result (dict)."""
        rows = self._run(
            "SELECT run_id, irt_run_id, created_at, result_json FROM forecast_runs "
            "WHERE kind = 'capability' ORDER BY created_at, run_id"
        )
        return [
            {"run_id": r, "irt_run_id": i, "created_at": c, "result": json.loads(res)}
            for r, i, c, res in rows
        ]

    def get_latest_forecasts(self) -> dict[str, dict]:
        """Uusimman ennusteajon tulokset: run_id-pääte (capability, cost:<taso>, …) → dict."""
        row = self._one(
            "SELECT split_part(run_id, ':', 1) FROM forecast_runs "
            "ORDER BY created_at DESC, run_id DESC LIMIT 1"
        )
        if row is None:
            return {}
        rows = self._run(
            "SELECT run_id, result_json FROM forecast_runs WHERE run_id LIKE ? ORDER BY run_id",
            [row[0] + ":%"],
        )
        return {rid.split(":", 1)[1]: json.loads(res) for rid, res in rows}

    # --- Päivitysajot (K7) --------------------------------------------------------------

    def get_benchmark_domains(self) -> pl.DataFrame:
        """benchmark_id, name, domain (validointiraportin osa-aluejaottelu, 6.3)."""
        return self._pl("SELECT benchmark_id, name, domain FROM benchmarks ORDER BY benchmark_id")

    def list_domains(self) -> list[str]:
        return [r[0] for r in self._run("SELECT DISTINCT domain FROM benchmarks ORDER BY 1")]

    def latest_irt_converged(self, domain: str = "overall") -> bool | None:
        row = self._one(
            "SELECT converged FROM irt_runs WHERE domain = ? "
            "ORDER BY created_at DESC, run_id DESC LIMIT 1",
            [domain],
        )
        return None if row is None else bool(row[0])

    def unknown_name_set(self) -> set[tuple[str, str, str]]:
        """(lähde, tyyppi, nimi) kunkin lähteen uusimmasta onnistuneesta ajosta."""
        return set(self.get_unknown_names().select("source_name", "kind", "name").iter_rows())

    def save_pipeline_run(
        self,
        run_id: str,
        started_at: datetime,
        finished_at: datetime | None,
        status: str,
        steps: list[dict],
        alerts: list[dict],
    ) -> None:
        if status not in {"running", "ok", "warning", "failed"}:
            raise ValueError(f"Tuntematon status: {status}")
        self._run(
            """
            INSERT INTO pipeline_runs (run_id, started_at, finished_at, status, steps_json,
                                       alerts_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id) DO UPDATE SET finished_at = excluded.finished_at,
                status = excluded.status, steps_json = excluded.steps_json,
                alerts_json = excluded.alerts_json
            """,
            [run_id, started_at, finished_at, status,
             json.dumps(steps, default=str, ensure_ascii=False),
             json.dumps(alerts, default=str, ensure_ascii=False)],
        )  # fmt: skip

    def get_pipeline_runs(self, limit: int = 20) -> pl.DataFrame:
        if not self.has_table("pipeline_runs"):
            return pl.DataFrame(
                schema={"run_id": pl.String, "started_at": pl.Datetime, "finished_at": pl.Datetime,
                        "status": pl.String, "steps_json": pl.String, "alerts_json": pl.String}
            )  # fmt: skip
        return self._pl(
            "SELECT run_id, started_at, finished_at, status, steps_json, alerts_json "
            "FROM pipeline_runs ORDER BY started_at DESC LIMIT ?",
            [limit],
        )

    def table_counts(self) -> dict[str, int]:
        tables = [
            r[0]
            for r in self._run(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            )
        ]
        return {t: self._one(f"SELECT count(*) FROM {t}")[0] for t in tables}
