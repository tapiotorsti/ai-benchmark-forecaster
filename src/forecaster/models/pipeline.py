"""K4: tuotannon sovitusputki.

overall:  karsinta → sovitus → poikkeamien tunnistus → (high-poikkeamat pois) → karsinta
          → uudelleensovitus → liput + ajo tallennetaan YHDESSÄ transaktiossa.
domain:   overall-ajon benchmark-parametrit (a, b) kiinnitetään ja vain θ estimoidaan
          (kiinteät osioparametrit, A12). θ on suoraan overall-asteikolla ilman linkitys-
          regressiota, joka kutistaisi asteikkoa (math-reviewer K4).

Poikkeamapolitiikka (A4, math-reviewer K4):
- liputetaan molemmat suunnat (raportti), mutta sovituksesta poistetaan vain `high`
  (epäilyttävän hyvä = kontaminaatio, määrittely 4.3). `low` voi olla aito heikkous
  yksiulotteisen mallin ulkopuolella; sen poisto nostaisi θ:aa systemaattisesti.
- enintään yksi poisto per malli, eikä koskaan niin, että malli putoaisi karsintarajan alle.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import polars as pl

from forecaster.db.repository import Repository
from forecaster.models.irt_engine import IRTConfig, IRTResult, fit_irt, store_result
from forecaster.models.quality import detect_outliers, prune_sparse


@dataclass(frozen=True)
class PipelineConfig:
    min_model_obs: int = 3
    min_benchmark_obs: int = 5
    detect_outliers: bool = True
    outlier_threshold: float = 3.0  # σ-ekvivalenttia (robusti z = (r − med) / (1.4826·MAD))
    outlier_min_benchmark_obs: int = 12  # MAD on epävakaa pienillä n (n=8: ~40 % hajonta)


@dataclass
class FitSummary:
    run_id: str
    domain: str
    result: IRTResult
    obs: pl.DataFrame
    dropped_models: list[str]
    dropped_benchmarks: list[str]
    outliers: pl.DataFrame = field(default_factory=pl.DataFrame)  # sarake `excluded`
    fixed_from: str | None = None


def run_fit(
    repo: Repository,
    cfg: IRTConfig,
    domain: str = "overall",
    pcfg: PipelineConfig | None = None,
) -> FitSummary:
    pcfg = pcfg or PipelineConfig()
    if domain == "overall":
        return _run_overall(repo, cfg, pcfg)
    return _run_domain(repo, cfg, domain, pcfg)


def _check_not_empty(obs: pl.DataFrame, pcfg: PipelineConfig) -> None:
    if obs.is_empty():
        raise ValueError(
            "Ei havaintoja karsinnan jälkeen (min_model_obs="
            f"{pcfg.min_model_obs}, min_benchmark_obs={pcfg.min_benchmark_obs})"
        )


def select_exclusions(flagged: pl.DataFrame, obs: pl.DataFrame, min_model_obs: int) -> pl.DataFrame:
    """Lisää sarakkeen `excluded`: vain high, enintään 1 per malli, ei karsintarajan alle."""
    if flagged.is_empty():
        return flagged.with_columns(excluded=pl.lit(False))
    counts = obs.group_by("model_id").len().rename({"len": "_n"})
    ranked = (
        flagged.join(counts, on="model_id", how="left").with_columns(
            _rank=pl.col("robust_z").rank("ordinal", descending=True).over("model_id", "direction")
        )  # fmt: skip
    )
    return ranked.with_columns(
        excluded=(pl.col("direction") == "high")
        & (pl.col("_rank") == 1)
        & (pl.col("_n") - 1 >= min_model_obs)
    ).drop("_n", "_rank")


def _run_overall(repo: Repository, cfg: IRTConfig, pcfg: PipelineConfig) -> FitSummary:
    raw = repo.get_observation_matrix("overall", exclude_outliers=False)
    if raw.is_empty():
        raise ValueError("Ei havaintoja – aja ensin 'forecaster ingest'")
    pr = prune_sparse(raw, pcfg.min_model_obs, pcfg.min_benchmark_obs)
    _check_not_empty(pr.obs, pcfg)
    res = fit_irt(pr.obs, cfg)

    flagged = pl.DataFrame()
    if pcfg.detect_outliers:
        flagged = select_exclusions(
            detect_outliers(pr.obs, res, pcfg.outlier_threshold, pcfg.outlier_min_benchmark_obs),
            pr.obs,
            pcfg.min_model_obs,
        )
    excluded = (
        list(flagged.filter("excluded").select("model_id", "benchmark_id").iter_rows())
        if flagged.height
        else []
    )
    dropped_m, dropped_b, obs = set(pr.dropped_models), set(pr.dropped_benchmarks), pr.obs
    if excluded:
        cleaned = obs.join(
            pl.DataFrame(excluded, schema=["model_id", "benchmark_id"], orient="row"),
            on=["model_id", "benchmark_id"],
            how="anti",
        )
        pr2 = prune_sparse(cleaned, pcfg.min_model_obs, pcfg.min_benchmark_obs)
        dropped_m |= set(pr2.dropped_models)
        dropped_b |= set(pr2.dropped_benchmarks)
        obs = pr2.obs
        res = fit_irt(obs, cfg)  # uudelleensovitus kerran (määrittely 4.3)

    extra = {
        "pipeline": pcfg.__dict__,
        "dropped_models": len(dropped_m),
        "dropped_benchmarks": sorted(dropped_b),
        "n_flagged": flagged.height,
        "n_excluded": len(excluded),
    }
    # Kaikki laskettu → kirjoitus atomisesti (keskeytys ei jätä lippuja ilman ajoa)
    with repo.transaction():
        repo.set_outliers(excluded)
        run_id = store_result(repo, res, obs, "overall", extra)
    return FitSummary(run_id, "overall", res, obs, sorted(dropped_m), sorted(dropped_b), flagged)


def _run_domain(repo: Repository, cfg: IRTConfig, domain: str, pcfg: PipelineConfig) -> FitSummary:
    overall_run = repo.latest_run_id("overall")
    if overall_run is None:
        raise ValueError(f"Domain-ajo '{domain}' vaatii ensin overall-ajon (forecaster fit)")
    raw = repo.get_observation_matrix(domain, exclude_outliers=True)
    if raw.is_empty():
        raise ValueError(f"Ei havaintoja domainille '{domain}'")
    pr = prune_sparse(raw, pcfg.min_model_obs, pcfg.min_benchmark_obs)
    _check_not_empty(pr.obs, pcfg)
    items = {
        b: (float(bb), float(aa))
        for b, bb, aa in repo._run(
            "SELECT benchmark_id, difficulty_b, discrimination_a FROM benchmark_irt_params "
            "WHERE run_id = ?",
            [overall_run],
        )
        if b in set(pr.obs["benchmark_id"])
    }
    if len(items) < 2:
        raise ValueError(
            f"Domainilla '{domain}' alle 2 benchmarkia, joilla on overall-ajon parametrit"
        )
    dcfg = replace(cfg, anchors={}, anchor_mode="fixed", fixed_items=items)
    res = fit_irt(pr.obs, dcfg)
    extra = {
        "pipeline": pcfg.__dict__,
        "fixed_items_from": overall_run,
        "n_fixed_items": len(items),
        "dropped_models": len(pr.dropped_models),
        "dropped_benchmarks": pr.dropped_benchmarks,
    }
    with repo.transaction():
        run_id = store_result(repo, res, pr.obs, domain, extra)
    return FitSummary(run_id, domain, res, pr.obs, pr.dropped_models, pr.dropped_benchmarks,
                      fixed_from=overall_run)  # fmt: skip
