"""Epoch AI Benchmarking Hub -datan tuonti (CC BY 4.0).

Lähde: https://epoch.ai/data/benchmark_data.zip
- benchmark_metadata.csv → benchmarkit (raja-arvot, A8) + epoch_benchmarks.yaml (domain ym.)
- model_metadata.csv     → mallit (malliryhmä = model_id, A9) ja aliakset (versio → malli)
- <benchmark>.csv        → tulokset versiotasolla

Pisteet tuodaan RAAKOINA (ei Epochin valmiiksi korjattua performance-saraketta, A8).
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from forecaster.config import BenchmarkConfig, EpochBenchmarksFile, Paths, load_epoch_benchmarks
from forecaster.db.models import AliasRow, EvaluationRow, ModelRow
from forecaster.db.repository import Repository
from forecaster.ingest.base import IngestReport, download, manual_aliases, run_ingest, slugify

SOURCE = "epoch_ai"
URL = "https://epoch.ai/data/benchmark_data.zip"
SOURCE_URL = "https://epoch.ai/benchmarks"


@dataclass(frozen=True)
class FileSpec:
    benchmark_id: str
    source_file: str
    score_column: str
    scale: float


# --- Haku -------------------------------------------------------------------


def fetch(paths: Paths, as_of: date, client: httpx.Client | None = None) -> Path:
    """Lataa ZIPin data/raw/epoch_ai/<pvm>/ -kansioon ja purkaa sen. Palauttaa puretun kansion."""
    day_dir = paths.raw_dir / SOURCE / as_of.isoformat()
    archive = day_dir / "benchmark_data.zip"
    if not archive.is_file():
        download(URL, archive, client=client)
    return extract(archive, day_dir / "extracted")


def extract(archive: Path, target: Path) -> Path:
    target = target.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            dest = (target / member).resolve()
            if not dest.is_relative_to(target):
                raise ValueError(f"Vaarallinen polku ZIPissä: {member}")
        zf.extractall(target)
    return target


# --- Muunnokset ----------------------------------------------------------------


def _str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def build_benchmarks(
    meta: pl.DataFrame, mapping: EpochBenchmarksFile
) -> tuple[list[BenchmarkConfig], list[FileSpec], list[str], list[str]]:
    """Palauttaa (benchmarkit, tiedostot, kuvaamattomat ECI-benchmarkit, huomiot)."""
    configs, specs, unmapped, notes = [], [], [], []
    seen = set()
    for row in meta.iter_rows(named=True):
        name = row["benchmark"]
        seen.add(name)
        m = mapping.benchmarks.get(name)
        if m is None:
            if str(row.get("in_eci")).lower() == "true":
                unmapped.append(name)
            continue
        if not m.include:
            continue
        source_file = m.source_file or _str(row.get("source_file"))
        score_column = m.score_column or _str(row.get("score_column"))
        scale = m.scale or float(row.get("scale") or 1.0)
        if not source_file or not score_column:
            unmapped.append(f"{name} (lähdetiedosto/sarake puuttuu metatiedoista)")
            continue
        ceiling = float(row.get("score_ceiling") or 1.0)
        baseline = float(row.get("random_baseline") or 0.0)
        configs.append(
            BenchmarkConfig(
                benchmark_id=m.benchmark_id,
                name=name,
                domain=m.domain,
                metric_type="accuracy",
                raw_min=0.0,
                raw_max=ceiling,
                guessing_floor=baseline / ceiling,  # normalisoidulla asteikolla
                contamination_risk=m.contamination_risk,
                weight=m.weight,
                release_date=_str(row.get("release_date")),
            )
        )
        specs.append(FileSpec(m.benchmark_id, source_file, score_column, scale))
    missing = sorted(set(mapping.benchmarks) - seen)
    if missing:
        notes.append(f"epoch_benchmarks.yaml:n nimiä ei löydy Epochin metatiedoista: {missing}")
    return configs, specs, unmapped, notes


def build_models(
    model_meta: pl.DataFrame,
) -> tuple[list[ModelRow], dict[str, str], list[dict], list[str]]:
    """Malliryhmät → ModelRow. Palauttaa (mallit, versio → model_id, karanteeni, huomiot)."""
    mm = model_meta.filter(
        pl.col("model_version").is_not_null() & pl.col("model_group").is_not_null()
    )
    grouped = (
        mm.with_columns(pl.col("date").str.to_date(strict=False))
        .group_by("model_group")
        .agg(
            pl.col("date").min().alias("release_date"),
            pl.col("organization").drop_nulls().first().alias("provider"),
            pl.col("accessibility").drop_nulls().alias("access"),
            pl.col("training_compute_flop").cast(pl.Float64, strict=False).max().alias("flops"),
            pl.col("model_version").alias("versions"),
            pl.col("date").n_unique().alias("n_dates"),
        )
        .sort("model_group")
    )
    models, version_map, quarantine, notes = [], {}, [], []
    used: set[str] = set()
    multi_date = 0
    for g in grouped.iter_rows(named=True):
        base = slugify(g["model_group"]) or "model"
        model_id, k = base, 2
        while model_id in used:
            model_id, k = f"{base}-{k}", k + 1
        if model_id != base:
            notes.append(f"Tunnistetörmäys: '{g['model_group']}' → {model_id}")
        used.add(model_id)
        if g["release_date"] is None:
            quarantine.append(
                {"model_group": g["model_group"], "versions": g["versions"],
                 "reason": "release_date puuttuu kaikilta versioilta"}
            )  # fmt: skip
            continue
        multi_date += g["n_dates"] > 1
        flops = g["flops"]
        models.append(
            ModelRow(
                model_id=model_id,
                display_name=g["model_group"],
                provider=g["provider"] or "unknown",
                release_date=g["release_date"],
                is_open_weights=any("open weights" in a.lower() for a in g["access"]),
                estimated_compute_flops=flops if flops and flops > 0 else None,
            )
        )
        for v in g["versions"]:
            version_map[v] = model_id
    if multi_date:
        notes.append(f"{multi_date} mallilla useita julkaisupäiviä – käytetty aikaisinta")
    return models, version_map, quarantine, notes


def build_evaluations(
    extracted: Path, specs: list[FileSpec], version_map: dict[str, str]
) -> tuple[list[EvaluationRow], set[str], int, int, list[str]]:
    """Palauttaa (tulokset, tuntemattomat versiot, haetut rivit, ohitetut rivit, huomiot)."""
    rows: list[EvaluationRow] = []
    unknown: set[str] = set()
    fetched = skipped = 0
    notes = []
    for spec in specs:
        path = extracted / spec.source_file
        if not path.is_file():
            notes.append(f"{spec.benchmark_id}: tiedostoa {spec.source_file} ei ole ZIPissä")
            continue
        df = pl.read_csv(path, infer_schema_length=0)
        if spec.score_column not in df.columns or "Model version" not in df.columns:
            notes.append(f"{spec.benchmark_id}: sarake '{spec.score_column}' puuttuu")
            continue
        fetched += df.height
        url_col = next((c for c in ("Source link", "Log viewer") if c in df.columns), None)
        date_col = next((c for c in ("Started at", "Date of evaluation") if c in df.columns), None)
        df = df.select(
            pl.col("Model version").str.strip_chars().alias("version"),
            (pl.col(spec.score_column).cast(pl.Float64, strict=False) * spec.scale).alias("score"),
            (pl.col(url_col) if url_col else pl.lit(None, pl.String)).alias("url"),
            (
                pl.col(date_col).str.slice(0, 10).str.to_date(strict=False)
                if date_col
                else pl.lit(None, pl.Date)
            ).alias("eval_date"),
        )
        valid = df.filter(pl.col("version").is_not_null() & pl.col("score").is_not_null())
        skipped += df.height - valid.height
        # Sama versio useammin (eri lähteet/ajot) → paras tulos, kuten Epoch
        best = valid.sort("score", descending=True).unique("version", keep="first")
        skipped += valid.height - best.height
        for v, score, url, eval_date in best.iter_rows():
            model_id = version_map.get(v)
            if model_id is None:
                unknown.add(v)
                continue
            rows.append(
                EvaluationRow(
                    model_id=model_id,
                    benchmark_id=spec.benchmark_id,
                    raw_score=score,
                    source_name=SOURCE,
                    model_version=v,
                    eval_date=eval_date,
                    source_url=url or SOURCE_URL,
                )
            )
    return rows, unknown, fetched, skipped, notes


# --- Ajo ----------------------------------------------------------------------


def ingest(
    repo: Repository,
    paths: Paths,
    extracted: Path,
    *,
    mapping: EpochBenchmarksFile | None = None,
) -> IngestReport:
    mapping = mapping or load_epoch_benchmarks(paths)

    def work(run_id: str, report: IngestReport) -> None:
        meta = pl.read_csv(extracted / "benchmark_metadata.csv", infer_schema_length=0)
        model_meta = pl.read_csv(extracted / "model_metadata.csv", infer_schema_length=0)

        benchmarks, specs, unmapped, notes_b = build_benchmarks(meta, mapping)
        models, version_map, quarantine, notes_m = build_models(model_meta)
        evals, unknown, fetched, skipped, notes_e = build_evaluations(extracted, specs, version_map)

        existing = repo.existing_ids("models", "model_id")
        aliases = {
            (SOURCE, v): AliasRow(source_name=SOURCE, alias=v, model_id=m)
            for v, m in version_map.items()
        }
        for a in manual_aliases(paths, SOURCE):  # käsin määritellyt ohittavat
            aliases[(a.source_name, a.alias)] = a

        with repo.transaction():
            repo.upsert_benchmarks(benchmarks)
            repo.upsert_models(models)
            repo.upsert_aliases(aliases.values())
            for q in quarantine:
                repo.quarantine(run_id, SOURCE, q, q["reason"])
            report.rows_upserted = repo.upsert_evaluations(evals)

        # Karanteeniin joutuneiden mallien versiot eivät ole "tuntemattomia" vaan ohitettuja
        q_versions = {v for q in quarantine for v in q["versions"]}
        skipped_q = len(unknown & q_versions)
        if skipped_q:
            notes_e.append(f"{skipped_q} karanteeniin joutuneen mallin versiota ohitettu")

        report.rows_fetched = fetched
        report.rows_skipped = skipped + skipped_q
        report.rows_quarantined = len(quarantine)
        report.new_models = len({m.model_id for m in models} - existing)
        report.unknown_aliases = sorted(unknown - q_versions)
        report.unmapped_benchmarks = unmapped
        report.notes = (
            notes_b
            + notes_m
            + notes_e
            + [f"{len(benchmarks)} benchmarkia, {len(models)} mallia, {len(aliases)} aliasta"]
        )

    return run_ingest(repo, SOURCE, work)
