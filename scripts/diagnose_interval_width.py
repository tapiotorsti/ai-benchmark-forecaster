"""2.10: miksi kyvykkyysennusteen välit ovat oikealla datalla leveämpiä kuin virheet.

Ajo:  .venv\\Scripts\\python.exe scripts\\diagnose_interval_width.py [n_boot] [as_of] [irt_run]

Jokaiselle lähtöpäivälle (3–27 kk ennen as_ofia) sovitetaan pääennuste vain lähtöpäivään
mennessä julkaistulla datalla, ja sitä verrataan lähtöpäivän jälkeen toteutuneisiin
rintamapisteisiin (enintään 12 kk eteenpäin). Tulostetaan:
  1. välin leveyden osat: trendin P10–P90 vs. ennusteen P10–P90 ja jäännöskohina s
     (koko jakson lineaarinen OLS-sovitus rintamaan lähtöpäivänä)
  2. todellinen virhe: RMSE (toteuma − ennusteen P50) verrattuna s:ään ja välin
     implisiittiseen hajontaan (leveys / 2.563)
  3. asetusvariantit: A27 (small_sample) ja A28 (sovitusikkunat) pois/päällä, myös
     vertailuennusteelle (top-3)
  4. koko jakson rintaman jäännösten itseisarvo ajassa (puolivuosittain)
  5. jäännöshajonta s lähtöpäivittäin rintaman omalla OLS-kulmakertoimella ja top-3:n
     kulmakertoimella (taso = jäännösten keskiarvo), kaikilla pisteillä ja ilman
     ensimmäistä rintamapistettä

Rajoitus: θ-arvot tulevat nykyisestä IRT-ajosta, joka on sovitettu myös lähtöpäivän jälkeen
julkaistulla datalla. Toteutuneet virheet ovat siksi todennäköisesti hieman optimistisia.
"""

import sys
from dataclasses import replace
from datetime import date
from functools import partial
from pathlib import Path

import numpy as np
import polars as pl
import statsmodels.api as sm

from forecaster.config import get_paths, load_forecast
from forecaster.db.repository import Repository
from forecaster.models.forecaster import (
    ForecastConfig,
    add_months,
    capability_forecast,
    complete_quarters,
    frontier_mask,
    top_k_capability_forecast,
    top_k_per_quarter,
    years_since,
)

ROOT = Path(__file__).resolve().parents[1]
ORIGINS = tuple(range(3, 28, 3))
Z80 = 2.563  # normaalijakauman P10–P90-välin leveys keskihajonnoissa


def frontier(scores: pl.DataFrame, start: date):
    rel = scores["release_date"].to_numpy().astype("datetime64[D]")
    th = scores["theta"].to_numpy().astype(float)
    m = frontier_mask(rel, th) & (rel >= np.datetime64(start, "D"))
    return rel[m], th[m]


