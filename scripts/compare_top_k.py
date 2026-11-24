"""A32 / 2.6: pääennuste vs. vertailuennuste (vauhti k parhaasta per neljännes) liukuvalla
backtestillä tiheämmillä lähtöpäivillä kuin raportissa (toistettava).

Ajo:  .venv\\Scripts\\python.exe scripts\\compare_top_k.py [n_boot] [as_of] [irt_run]
      metodologian luvut: ... compare_top_k.py 300 2026-11-23 \\
                          irt_overall_20261123T082225990742_6ff11b99   (muutama minuutti)

Lukee annetun (oletus: viimeisimmän) overall-IRT-ajon kannasta data/benchmark.duckdb
(vain luku) ja
config/forecast.yaml-asetukset. Mittarit lasketaan vain lähtöpäiviltä, jotka onnistuivat
kaikilla menetelmillä.
"""

import sys
from datetime import date
from functools import partial
from pathlib import Path

import numpy as np
import polars as pl

from forecaster.config import get_paths, load_forecast
from forecaster.db.repository import Repository
from forecaster.models.forecast_run import _origin_metrics
from forecaster.models.forecaster import (
    ForecastConfig,
    backtest_capability_rolling,
    top_k_capability_forecast,
)

ROOT = Path(__file__).resolve().parents[1]
ORIGINS = tuple(range(3, 31, 3))


def main(n_boot: int, as_of: date, irt_run: str | None) -> None:
    paths = get_paths(ROOT)
    ff = load_forecast(paths)
    with Repository.connect(paths.db_path, read_only=True) as repo:
        irt_run = irt_run or repo.latest_run_id("overall")
        scores = repo.get_capability_with_dates(irt_run)
    print(f"IRT-ajo {irt_run}")
    scores = scores.filter(
        (pl.col("theta_se") <= ff.max_theta_se) & (pl.col("release_date") <= as_of)
    )
    cfg = ForecastConfig(
        fit_start=ff.fit_start, n_boot=n_boot, seed=ff.seed,
        piecewise_window_months=ff.piecewise_window_months,
        piecewise_alpha=ff.piecewise_alpha, small_sample=ff.small_sample,
        small_sample_max_points=ff.small_sample_max_points,
        fit_windows_months=tuple(ff.fit_windows_months),
    )  # fmt: skip
    methods = {"pääennuste": None} | {
        f"top-{k}": partial(top_k_capability_forecast, k=k) for k in (1, 3, 5)
    }
    runs = {
        name: backtest_capability_rolling(scores, cfg, as_of, ORIGINS, 12, forecast_fn=fn)
        for name, fn in methods.items()
    }
    common = set.intersection(
        *({o["cutoff"] for o in r["origins"] if o["status"] == "ok"} for r in runs.values())
    )
    print(f"as_of {as_of}, n_boot {n_boot}, yhteisiä lähtöpäiviä {len(common)}: "
          + ", ".join(str(c) for c in sorted(common)))  # fmt: skip
    print(f"{'menetelmä':11} {'pisteitä':>8} {'sisällä':>8} {'virhe':>7} {'|virhe|':>8} "
          f"{'leveys':>7}   lähtöpäivittäin: kk / pisteitä / sisällä / |virhe|")  # fmt: skip
    for name, r in runs.items():
        m = _origin_metrics(r, common)
        per = [
            f"{o['origin_months']}/{o['n_points']}/{o['share_inside_p10_p90']:.2f}/"
            f"{np.mean([abs(p['theta'] - p['prediction_p10_p50_p90'][1]) for p in o['points']]):.2f}"  # noqa: E501
            for o in r["origins"]
            if o["status"] == "ok" and o["cutoff"] in common
        ]
        print(f"{name:11} {m['n_points']:8d} {m['mean_origin_share']:8.2f} "
              f"{m['mean_error']:+7.2f} {m['mean_abs_error']:8.2f} "
              f"{m['mean_width_p10_p90']:7.2f}   " + " ".join(per))  # fmt: skip


if __name__ == "__main__":
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    day = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    main(nb, day, sys.argv[3] if len(sys.argv) > 3 else None)
