"""Dashboardin datanmuokkaus: puhtaita funktioita Polars-tauluille (ei Streamlitia, ei SQL:ää)."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import polars as pl

from forecaster.config import ForecastFile
from forecaster.models.forecaster import (
    ForecastConfig,
    add_months,
    capability_forecast,
    cost_forecast,
    frontier_mask,
    month_starts,
    years_since,
)
from forecaster.models.irt_engine import IRTConfig, IRTResult, fit_irt
from forecaster.models.quality import prune_sparse


def forecast_config(ff: ForecastFile, n_boot: int | None = None) -> ForecastConfig:
    """n_boot None → forecast.yaml:n arvo, jotta dashboard ja raportti antavat samat luvut
    (aiemmin dashboard käytti 300 drawia ja raportti 1000; jatkokehitys 2.9)."""
    return ForecastConfig(
        fit_start=ff.fit_start,
        horizons_months=tuple(ff.horizons_months),
        n_boot=n_boot or ff.n_boot,
        seed=ff.seed,
        piecewise_window_months=ff.piecewise_window_months,
        piecewise_alpha=ff.piecewise_alpha,
        level_floor=ff.level_floor,
        small_sample=ff.small_sample,
        small_sample_max_points=ff.small_sample_max_points,
        fit_windows_months=tuple(ff.fit_windows_months),
    )


def forecast_input(scores: pl.DataFrame, ff: ForecastFile, as_of: date) -> pl.DataFrame:
    """Sama suodatus kuin run_forecasts: julkaistu ≤ as_of ja θ-SE ≤ max_theta_se (A18)."""
    return scores.filter(
        (pl.col("release_date") <= as_of) & (pl.col("theta_se").fill_null(0.0) <= ff.max_theta_se)
    )


def with_frontier(scores: pl.DataFrame) -> pl.DataFrame:
    """Lisää sarakkeen is_frontier (Pareto-rintama, A11) koko datasta."""
    if scores.is_empty():
        return scores.with_columns(is_frontier=pl.lit(False))
    rel = scores["release_date"].to_numpy().astype("datetime64[D]")
    mask = frontier_mask(rel, scores["theta"].to_numpy().astype(float))
    return scores.with_columns(is_frontier=pl.Series(mask))


# --- Yleiskuva ---------------------------------------------------------------------


def forecast_band(
    scores: pl.DataFrame, ff: ForecastFile, as_of: date, n_boot: int | None = None
) -> tuple[pl.DataFrame, dict] | None:
    """Kuukausittainen trendi- ja ennustekaista fit_start → as_of + suurin horisontti.

    Palauttaa (kaista, ennusteen yhteenveto) tai None, jos rintamassa on liian vähän pisteitä
    (esim. pieni domain).
    """
    sc = forecast_input(scores, ff, as_of)
    dates = month_starts(ff.fit_start, add_months(as_of, max(ff.horizons_months)))
    try:
        fc = capability_forecast(sc, forecast_config(ff, n_boot), as_of, eval_dates=dates)
    except ValueError:
        return None
    rows = [
        {
            "date": e["date"],
            "trend_p10": e["trend_p10_p50_p90"][0],
            "trend_p50": e["trend_p10_p50_p90"][1],
            "trend_p90": e["trend_p10_p50_p90"][2],
            "pred_p10": e["prediction_p10_p50_p90"][0],
            "pred_p90": e["prediction_p10_p50_p90"][2],
        }
        for e in fc["eval"]
    ]
    summary = {k: v for k, v in fc.items() if k != "eval"}
    return pl.DataFrame(rows), summary


# --- Mallit ja benchmarkit ------------------------------------------------------------


def predict_scores(theta: float, params: pl.DataFrame) -> np.ndarray:
    """ŷ = c + (1 − c)·σ(a(θ − b)) jokaiselle params-riville (guessing_floor, a, b)."""
    a = params["discrimination_a"].to_numpy()
    b = params["difficulty_b"].to_numpy()
    c = params["guessing_floor"].to_numpy()
    return c + (1 - c) / (1 + np.exp(-a * (theta - b)))


def model_detail(
    model_id: str, theta: float, params: pl.DataFrame, obs: pl.DataFrame, outliers: pl.DataFrame
) -> pl.DataFrame:
    """Benchmarkeittain: havaittu (jos on) ja ennustettu normalisoitu tulos + poikkeamalippu.

    obs: get_observation_matrix(exclude_outliers=False) – sisältää myös poistetut tulokset.
    """
    p = params.select(
        "benchmark_id", "name", "domain", "guessing_floor", "difficulty_b", "discrimination_a"
    )
    p = p.with_columns(y_pred=pl.Series(predict_scores(theta, p)))
    mine = obs.filter(pl.col("model_id") == model_id).select("benchmark_id", y_obs="y")
    flagged = outliers.filter(pl.col("model_id") == model_id).select(
        "benchmark_id", is_outlier=pl.lit(True)
    )
    return (
        p.join(mine, on="benchmark_id", how="left")
        .join(flagged, on="benchmark_id", how="left")
        .with_columns(
            is_outlier=pl.col("is_outlier").fill_null(False),
            residual=pl.col("y_obs") - pl.col("y_pred"),
        )
        .sort("difficulty_b")
    )


def with_saturation(params: pl.DataFrame, theta_max: float) -> pl.DataFrame:
    """Saturaatioaste = σ(a(θ_max − b)): parhaan mallin odotettu osuus arvausrajan yläpuolisesta
    vaihteluvälistä. 1 = testi ei enää erottele kärkimalleja."""
    a = params["discrimination_a"].to_numpy()
    b = params["difficulty_b"].to_numpy()
    return params.with_columns(saturation=pl.Series(1 / (1 + np.exp(-a * (theta_max - b)))))


# --- Kustannukset -------------------------------------------------------------------------


def cost_view(
    scores: pl.DataFrame,
    prices: pl.DataFrame,
    target: float,
    ff: ForecastFile,
    as_of: date,
    n_boot: int | None = None,
    frontier_slope: float | None = None,
) -> dict:
    """Iso-kyvykkyyskäyrä ja T½ valitulle θ_target-tasolle (cost_forecast + taulumuodot).
    frontier_slope: kyvykkyysennusteen slope_per_year A26:n lattiaan (A29)."""
    if prices.is_empty() or prices["blended"].drop_nulls().is_empty():
        return {"status": "insufficient", "note": "hintadataa ei ole (aja forecaster ingest all)"}
    fc = cost_forecast(
        forecast_input(scores, ff, as_of), prices, target, forecast_config(ff, n_boot), as_of,
        frontier_slope,
    )  # fmt: skip
    monthly = (
        pl.DataFrame(
            fc["monthly_cheapest"],
            schema={"month": pl.Date, "cost": pl.Float64, "model_id": pl.String},
            orient="row",
        )
        if fc["monthly_cheapest"]
        else pl.DataFrame(schema={"month": pl.Date, "cost": pl.Float64, "model_id": pl.String})
    )
    horizons = [
        {
            "date": add_months(as_of, h["months"]),
            "months": h["months"],
            "p10": h["cost_p10_p50_p90"][0],
            "p50": h["cost_p10_p50_p90"][1],
            "p90": h["cost_p10_p50_p90"][2],
        }
        for h in fc.get("horizons", [])
        if h["cost_p10_p50_p90"][0] is not None
    ]
    return fc | {"monthly_df": monthly, "horizons_df": pl.DataFrame(horizons)}


def price_grid(prices_fn, ff: ForecastFile, as_of: date) -> pl.DataFrame:
    """prices_fn = Repository.get_price_at_dates; kuukauden alut fit_start → as_of."""
    return prices_fn(month_starts(ff.fit_start, as_of))


# --- Simulaattori ------------------------------------------------------------------------


@dataclass(frozen=True)
class SimSettings:
    """Benchmarkien painot (puuttuva = konfiguraation paino; 0 = pois) ja suodattimet."""

    weights: dict[str, float] = field(default_factory=dict)
    drop_contamination: frozenset[str] = frozenset()  # esim. {"high"}
    exclude_outliers: bool = True

    def key(self) -> str:
        """Välimuistiavain: painojen ja suodattimien hash."""
        payload = {
            "w": sorted((k, round(v, 6)) for k, v in self.weights.items()),
            "c": sorted(self.drop_contamination),
            "o": self.exclude_outliers,
        }
        return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:16]


def apply_settings(obs: pl.DataFrame, bench: pl.DataFrame, s: SimSettings) -> pl.DataFrame:
    """obs (get_observation_matrix) + benchmarkien metatiedot → painotettu, suodatettu syöte.

    bench: benchmark_id, contamination_risk.
    """
    risk = dict(bench.select("benchmark_id", "contamination_risk").iter_rows())
    out = obs.filter(
        ~pl.col("benchmark_id")
        .replace_strict(risk, default=None)
        .is_in(list(s.drop_contamination))
        .fill_null(False)
    )
    if s.weights:
        out = out.with_columns(
            weight=pl.col("benchmark_id")
            .replace_strict(s.weights, default=pl.col("weight"))
            .cast(pl.Float64)
        )
    return out.filter(pl.col("weight") > 0)


def simulate(
    obs: pl.DataFrame,
    bench: pl.DataFrame,
    anchors: dict[str, float],
    s: SimSettings,
    min_model_obs: int = 3,
    min_benchmark_obs: int = 5,
) -> tuple[IRTResult, pl.DataFrame]:
    """IRT uudelleen säädetyillä painoilla (ilman SE:tä → nopea). Palauttaa (tulos, syöte)."""
    data = apply_settings(obs, bench, s)
    pr = prune_sparse(data, min_model_obs, min_benchmark_obs)
    if pr.obs.is_empty():
        raise ValueError("Ei havaintoja säätöjen ja karsinnan jälkeen")
    anchors_in = {m: v for m, v in anchors.items() if m in set(pr.obs["model_id"])}
    res = fit_irt(pr.obs, IRTConfig(anchors=anchors_in, compute_se=False))
    return res, pr.obs


def frontier_slope(scores: pl.DataFrame, fit_start: date) -> float | None:
    """Rintaman lineaarinen kulmakerroin θ/vuosi (piste-estimaatti, ei bootstrapia)."""
    sc = with_frontier(scores).filter(pl.col("is_frontier") & (pl.col("release_date") >= fit_start))
    if sc.height < 3:
        return None
    t = years_since(sc["release_date"].to_numpy().astype("datetime64[D]"), fit_start)
    slope = float(np.polyfit(t, sc["theta"].to_numpy(), 1)[0])
    return slope if math.isfinite(slope) else None


def compare_runs(base: pl.DataFrame, sim: IRTResult) -> pl.DataFrame:
    """base: model_id, display_name, release_date, theta → lisää theta_sim, delta, sijoitukset."""
    sim_df = pl.DataFrame({"model_id": sim.model_ids, "theta_sim": sim.theta})
    out = base.join(sim_df, on="model_id", how="left").with_columns(
        delta=pl.col("theta_sim") - pl.col("theta"),
        rank_base=pl.col("theta").rank("ordinal", descending=True),
        rank_sim=pl.col("theta_sim").rank("ordinal", descending=True),
    )
    return out.with_columns(rank_change=pl.col("rank_base") - pl.col("rank_sim")).sort(
        "theta_sim", descending=True, nulls_last=True
    )
