"""K4: datan karsinta, poikkeamien tunnistus ja asteikon laadunvalvonta.

- prune_sparse: liian harvat mallit/benchmarkit pois (toistetaan kunnes vakaa)
- detect_outliers: standardoitu residuaali + benchmark-kohtainen MAD-raja (määrittely 4.3, A4)
- connectivity / bridge_summary: voiko kaikki asettaa samalle asteikolle
- leave_one_benchmark_out: kuinka paljon θ liikkuu, kun yksi testi jätetään pois
- compare_external: järjestyksen vertailu ulkoiseen indeksiin (Epoch ECI, A10)
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

import numpy as np
import polars as pl
from scipy.stats import spearmanr

from forecaster.models.irt_engine import IRTConfig, IRTResult, fit_irt

MAD_TO_SD = 1.4826  # normaalijakaumalla 1.4826·MAD ≈ σ


# --- Karsinta -----------------------------------------------------------------


@dataclass
class PruneResult:
    obs: pl.DataFrame
    dropped_models: list[str]
    dropped_benchmarks: list[str]


def prune_sparse(
    obs: pl.DataFrame, min_model_obs: int = 3, min_benchmark_obs: int = 5
) -> PruneResult:
    """Poistaa mallit, joilla < min_model_obs tulosta, ja benchmarkit, joilla < min_benchmark_obs.

    Toistetaan kunnes vakaa: mallin poisto voi pudottaa benchmarkin rajan alle ja päinvastoin.
    """
    dropped_m: set[str] = set()
    dropped_b: set[str] = set()
    cur = obs
    while True:
        m_counts = cur.group_by("model_id").len()
        b_counts = cur.group_by("benchmark_id").len()
        bad_m = set(m_counts.filter(pl.col("len") < min_model_obs)["model_id"])
        bad_b = set(b_counts.filter(pl.col("len") < min_benchmark_obs)["benchmark_id"])
        if not bad_m and not bad_b:
            break
        dropped_m |= bad_m
        dropped_b |= bad_b
        cur = cur.filter(~pl.col("model_id").is_in(bad_m) & ~pl.col("benchmark_id").is_in(bad_b))
    # Kokonaan kadonneet (toisen akselin poiston takia) ovat myös pudotettuja
    dropped_m |= set(obs["model_id"]) - set(cur["model_id"])
    dropped_b |= set(obs["benchmark_id"]) - set(cur["benchmark_id"])
    return PruneResult(cur, sorted(dropped_m), sorted(dropped_b))


# --- Poikkeamat -----------------------------------------------------------------


def residuals(obs: pl.DataFrame, res: IRTResult) -> pl.DataFrame:
    """Pearson-residuaali r = (y − ŷ) / √(ŷ(1−ŷ)) ja benchmark-kohtainen robusti z.

    Pelkkä |y − ŷ| (määrittelyn kaava) ei ole vertailukelpoinen testien välillä:
    saturoituneella testillä (ŷ ≈ 0.95) virheet ovat luonnostaan pieniä (A4).
    """
    yhat = res.predict(obs)
    df = obs.with_columns(
        yhat=pl.Series(yhat),
        resid=pl.Series((obs["y"].to_numpy() - yhat) / np.sqrt(yhat * (1 - yhat))),
    )
    stats = df.group_by("benchmark_id").agg(
        pl.col("resid").median().alias("_med"),
        (pl.col("resid") - pl.col("resid").median()).abs().median().alias("_mad"),
        pl.len().alias("_n"),
    )
    return (
        df.join(stats, on="benchmark_id")
        .with_columns(
            robust_z=pl.when(pl.col("_mad") > 1e-12)
            .then((pl.col("resid") - pl.col("_med")) / (MAD_TO_SD * pl.col("_mad")))
            .otherwise(0.0)
        )
        .rename({"_n": "bench_n"})
        .drop("_med", "_mad")
    )


def detect_outliers(
    obs: pl.DataFrame, res: IRTResult, threshold: float = 3.0, min_benchmark_obs: int = 8
) -> pl.DataFrame:
    """Havainnot, joiden |robust_z| > threshold. Alle min_benchmark_obs havainnon testeillä
    MAD on liian epävakaa, joten niitä ei liputeta. Palauttaa myös suunnan
    (high = epäilyttävän hyvä, esim. kontaminaatio)."""
    r = residuals(obs, res)
    return (
        r.filter((pl.col("bench_n") >= min_benchmark_obs) & (pl.col("robust_z").abs() > threshold))
        .with_columns(
            direction=pl.when(pl.col("robust_z") > 0).then(pl.lit("high")).otherwise(pl.lit("low"))
        )
        .select("model_id", "benchmark_id", "y", "yhat", "resid", "robust_z", "direction")
        .sort(pl.col("robust_z").abs(), descending=True)
    )


# --- Kytkentä ja siltamallit ------------------------------------------------------


def connectivity(obs: pl.DataFrame) -> list[tuple[int, int]]:
    """Yhtenäiset komponentit kaksijakoisessa malli–benchmark-graafissa: [(mallit, benchmarkit)]."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for m, b in obs.select("model_id", "benchmark_id").iter_rows():
        ra, rb = find("M" + m), find("B" + b)
        if ra != rb:
            parent[ra] = rb
    comps: dict[str, list[str]] = {}
    for node in list(parent):
        comps.setdefault(find(node), []).append(node)
    sizes = [(sum(n[0] == "M" for n in c), sum(n[0] == "B" for n in c)) for c in comps.values()]
    return sorted(sizes, reverse=True)