def main(n_boot: int, as_of: date, irt_run: str | None) -> None:
    paths = get_paths(ROOT)
    ff = load_forecast(paths)
    with Repository.connect(paths.db_path, read_only=True) as repo:
        irt_run = irt_run or repo.latest_run_id("overall")
        scores = repo.get_capability_with_dates(irt_run)
    scores = scores.filter(
        (pl.col("theta_se") <= ff.max_theta_se) & (pl.col("release_date") <= as_of)
    ).sort("release_date", "model_id")
    base = ForecastConfig(
        fit_start=ff.fit_start, n_boot=n_boot, seed=ff.seed, horizons_months=(12,),
        piecewise_window_months=ff.piecewise_window_months,
        piecewise_alpha=ff.piecewise_alpha, small_sample=ff.small_sample,
        small_sample_max_points=ff.small_sample_max_points,
        fit_windows_months=tuple(ff.fit_windows_months),
    )  # fmt: skip
    top3 = partial(top_k_capability_forecast, k=3)
    variants = {
        "tuotanto": (capability_forecast, base),
        "ei A27": (capability_forecast, replace(base, small_sample="none")),
        "ei A28": (capability_forecast, replace(base, fit_windows_months=())),
        "ei A27/A28": (
            capability_forecast,
            replace(base, small_sample="none", fit_windows_months=()),
        ),
        "top-3": (top3, base),
        "top-3 ei A28": (top3, replace(base, fit_windows_months=())),
    }
    print(f"IRT-ajo {irt_run}, as_of {as_of}, n_boot {n_boot}\n")
    rel_all = scores["release_date"].to_numpy().astype("datetime64[D]")
    fm_all = frontier_mask(rel_all, scores["theta"].to_numpy())

    print("1–2. Pääennuste (tuotanto) lähtöpäivittäin")
    print(f"{'kk':>3} {'rint':>4} {'pist':>4} {'s':>5} {'trendiL':>7} {'ennL':>6} "
          f"{'impl.σ':>6} {'RMSE':>5} {'|e|':>5} {'sis.':>5}")  # fmt: skip
    rows: dict[str, list] = {v: [] for v in variants}
    for k in ORIGINS:
        cutoff = add_months(as_of, -k)
        end = as_of if k <= 12 else add_months(as_of, 12 - k)
        m = fm_all & (rel_all > np.datetime64(cutoff, "D")) & (rel_all <= np.datetime64(end, "D"))
        real = scores.filter(pl.Series(m))
        if not real.height:
            continue
        train = scores.filter(pl.col("release_date") <= cutoff)
        dates, y = real["release_date"].to_list(), real["theta"].to_numpy()
        fr, fy = frontier(train, ff.fit_start)
        ft = years_since(fr, ff.fit_start)
        ols = sm.OLS(fy, sm.add_constant(ft)).fit()
        s = float(np.sqrt(ols.scale))
        for name, (fn, cfg) in variants.items():
            try:
                fc = fn(train, cfg, cutoff, eval_dates=dates)
            except ValueError:
                continue
            tr = np.array([e["trend_p10_p50_p90"] for e in fc["eval"]])
            pr = np.array([e["prediction_p10_p50_p90"] for e in fc["eval"]])
            err = y - pr[:, 1]
            rows[name].append({
                "k": k, "n_front": len(fy), "n": len(y), "s": s,
                "trend_w": float(np.mean(tr[:, 2] - tr[:, 0])),
                "pred_w": float(np.mean(pr[:, 2] - pr[:, 0])),
                "rmse": float(np.sqrt(np.mean(err**2))),
                "mae": float(np.mean(np.abs(err))),
                "inside": float(np.mean((pr[:, 0] <= y) & (y <= pr[:, 2]))),
            })  # fmt: skip
        r = rows["tuotanto"][-1] if rows["tuotanto"] and rows["tuotanto"][-1]["k"] == k else None
        if r:
            print(f"{k:3d} {r['n_front']:4d} {r['n']:4d} {r['s']:5.2f} {r['trend_w']:7.2f} "
                  f"{r['pred_w']:6.2f} {r['pred_w'] / Z80:6.2f} {r['rmse']:5.2f} "
                  f"{r['mae']:5.2f} {r['inside']:5.2f}")  # fmt: skip

    print("\n3. Asetusvariantit (lähtöpäivät 3–21 kk | 24–27 kk)")
    print(f"{'variantti':13} {'ennL':>6} {'trendiL':>7} {'RMSE':>5} {'sis.':>5}   |"
          f" {'ennL':>6} {'RMSE':>5} {'sis.':>5}")  # fmt: skip
    for name, rs in rows.items():
        a = [r for r in rs if r["k"] <= 21]
        b = [r for r in rs if r["k"] > 21]

        def agg(sub, key):
            return np.mean([r[key] for r in sub]) if sub else float("nan")

        def pooled_rmse(sub):
            if not sub:
                return float("nan")
            return float(np.sqrt(sum(r["rmse"] ** 2 * r["n"] for r in sub)
                                 / sum(r["n"] for r in sub)))  # fmt: skip

        print(f"{name:13} {agg(a, 'pred_w'):6.2f} {agg(a, 'trend_w'):7.2f} "
              f"{pooled_rmse(a):5.2f} {agg(a, 'inside'):5.2f}   | {agg(b, 'pred_w'):6.2f} "
              f"{pooled_rmse(b):5.2f} {agg(b, 'inside'):5.2f}")  # fmt: skip

    print("\n4. Koko jakson rintaman jäännökset (lineaarinen OLS) puolivuosittain")
    fr, fy = frontier(scores, ff.fit_start)
    ft = years_since(fr, ff.fit_start)
    res = fy - sm.OLS(fy, sm.add_constant(ft)).fit().predict(sm.add_constant(ft))
    half = (fr.astype("datetime64[M]").astype(int) // 6) * 6
    for h in np.unique(half):
        sel = half == h
        print(
            f"  {np.datetime64(int(h), 'M')}: n {sel.sum():2d}, |jäännös| ka. "
            f"{np.mean(np.abs(res[sel])):.2f}, jäännös ka. {np.mean(res[sel]):+.2f}"
        )
    # Ennätyksen yli: uuden rintamapisteen hyppy edelliseen nähden vs. trendin kasvu välissä
    jumps = np.diff(fy)
    gaps = np.diff(ft)
    print(
        f"\n  Rintaman hypyt: n {len(jumps)}, hypyn ka. {np.mean(jumps):.2f} θ, "
        f"väli ka. {np.mean(gaps) * 12:.1f} kk"
    )

    print("\n5. Jäännöshajonta s lähtöpäivänä (n−p vapausastetta)")
    print(f"{'kk':>4} {'OLS b':>6} {'top3 b':>6} {'s OLS':>6} {'s top3':>6}   ilman 1. pistettä:"
          f" {'OLS b':>6} {'s OLS':>6} {'s top3':>6}")  # fmt: skip
    for k in (0, *ORIGINS):
        cutoff = add_months(as_of, -k)
        train = scores.filter(pl.col("release_date") <= cutoff)
        fr, fy = frontier(train, ff.fit_start)
        ft = years_since(fr, ff.fit_start)
        rel = train["release_date"].to_numpy().astype("datetime64[D]")
        th = train["theta"].to_numpy().astype(float)
        mask = (rel >= np.datetime64(ff.fit_start, "D")) & complete_quarters(
            rel, ff.fit_start, cutoff
        )
        top = top_k_per_quarter(rel, th, mask, 3)
        b_top = float(np.polyfit(years_since(rel[top], ff.fit_start), th[top], 1)[0])

        def s_ols(t, y):
            return float(np.sqrt(sm.OLS(y, sm.add_constant(t)).fit().scale))

        def s_top(t, y, b=b_top):
            r = y - b * t
            r = r - r.mean()
            return float(np.sqrt(r @ r / (len(y) - 1)))

        b_ols = float(np.polyfit(ft, fy, 1)[0])
        t1, y1 = ft[1:], fy[1:]
        b_ols1 = float(np.polyfit(t1, y1, 1)[0])
        print(f"{k:4d} {b_ols:6.2f} {b_top:6.2f} {s_ols(ft, fy):6.2f} {s_top(ft, fy):6.2f}"
              f"   {'':11} {b_ols1:6.2f} {s_ols(t1, y1):6.2f} {s_top(t1, y1):6.2f}")  # fmt: skip


if __name__ == "__main__":
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    day = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    main(nb, day, sys.argv[3] if len(sys.argv) > 3 else None)
