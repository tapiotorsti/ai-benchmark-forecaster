"""Omien testitulosten tuonti CSV-tiedostosta.

Pakolliset sarakkeet: model_id, benchmark_id, raw_score
Valinnaiset:          model_version, eval_date (YYYY-MM-DD), source_url

model_id voi olla kanoninen tunniste tai alias (source_name = 'internal').
benchmark_id:n pitää olla tietokannassa (config/benchmarks.yaml tai Epoch).
Rivit, joita ei voi tuoda, menevät karanteeniin syyn kanssa.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
from pydantic import ValidationError

from forecaster.config import Paths
from forecaster.db.models import EvaluationRow
from forecaster.db.repository import Repository
from forecaster.ingest.base import IngestReport, manual_aliases, run_ingest

SOURCE = "internal"
REQUIRED = {"model_id", "benchmark_id", "raw_score"}


def ingest(repo: Repository, paths: Paths, csv_path: Path) -> IngestReport:
    def work(run_id: str, report: IngestReport) -> None:
        df = pl.read_csv(csv_path, infer_schema_length=0)
        missing = REQUIRED - set(df.columns)
        if missing:
            raise ValueError(f"{csv_path.name}: pakollisia sarakkeita puuttuu: {sorted(missing)}")
        report.rows_fetched = df.height

        models = repo.existing_ids("models", "model_id")
        benchmarks = repo.existing_ids("benchmarks", "benchmark_id")
        aliases, _ = repo.resolve_aliases(SOURCE, df["model_id"].drop_nulls().to_list())
        aliases |= {a.alias: a.model_id for a in manual_aliases(paths, SOURCE)}

        rows: dict[str, EvaluationRow] = {}
        problems: list[tuple[dict, str]] = []
        for rec in df.iter_rows(named=True):
            mid = rec["model_id"]
            mid = mid if mid in models else aliases.get(mid)
            if mid is None:
                problems.append((rec, "tuntematon model_id"))
                report.unknown_aliases.append(str(rec["model_id"]))
                continue
            if rec["benchmark_id"] not in benchmarks:
                problems.append((rec, "tuntematon benchmark_id"))
                continue
            try:
                row = EvaluationRow(
                    model_id=mid,
                    benchmark_id=rec["benchmark_id"],
                    raw_score=rec["raw_score"],
                    source_name=SOURCE,
                    model_version=rec.get("model_version") or "",
                    eval_date=rec.get("eval_date") or None,
                    source_url=rec.get("source_url") or None,
                )
            except ValidationError as exc:
                problems.append((rec, f"virheellinen rivi: {exc.errors()[0]['msg']}"))
                continue
            rows[row.eval_id] = row  # viimeinen voittaa, jos sama rivi toistuu

        with repo.transaction():
            for rec, reason in problems:
                repo.quarantine(run_id, SOURCE, rec, reason)
            report.rows_upserted = repo.upsert_evaluations(rows.values())
        report.rows_quarantined = len(problems)
        report.unknown_aliases = sorted(set(report.unknown_aliases))

    return run_ingest(repo, SOURCE, work)