def bridge_summary(
    obs: pl.DataFrame,
    bench_dates: dict[str, date | None],
    old_before: date = date(2023, 1, 1),
    new_from: date = date(2025, 1, 1),
) -> dict[str, int]:
    era = {
        b: ("old" if d < old_before else "new" if d >= new_from else "mid") if d else "unknown"
        for b, d in bench_dates.items()
    }
    tagged = obs.with_columns(era=pl.col("benchmark_id").replace_strict(era, default="unknown"))
    per = tagged.group_by("model_id").agg(
        (pl.col("era") == "old").sum().alias("o"), (pl.col("era") == "new").sum().alias("n")
    )
    return {
        "bridge_1_1": per.filter((pl.col("o") >= 1) & (pl.col("n") >= 1)).height,
        "bridge_2_2": per.filter((pl.col("o") >= 2) & (pl.col("n") >= 2)).height,
        "only_old": per.filter((pl.col("o") > 0) & (pl.col("n") == 0)).height,
        "only_new": per.filter((pl.col("o") == 0) & (pl.col("n") > 0)).height,
    }


# --- Vakaus ----------------------------------------------------------------------


def leave_one_benchmark_out(
    obs: pl.DataFrame,
    cfg: IRTConfig,
    benchmarks: list[str],
    *,
    full: IRTResult | None = None,
    min_model_obs: int = 3,
    min_benchmark_obs: int = 5,
) -> pl.DataFrame:
    """Jokaiselle benchmarkille: sovitus ilman sitä ja θ-muutos sen testaamilla malleilla.

    Sisäkkäisillä estimaateilla Var(θ_lobo − θ_full) ≈ SE_lobo² − SE_full² (Hausman), joten
    z = Δθ / √(SE_lobo² − SE_full²) ~ N(0,1), jos poisto ei muuta mallia systemaattisesti.
    Tunnusluvut lasketaan VAIN malleille, joilla on tulos poistetusta testistä (muiden θ ei
    juuri liiku ja laimentaisi luvut; math-reviewer K4).

    Sarakkeet: benchmark_id, n_tested, median_z, share_z_within_1_96 (odotus ≈ 0.95),
    median_shift_se (Δθ / SE_full, havainnollinen), max_shift, untested_median_shift_se,
    n_dropped.
    """
    full = full or fit_irt(obs, replace(cfg, compute_se=True))
    t_full, se_full = full.theta_by_model(), full.theta_se_by_model()
    rows = []
    for b in benchmarks:
        tested_all = set(obs.filter(pl.col("benchmark_id") == b)["model_id"])
        pr = prune_sparse(obs.filter(pl.col("benchmark_id") != b), min_model_obs, min_benchmark_obs)
        anchors_left = {m: v for m, v in cfg.anchors.items() if m in set(pr.obs["model_id"])}
        if cfg.anchors and len(anchors_left) < 2:
            rows.append({"benchmark_id": b, "n_tested": 0, "median_z": None,
                         "share_z_within_1_96": None, "median_shift_se": None, "max_shift": None,
                         "untested_median_shift_se": None,
                         "n_dropped": len(pr.dropped_models)})  # fmt: skip
            continue
        r = fit_irt(pr.obs, replace(cfg, compute_se=True))
        t_new, se_new = r.theta_by_model(), r.theta_se_by_model()
        common = [m for m in r.model_ids if m in t_full and se_full[m] > 0]
        tested = [m for m in common if m in tested_all]
        untested = [m for m in common if m not in tested_all]

        def shift_se(ms: list[str], t_new: dict[str, float] = t_new) -> np.ndarray:
            return np.array([abs(t_new[m] - t_full[m]) / se_full[m] for m in ms])

        if tested:
            delta = np.array([abs(t_new[m] - t_full[m]) for m in tested])
            var_diff = np.array([se_new[m] ** 2 - se_full[m] ** 2 for m in tested])
            floor = np.array([(0.1 * se_full[m]) ** 2 for m in tested])
            z = delta / np.sqrt(np.maximum(np.abs(var_diff), floor))
        rows.append(
            {
                "benchmark_id": b,
                "n_tested": len(tested),
                "median_z": float(np.median(z)) if tested else None,
                "share_z_within_1_96": float(np.mean(z < 1.96)) if tested else None,
                "median_shift_se": float(np.median(shift_se(tested))) if tested else None,
                "max_shift": float(delta.max()) if tested else None,
                "untested_median_shift_se": (
                    float(np.median(shift_se(untested))) if untested else None
                ),
                "n_dropped": len(pr.dropped_models),
            }
        )
    return pl.DataFrame(rows).sort("share_z_within_1_96", nulls_last=True)


# --- Ulkoinen vertailu -------------------------------------------------------------


def compare_external(theta: dict[str, float], external: dict[str, float]) -> dict:
    common = sorted(set(theta) & set(external))
    if len(common) < 3:
        return {"n": len(common), "spearman": None}
    rho = spearmanr([theta[m] for m in common], [external[m] for m in common]).statistic
    return {"n": len(common), "spearman": float(rho)}
