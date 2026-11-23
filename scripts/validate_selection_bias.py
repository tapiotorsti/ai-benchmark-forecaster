"""A31: rintamavalinnan harhakorjauksen synteettinen validointi (toistettava).

Ajo:  .venv\\Scripts\\python.exe scripts\\validate_selection_bias.py <toistoja> <SE> [<SE> ...]
      esim. ... validate_selection_bias.py 100 0.15 0.3   (metodologian luvut, ~10 min)

Synteettinen data: tosi θ suoran 0.5 + 1.2·t alapuolella, havaittu θ̂ = θ + N(0, SE²).
Mitataan cutoffista 12 kk eteenpäin:
  - havaitut ennätykset (rintama θ̂:sta): virhe θ̂ − P50 (tavallinen ennuste, pitäisi ≈ 0)
  - todelliset ennätykset (rintama todesta θ:sta): virhe θ − P50 tavallisella ja korjatulla,
    sekä P10–P90-välin kattavuus kummallakin.
"""

import sys
from datetime import date, timedelta

import numpy as np
import polars as pl

from forecaster.models.forecaster import (
    ForecastConfig,
    add_months,
    capability_forecast,
    frontier_mask,
    selection_bias,
)

START = date(2023, 1, 1)
AS_OF = date(2026, 11, 1)
CUT = add_months(AS_OF, -12)


def make(seed, n, se):
    rng = np.random.default_rng(seed)
    span = (AS_OF - START).days
    rows = []
    for k in range(n):
        d = START + timedelta(days=int(rng.uniform(0, span)))
        t = (d - START).days / 365.25
        true = 0.5 + 1.2 * t + rng.normal(0, 0.25) - abs(rng.normal(0, 0.8))
        rows.append((f"m{k:03d}", d, true + rng.normal(0, se), se, true))
    return pl.DataFrame(
        rows, schema=["model_id", "release_date", "theta", "theta_se", "true"], orient="row"
    )


def future_records(df, col):
    rel = df["release_date"].to_numpy().astype("datetime64[D]")
    fm = frontier_mask(rel, df[col].to_numpy())
    f = df.filter(
        pl.Series(fm) & (pl.col("release_date") > CUT) & (pl.col("release_date") <= AS_OF)
    )
    return f


def run(se, reps, n=120):
    e_obs, e_true, e_true_corr, biases, cov_t, cov_tc = [], [], [], [], [], []
    for s in range(reps):
        df = make(s, n, se)
        train = df.filter(pl.col("release_date") <= CUT).select(
            "model_id", "release_date", "theta", "theta_se"
        )
        cfg = ForecastConfig(
            n_boot=200, seed=s, small_sample="auto", fit_windows_months=(18, 24, 36)
        )
        obs_rec = future_records(df, "theta")
        true_rec = future_records(df, "true")
        dates = sorted(
            set(obs_rec["release_date"].to_list()) | set(true_rec["release_date"].to_list())
        )
        if not dates:
            continue
        try:
            fc = capability_forecast(train, cfg, CUT, eval_dates=dates)
        except ValueError:
            continue
        sb = selection_bias(train, cfg, CUT, n_sim=15, inner_boot=80, eval_dates=dates)
        if sb["status"] != "ok":
            continue
        p50 = {d: e["prediction_p10_p50_p90"] for d, e in zip(dates, fc["eval"], strict=True)}
        bias = dict(zip(dates, sb["eval_bias"], strict=True))
        biases.append(np.mean(list(bias.values())))
        for d, th in obs_rec.select("release_date", "theta").iter_rows():
            e_obs.append(th - p50[d][1])
        for d, th in true_rec.select("release_date", "true").iter_rows():
            lo, mid, hi = p50[d]
            e_true.append(th - mid)
            e_true_corr.append(th - (mid - bias[d]))
            cov_t.append(lo <= th <= hi)
            cov_tc.append(lo - bias[d] <= th <= hi - bias[d])
    m = lambda x: f"{np.mean(x):+.3f} ± {np.std(x) / np.sqrt(len(x)):.3f}"  # noqa: E731
    print(f"SE {se}: toistoja {len(biases)}, arvioitu harha ka. {np.mean(biases):.3f}")
    print(f"  havaitut ennätykset, tavallinen ennuste: virhe {m(e_obs)}")
    print(f"  todelliset ennätykset, tavallinen:    virhe {m(e_true)}")
    print(f"    kattavuus {np.mean(cov_t):.3f}")
    print(f"  todelliset ennätykset, harhakorjattu: virhe {m(e_true_corr)}")
    print(f"    kattavuus {np.mean(cov_tc):.3f}")


if __name__ == "__main__":
    reps = int(sys.argv[1])
    for se in map(float, sys.argv[2:]):
        run(se, reps)
