"""Testiapu: synteettinen data tietokantaan (K2/K4-testit)."""

from datetime import date, timedelta

from forecaster.config import BenchmarkConfig
from forecaster.db.models import EvaluationRow, ModelRow
from forecaster.db.repository import Repository
from forecaster.models.synthetic import SyntheticData


def pick_anchors(data: SyntheticData) -> dict[str, float]:
    """Kaksi paljon havaittua mallia θ-jakauman eri päistä, kiinnitettynä todelliseen arvoon."""
    counts = data.obs.group_by("model_id").len()
    well = set(counts.filter(counts["len"] >= counts["len"].quantile(0.5))["model_id"])
    ordered = sorted((data.theta[m], m) for m in well)
    lo, hi = ordered[len(ordered) // 5], ordered[4 * len(ordered) // 5]
    return {lo[1]: lo[0], hi[1]: hi[0]}


def seed_repo(data: SyntheticData, obs=None) -> Repository:
    """Muistinvarainen kanta: benchmarkit vuorotellen coding/math, mallit julkaisujärjestyksessä."""
    obs = data.obs if obs is None else obs
    repo = Repository.connect(":memory:")
    repo.init_db()
    bids = sorted(data.b)
    repo.upsert_benchmarks(
        [
            BenchmarkConfig(benchmark_id=j, name=j, domain="math" if i % 2 else "coding",
                            metric_type="accuracy", raw_min=0, raw_max=1,
                            guessing_floor=data.c[j])
            for i, j in enumerate(bids)
        ]
    )  # fmt: skip
    start = date(2023, 1, 1)
    repo.upsert_models(
        [
            ModelRow(model_id=m, display_name=m, provider="synthetic",
                     release_date=start + timedelta(days=int(1000 * data.release_index[m])))
            for m in sorted(data.theta)
        ]
    )  # fmt: skip
    repo.upsert_evaluations(
        [
            EvaluationRow(model_id=m, benchmark_id=j, raw_score=y, source_name="synthetic")
            for m, j, y in obs.select("model_id", "benchmark_id", "y").iter_rows()
        ]
    )
    return repo
