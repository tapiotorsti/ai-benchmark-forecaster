"""K5:n hyväksymiskriteerit (TOTEUTUSSUUNNITELMA.md, K5).

- Synteettisellä lineaarisella trendillä estimoitu kulmakerroin osuu luottamusväliin
- Palainen trendi tunnistetaan, kun viimeisen 12 kk:n kulmakerroin poikkeaa
- Puoliintumisaika palautuu tunnetusta hintasarjasta; α1 ≤ 0 → määrittelemätön (ei negatiivinen)
- Backtest-raportti on olemassa ja sen virhe lasketaan
- θ-epävarmuus vaikuttaa bootstrapiin (A5); tulokset deterministisiä siemenellä
"""

import math
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from forecaster.models.forecaster import (
    ForecastConfig,
    add_months,
    backtest_capability,
    backtest_cost,
    capability_forecast,
    cost_forecast,
    frontier_mask,
    month_starts,
)

AS_OF = date(2026, 11, 1)
START = date(2023, 1, 1)


def make_scores(slope=1.2, slope_recent=None, n=45, noise=0.05, se=0.05, seed=0):
    """Rintamamallit suoralla (+ hidas joukko alapuolella). slope_recent: kulmakerroin
    viimeisen 12 kk:n aikana (palainen)."""
    rng = np.random.default_rng(seed)
    knot = (add_months(AS_OF, -12) - START).days / 365.25
    rows = []
    for k in range(n):
        d = START + timedelta(days=int(k * (AS_OF - START).days / n))
        t = (d - START).days / 365.25
        mu = 0.5 + slope * t
        if slope_recent is not None and t > knot:
            mu += (slope_recent - slope) * (t - knot)
        rows.append((f"f{k:02d}", d, mu + rng.normal(0, noise), se))
        rows.append((f"slow{k:02d}", d, mu - 1.5 + rng.normal(0, 0.3), se))
    return pl.DataFrame(rows, schema=["model_id", "release_date", "theta", "theta_se"],
                        orient="row")  # fmt: skip


CFG = ForecastConfig(n_boot=300, seed=1)


# --- Rintama -------------------------------------------------------------------


def test_frontier_mask_rules():
    rel = np.array(["2023-01-01", "2023-06-01", "2023-09-01", "2024-03-01", "2024-03-01",
                    "2024-06-01"], dtype="datetime64[D]")  # fmt: skip
    th = np.array([0.0, -1.0, 0.5, 1.2, 0.9, 1.2])
    # saman päivän heikompi (0.9) ei, tasapeli aiemman kanssa (1.2) ei
    assert frontier_mask(rel, th).tolist() == [True, False, True, True, False, False]


# --- Kyvykkyys -------------------------------------------------------------------


def test_linear_slope_recovered():
    fc = capability_forecast(make_scores(slope=1.2), CFG, AS_OF)
    lo, _, hi = fc["slope_per_year_p10_p50_p90"]
    assert fc["model"] == "linear"
    assert lo <= 1.2 <= hi, (lo, hi)
    assert fc["slope_per_year"] == pytest.approx(1.2, abs=0.1)
    # 12 kk ennuste ≈ suora
    t12 = (add_months(AS_OF, 12) - START).days / 365.25
    h12 = next(h for h in fc["horizons"] if h["months"] == 12)
    assert h12["trend_p10_p50_p90"][1] == pytest.approx(0.5 + 1.2 * t12, abs=0.2)


def test_prediction_band_contains_trend_band():
    fc = capability_forecast(make_scores(noise=0.2), CFG, AS_OF)
    for h in fc["horizons"]:
        t, p = h["trend_p10_p50_p90"], h["prediction_p10_p50_p90"]
        assert p[0] <= t[0] and p[2] >= t[2]


def test_piecewise_detected_when_recent_slope_differs():
    fc = capability_forecast(make_scores(slope=0.6, slope_recent=2.5), CFG, AS_OF)
    assert fc["model"] == "piecewise"
    assert fc["piecewise_p_value"] < 0.05
    assert fc["recent_slope_per_year"] == pytest.approx(2.5, abs=0.4)
    assert fc["slope_per_year"] == pytest.approx(2.5, abs=0.4)


def _measured(se: float, seed: int = 0) -> pl.DataFrame:
    """Havaittu θ̂ = todellinen + N(0, se²): ilmoitettu SE vastaa todellista mittausvirhettä."""
    sc = make_scores(slope=1.2, noise=0.1, se=se, seed=seed)
    rng = np.random.default_rng(100 + seed)
    return sc.with_columns(theta=sc["theta"] + rng.normal(0, se, sc.height))


def test_theta_uncertainty_widens_slope_interval():
    """A5: suurempi (todellinen) θ-epävarmuus → leveämpi kulmakertoimen väli."""
    narrow = capability_forecast(_measured(0.05), CFG, AS_OF)["slope_per_year_p10_p50_p90"]
    wide = capability_forecast(_measured(0.6), CFG, AS_OF)["slope_per_year_p10_p50_p90"]
    assert (wide[2] - wide[0]) > (narrow[2] - narrow[0])


def test_theta_noise_not_counted_twice():
    """A25: arvottu θ ratkaisee vain rintamajäsenyyden, sovitus alkuperäisiin θ̂:hin →
    bootstrapin mediaani ≈ pistekohtainen OLS-ennuste (ei omaa lisäharhaa).
    Kun arvottu θ syötettiin sovitukseen, mediaani oli ~+0.2 θ piste-estimaatin yläpuolella
    (SE 0.3; math-reviewer, 40 siementä). Rintamavalinnan harha todelliseen θ:aan nähden
    (~+0.3) on aineistossa itsessään ja näkyy myös piste-estimaatissa."""
    from forecaster.models.forecaster import _ols, frontier_mask, years_since

    t12 = years_since([add_months(AS_OF, 12)], START)[0]
    diffs = []
    for s in range(8):
        sc = _measured(0.3, s)
        fc = capability_forecast(sc, ForecastConfig(n_boot=150, seed=s), AS_OF)
        rel = sc["release_date"].to_numpy().astype("datetime64[D]")
        fm = frontier_mask(rel, sc["theta"].to_numpy()) & (rel >= np.datetime64(START, "D"))
        beta = _ols(years_since(rel[fm], START), sc["theta"].to_numpy()[fm], None).params
        point = beta[0] + beta[1] * t12
        mid = next(h for h in fc["horizons"] if h["months"] == 12)["trend_p10_p50_p90"][1]
        diffs.append(mid - point)
    assert abs(np.mean(diffs)) < 0.1, np.mean(diffs)


def _sparse_scores(seed: int, n: int = 30) -> pl.DataFrame:
    """Harva rintama (~9 pistettä): mallit satunnaisina päivinä suoran alapuolella."""
    rng = np.random.default_rng(seed)
    span = (AS_OF - START).days
    rows = []
    for k in range(n):
        d = START + timedelta(days=int(rng.uniform(0, span)))
        t = (d - START).days / 365.25
        th = 0.5 + 1.2 * t + rng.normal(0, 0.25) - abs(rng.normal(0, 0.8))
        rows.append((f"m{k:03d}", d, th + rng.normal(0, 0.15), 0.15))
    return pl.DataFrame(rows, schema=["model_id", "release_date", "theta", "theta_se"],
                        orient="row")  # fmt: skip


def test_small_sample_inflate_widens_around_same_center():
    """A27: inflate leventää trendi- ja ennustevälejä, mutta ei siirrä mediaania."""
    sc = _sparse_scores(3)
    base = capability_forecast(sc, ForecastConfig(n_boot=400, seed=2), AS_OF)
    infl = capability_forecast(sc, ForecastConfig(n_boot=400, seed=2, small_sample="inflate"),
                               AS_OF)  # fmt: skip
    for hb, hi in zip(base["horizons"], infl["horizons"], strict=True):
        for band in ("trend_p10_p50_p90", "prediction_p10_p50_p90"):
            b, i = hb[band], hi[band]
            assert (i[2] - i[0]) > (b[2] - b[0])
            assert i[1] == pytest.approx(b[1], abs=0.1 * (b[2] - b[0]))


def test_small_sample_none_unchanged():
    """A27: oletustila tuottaa bitilleen saman tuloksen kuin ennen korjauksen lisäämistä
    (arvot ajettu ennen muutosta; satunnaislukuvirran järjestys ei saa muuttua)."""
    fc = capability_forecast(_sparse_scores(3), ForecastConfig(n_boot=400, seed=2), AS_OF)
    assert fc["slope_per_year_p10_p50_p90"] == pytest.approx(
        [1.200387059816618, 1.3035127360740342, 1.4054566705240854], abs=1e-12)  # fmt: skip
    assert fc["horizons"][0]["trend_p10_p50_p90"] == pytest.approx(
        [5.5918937816453145, 5.898534567221857, 6.243605071119024], abs=1e-12)  # fmt: skip


def test_small_sample_inflate_with_single_point_after_knot():
    """Saranan jälkeen vain yksi alkuperäinen rintamapiste → palaisia vertailuparametreja ei
    sovita (saranakerroin interpoloisi pisteen); ajo onnistuu ja välit ovat äärellisiä."""
    rows = [(f"m{k}", add_months(START, 3 * k), 0.5 + 0.3 * k, 0.3) for k in range(14)]
    rows.append(("late", add_months(AS_OF, -2), 5.5, 0.3))
    sc = pl.DataFrame(rows, schema=["model_id", "release_date", "theta", "theta_se"],
                      orient="row")  # fmt: skip
    fc = capability_forecast(sc, ForecastConfig(n_boot=200, seed=0, small_sample="inflate"),
                             AS_OF)  # fmt: skip
    for h in fc["horizons"]:
        assert all(np.isfinite(h["prediction_p10_p50_p90"]))


def test_small_sample_auto_applies_only_to_small_frontier():
    """A27 auto: korjaus vain, kun rintamassa on alle small_sample_max_points pistettä;
    muuten tulos on sama kuin none-tilassa."""
    auto = ForecastConfig(n_boot=100, seed=0, small_sample="auto")
    small = capability_forecast(_sparse_scores(3), auto, AS_OF)
    assert small["n_frontier"] < 12 and small["small_sample_applied"]
    big_sc = make_scores()
    big = capability_forecast(big_sc, auto, AS_OF)
    assert big["n_frontier"] >= 12 and not big["small_sample_applied"]
    none = capability_forecast(big_sc, ForecastConfig(n_boot=100, seed=0), AS_OF)
    assert big["horizons"] == none["horizons"]


def _break_scores(seed: int, n: int = 120, as_of: date = AS_OF, linear: bool = False):
    """Kehitysvauhti 0.3 θ/v, kunnes 27 kk ennen as_ofia, sen jälkeen 2.0 (vrt. 2023 → 2024).
    linear=True: tasainen 1.2 θ/v (vertailuskenaario)."""
    rng = np.random.default_rng(seed)
    t_break = (add_months(as_of, -27) - START).days / 365.25
    span = (as_of - START).days
    rows = []
    for k in range(n):
        d = START + timedelta(days=int(rng.uniform(0, span)))
        t = (d - START).days / 365.25
        mu = (0.5 + 1.2 * t) if linear else (
            0.5 + 0.3 * min(t, t_break) + 2.0 * max(0.0, t - t_break))  # fmt: skip
        th = mu + rng.normal(0, 0.25) - abs(rng.normal(0, 0.8))
        rows.append((f"m{k:03d}", d, th + rng.normal(0, 0.15), 0.15))
    return pl.DataFrame(rows, schema=["model_id", "release_date", "theta", "theta_se"],
                        orient="row")  # fmt: skip


def test_fit_windows_follow_recent_slope_after_break():
    """A28: kun vauhti on kiihtynyt, ikkunat siirtävät kulmakertoimen jakaumaa kohti tuoretta
    vauhtia (2.0) ja nostavat 12 kk:n mediaania; koko jakson sovitus jää jälkeen."""
    cut = add_months(AS_OF, -12)
    sc = _break_scores(1).filter(pl.col("release_date") <= cut)
    off = capability_forecast(sc, ForecastConfig(n_boot=300, seed=0), cut)
    win = capability_forecast(sc, ForecastConfig(n_boot=300, seed=0,
                                                 fit_windows_months=(18, 24, 36)), cut)  # fmt: skip
    assert win["slope_per_year_p10_p50_p90"][2] > off["slope_per_year_p10_p50_p90"][2]
    h_off = next(h for h in off["horizons"] if h["months"] == 12)["prediction_p10_p50_p90"]
    h_win = next(h for h in win["horizons"] if h["months"] == 12)["prediction_p10_p50_p90"]
    assert h_win[1] > h_off[1]


def test_fit_windows_do_not_distort_history_band():
    """A28 (katselmointi): lyhyen ikkunan suoraa ei ekstrapoloida ikkunaa edeltävään aikaan,
    joten dashboardin historiakaista (eval ennen ikkunoita) ei levene."""
    cut = add_months(AS_OF, -12)
    early = add_months(START, 6)
    for seed in range(3):
        sc = _break_scores(seed).filter(pl.col("release_date") <= cut)
        off = capability_forecast(sc, ForecastConfig(n_boot=200, seed=0), cut, eval_dates=[early])
        win = capability_forecast(sc, ForecastConfig(n_boot=200, seed=0,
                                                     fit_windows_months=(18, 24, 36)),
                                  cut, eval_dates=[early])  # fmt: skip
        o, w = off["eval"][0]["trend_p10_p50_p90"], win["eval"][0]["trend_p10_p50_p90"]
        assert (w[2] - w[0]) <= 1.3 * (o[2] - o[0]) + 0.05, (o, w)


def test_fit_window_before_start_is_identical_to_none():
    """Ikkuna, joka alkaa ennen fit_startia, jätetään pois → tulos bitilleen sama kuin ilman
    ikkunoita (satunnaislukuvirta ei muutu)."""
    sc = make_scores()
    off = capability_forecast(sc, ForecastConfig(n_boot=150, seed=3), AS_OF)
    w60 = capability_forecast(sc, ForecastConfig(n_boot=150, seed=3, fit_windows_months=(60,)),
                              AS_OF)  # fmt: skip
    assert w60["horizons"] == off["horizons"] and w60["fit_window_starts"] == []


def test_fit_window_with_too_few_points_is_dropped():
    """Katselmointi: ikkuna, jonka rintamassa on alle min_points + 1 pistettä, jätetään pois
    (muuten puolet drawista epäonnistuisi ja ennuste voisi kaatua)."""
    rows = [(f"m{k}", add_months(START, 4 * k), 0.4 * k, 0.1) for k in range(8)]  # 2023–2025
    rows += [
        ("late1", add_months(AS_OF, -10), 3.6, 0.1),
        ("late2", add_months(AS_OF, -3), 3.9, 0.1),
    ]
    sc = pl.DataFrame(rows, schema=["model_id", "release_date", "theta", "theta_se"],
                      orient="row")  # fmt: skip
    fc = capability_forecast(sc, ForecastConfig(n_boot=200, seed=0, fit_windows_months=(12, 18)),
                             AS_OF)  # fmt: skip
    assert fc["fit_window_starts"] == []  # 12 kk: 2 pistettä, 18 kk: 3 pistettä
    assert fc["n_boot_ok"] == 200


def test_fit_windows_bookkeeping():
    """A28: ikkuna, joka alkaa ennen fit_startia, on sama kuin koko jakso; noin
    (ikkunat − 1)/ikkunat drawista käyttää lyhyempää ikkunaa; ilman ikkunoita 0."""
    sc = make_scores()
    off = capability_forecast(sc, ForecastConfig(n_boot=400, seed=0), AS_OF)
    assert off["share_windowed_draws"] == 0.0 and off["fit_windows_months"] == []
    # AS_OF 2026-11 − 60 kk < fit_start 2023-01 → vain 12 kk on aito ikkuna
    win = capability_forecast(sc, ForecastConfig(n_boot=400, seed=0,
                                                 fit_windows_months=(12, 60)), AS_OF)  # fmt: skip
    assert win["share_windowed_draws"] == pytest.approx(0.5, abs=0.07)


@pytest.mark.slow
def test_fit_windows_improve_coverage_after_break():
    """A28 Monte Carlo: kiihtymisen jälkeen ennustevälin kattavuus on ilman ikkunoita liian
    pieni (~0.70, 200 toistoa) ja ikkunat 18/24/36 nostavat sen ~0.81:een."""
    cut = add_months(AS_OF, -12)
    shares = {(): [], (18, 24, 36): []}
    for s in range(60):
        sc = _break_scores(s)
        for w in shares:
            cfg = ForecastConfig(n_boot=120, seed=s, small_sample="auto", fit_windows_months=w)
            try:
                bt = backtest_capability(sc, cfg, AS_OF, cutoff=cut)
            except ValueError:
                continue
            if bt["share_inside_p10_p90"] is not None:
                shares[w].append(bt["share_inside_p10_p90"])
    off, win = np.mean(shares[()]), np.mean(shares[(18, 24, 36)])
    assert win - off > 0.05, (off, win)
    # Tasaisessa kehityksessä välit levenevät (MC: 0.82 → 0.87), mutta eivät liikaa
    lin = []
    for s in range(60):
        cfg = ForecastConfig(
            n_boot=120, seed=s, small_sample="auto", fit_windows_months=(18, 24, 36)
        )
        bt = backtest_capability(_break_scores(s, linear=True), cfg, AS_OF, cutoff=cut)
        if bt["share_inside_p10_p90"] is not None:
            lin.append(bt["share_inside_p10_p90"])
    assert np.mean(lin) < 0.93, np.mean(lin)


PROD_WINDOWS = (18, 24, 36)


def test_headline_slope_recovers_true_rate_with_windows():
    """A29: otsikkoluku (bootstrap-mediaani tuotannon ikkunoilla) palauttaa tasaisen vauhdin."""
    cfg = ForecastConfig(n_boot=300, seed=0, fit_windows_months=PROD_WINDOWS)
    fc = capability_forecast(make_scores(slope=1.2), cfg, AS_OF)
    assert fc["slope_per_year"] == pytest.approx(1.2, abs=0.15)
    assert fc["slope_per_year_full_period"] == pytest.approx(1.2, abs=0.1)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_headline_slope_not_dragged_down_after_detected_break(seed):
    """Katselmointi A29: kun koko jakson saranatesti tunnistaa murroksen, saranatesti tehdään
    myös saranan yli ulottuvissa ikkunoissa. Ennen korjausta ikkunoiden suorat sekoittivat
    vanhan ja uuden vauhdin, ja mediaani oli 10–20 % liian matala (2.5 → 1.86–2.14)."""
    sc = make_scores(slope=0.6, slope_recent=2.5, noise=0.15, seed=seed)
    off = capability_forecast(sc, ForecastConfig(n_boot=300, seed=0), AS_OF)
    cfg = ForecastConfig(n_boot=300, seed=0, fit_windows_months=PROD_WINDOWS)
    win = capability_forecast(sc, cfg, AS_OF)
    assert win["model"] == "piecewise" and win["fit_window_starts"]
    o, w = off["slope_per_year"], win["slope_per_year"]
    assert w >= o - 0.1, (o, w)  # vanhalla logiikalla w oli 0.3–0.6 pienempi
    for key in ("share_piecewise_full_draws", "share_piecewise_window_draws"):
        assert 0.0 <= win[key] <= 1.0, (key, win[key])


def _stored_forecast(run_id: str, as_of: date, p50_start: float, slope: float, width: float):
    """Tallennetun ennusteen muoto (JSON:sta luettu: päivät merkkijonoina)."""
    monthly = []
    for k in range(25):
        d = add_months(as_of, k)
        mid = p50_start + slope * k / 12
        monthly.append({"date": str(d), "prediction_p10_p50_p90": [mid - width, mid, mid + width]})
    return {"run_id": run_id, "irt_run_id": "x", "created_at": None,
            "result": {"as_of": str(as_of), "monthly": monthly}}  # fmt: skip


def test_prospective_calibration_counts_hits_after_forecast_date():
    """A30: vain ennustepäivän jälkeen julkaistut rintamapisteet; kaista interpoloidaan
    julkaisupäivälle; ennusteet ilman kuukausikaistaa ohitetaan."""
    from forecaster.models.forecaster import prospective_calibration

    f0 = date(2026, 1, 5)
    rows = [
        ("old", date(2025, 12, 1), 1.0, 0.1),  # ennen ennustetta → ei mukana
        # kuun puolivälissä: P50 = (1.5 + 1.6667)/2 = 1.5833 (interpolointi 04-05 … 05-05)
        ("hit", date(2026, 4, 20), 1.6, 0.1),
        ("lower", date(2026, 5, 1), 1.2, 0.1),  # ei ennätys (1.2 < 1.6) → ei mukana
        ("miss", date(2026, 7, 5), 3.0, 0.1),  # P50 = 2.0 → ulkona
        ("future", date(2026, 12, 1), 9.0, 0.1),  # as_of:n jälkeen → ei mukana
    ]
    sc = pl.DataFrame(rows, schema=["model_id", "release_date", "theta", "theta_se"],
                      orient="row")  # fmt: skip
    history = [
        {"run_id": "no_monthly", "irt_run_id": "x", "created_at": None,
         "result": {"as_of": "2025-12-01", "horizons": []}},
        _stored_forecast("fc1", f0, 1.0, 2.0, 0.3),
    ]  # fmt: skip
    pc = prospective_calibration(history, sc, date(2026, 11, 1))
    assert pc["n_forecasts"] == 1 and pc["n_pairs"] == 2
    assert pc["pooled_share_inside_p10_p90"] == 0.5
    f = pc["forecasts"][0]
    assert f["n_points"] == 2 and f["as_of"] == f0
    p50_hit = 1.5 + 0.5 * (2.0 / 12)
    assert pc["mean_error"] == pytest.approx(((1.6 - p50_hit) + (3.0 - 2.0)) / 2, abs=1e-9)


def test_prospective_calibration_scale_shift_and_retro_runs():
    """Katselmointi A30: asteikon siirtymä δ (ennustehetken rintaman paras malli nyt vs.
    tallennettu θ) raportoidaan ja virhe myös δ-korjattuna; takautuvat ajot ohitetaan."""
    from datetime import datetime

    from forecaster.models.forecaster import prospective_calibration

    f0 = date(2026, 1, 5)
    schema = ["model_id", "release_date", "theta", "theta_se"]
    # Ennustehetkellä a:n θ oli 1.0, nyt 1.3 → koko asteikko siirtynyt +0.3
    sc = pl.DataFrame([("a", date(2025, 12, 1), 1.3, 0.1), ("b", date(2026, 3, 5), 1.8, 0.1)],
                      schema=schema, orient="row")  # fmt: skip
    h = _stored_forecast("fc", f0, 1.0, 2.0, 0.3)
    h["result"] |= {"frontier_models": ["a"], "current_frontier_theta": 1.0}
    h["created_at"] = datetime(2026, 1, 5, 7, 0)
    retro = _stored_forecast("retro", date(2026, 2, 2), 0.0, 0.0, 0.1)
    retro["created_at"] = datetime(2026, 6, 1, 7, 0)  # ajettu jälkikäteen
    pc = prospective_calibration([h, retro], sc, date(2026, 11, 1))
    assert pc["n_retro_skipped"] == 1 and pc["n_forecasts"] == 1
    assert pc["forecasts"][0]["scale_shift"] == pytest.approx(0.3)
    # b: P50 (k = 2) = 1.3333; virhe 0.4667, δ-korjattuna 0.1667
    assert pc["mean_error"] == pytest.approx(1.8 - (1.0 + 2.0 * 2 / 12), abs=1e-9)
    assert pc["mean_error_shift_adjusted"] == pytest.approx(1.8 - 0.3 - (1.0 + 2.0 * 2 / 12),
                                                            abs=1e-9)  # fmt: skip


def test_prospective_calibration_one_forecast_per_week():
    """Saman ISO-viikon ennusteista käytetään vain uusinta (päivittäiset ajot lähes samoja)."""
    from forecaster.models.forecaster import prospective_calibration

    schema = ["model_id", "release_date", "theta", "theta_se"]
    sc = pl.DataFrame([("p", date(2026, 3, 1), 1.0, 0.1)], schema=schema, orient="row")
    history = [
        _stored_forecast("mon", date(2026, 1, 5), 0.0, 0.0, 0.1),  # osuisi ohi
        _stored_forecast("wed", date(2026, 1, 7), 1.0, 0.0, 0.1),
    ]  # osuu
    pc = prospective_calibration(history, sc, date(2026, 6, 1))
    assert pc["n_forecasts"] == 1 and pc["forecasts"][0]["run_id"] == "wed"
    assert pc["pooled_share_inside_p10_p90"] == 1.0


def test_prospective_calibration_without_history():
    from forecaster.models.forecaster import prospective_calibration

    pc = prospective_calibration([], make_scores(), AS_OF)
    assert pc["n_forecasts"] == 0 and pc["n_pairs"] == 0
    assert pc["pooled_share_inside_p10_p90"] is None
    # Saman päivän aiempi ajo ei ole "aiempi ennuste" (katselmointi A30)
    same_day = _stored_forecast("today", AS_OF, 0.0, 0.0, 0.1)
    assert prospective_calibration([same_day], make_scores(), AS_OF)["n_forecasts"] == 0


def test_small_sample_invalid_value():
    with pytest.raises(ValueError, match="small_sample"):
        capability_forecast(make_scores(), ForecastConfig(n_boot=60, small_sample="x"), AS_OF)


@pytest.mark.slow
def test_small_sample_inflate_improves_coverage_on_sparse_frontier():
    """A27 Monte Carlo: ~9 pisteen rintamalla ennustevälin (P10–P90) kattavuus tuleville
    rintamapisteille on ilman korjausta liian pieni (~0.72, 250 toistoa) ja inflate nostaa
    sen lähelle 0.80:aa. Parittaiset siemenet → ero on vakaa jo 60 toistolla."""
    cut = add_months(AS_OF, -12)
    shares = {"none": [], "inflate": []}
    for s in range(60):
        sc = _sparse_scores(s)
        for mode in shares:
            try:
                bt = backtest_capability(sc, ForecastConfig(n_boot=150, seed=s, small_sample=mode),
                                         AS_OF, cutoff=cut)  # fmt: skip
            except ValueError:
                continue
            if bt["share_inside_p10_p90"] is not None:
                shares[mode].append(bt["share_inside_p10_p90"])
    none, infl = np.mean(shares["none"]), np.mean(shares["inflate"])
    assert infl - none > 0.03, (none, infl)
    assert 0.72 <= infl <= 0.9, infl


def test_too_few_frontier_points():
    with pytest.raises(ValueError, match="Rintamassa"):
        capability_forecast(make_scores(n=2), CFG, AS_OF)


def test_deterministic():
    a = capability_forecast(make_scores(), CFG, AS_OF)
    b = capability_forecast(make_scores(), CFG, AS_OF)
    assert a["horizons"] == b["horizons"]


def test_rolling_backtest_calibrated_on_linear_data():
    """A24: oikein määritellyllä datalla liukuvan backtestin kattavuus ≈ 0.8 ja harha ≈ 0."""
    from forecaster.models.forecaster import backtest_capability_rolling

    pooled, errs = [], []
    for s in range(6):
        bt = backtest_capability_rolling(make_scores(slope=1.2, noise=0.1, seed=s),
                                         ForecastConfig(n_boot=120, seed=s), AS_OF)  # fmt: skip
        assert bt["n_origins_ok"] == 4
        assert [o["cutoff"] for o in bt["origins"]] == [
            add_months(AS_OF, -k) for k in (6, 12, 18, 24)
        ]
        # k = 6 < holdout → arvioidaan vain nykyhetkeen asti
        assert bt["origins"][0]["end"] == AS_OF
        assert bt["origins"][3]["end"] == add_months(AS_OF, -12)
        pooled.append(bt["pooled_share_inside_p10_p90"])
        errs.append(bt["mean_error"])
    # 6 siemenen keskiarvon SE ≈ 0.04 (math-reviewer) → väli 0.7–0.9 on perusteltu
    assert 0.7 <= np.mean(pooled) <= 0.9, np.mean(pooled)
    assert abs(np.mean(errs)) < 0.1, np.mean(errs)


def test_rolling_backtest_month_end_as_of():
    """Kuun viimeinen päivä: arviointi-ikkuna ulottuu as_of-päivään asti (ei ketjutettua
    add_months-leikkausta 31.3. → 30.9. → 30.3.)."""
    from forecaster.models.forecaster import backtest_capability_rolling

    as_of = date(2026, 3, 31)
    bt = backtest_capability_rolling(make_scores(), ForecastConfig(n_boot=60, seed=0), as_of,
                                     origins_months=(6, 18))  # fmt: skip
    o6, o18 = bt["origins"]
    assert o6["cutoff"] == date(2025, 9, 30) and o6["end"] == as_of
    assert o18["cutoff"] == date(2024, 9, 30) and o18["end"] == date(2025, 9, 30)


def test_rolling_backtest_detects_structural_break():
    """Kiihtyvä kehitys viimeisen vuoden aikana: vanhoilta lähtöpäiviltä ennuste jää jälkeen
    (positiivinen virhe) ja kalibraatio heikkenee – yhden lähtöpäivän backtest voisi peittää
    tämän."""
    from forecaster.models.forecaster import backtest_capability_rolling

    cfg = ForecastConfig(n_boot=120, seed=0)
    lin = backtest_capability_rolling(make_scores(slope=1.2, noise=0.1), cfg, AS_OF)
    brk = backtest_capability_rolling(make_scores(slope=0.6, slope_recent=2.5, noise=0.1), cfg,
                                      AS_OF)  # fmt: skip
    assert brk["mean_error"] > 0.2
    assert brk["pooled_share_inside_p10_p90"] < lin["pooled_share_inside_p10_p90"]
    lo, hi = brk["origin_share_min_max"]
    assert hi - lo > 0.3  # kalibraatio riippuu lähtöpäivästä


def test_rolling_backtest_skips_origins_without_enough_data():
    from forecaster.models.forecaster import backtest_capability_rolling

    bt = backtest_capability_rolling(make_scores(), ForecastConfig(n_boot=60, seed=0), AS_OF,
                                     origins_months=(12, 45))  # fmt: skip
    # 45 kk ennen AS_OFia = helmikuu 2023: rintamassa ~1 piste sovitusikkunan alun jälkeen
    statuses = {o["origin_months"]: o["status"] for o in bt["origins"]}
    assert statuses[12] == "ok"
    assert statuses[45] in {"insufficient", "no_points"}
    assert bt["n_origins_ok"] == 1


def test_backtest_capability_on_linear_data():
    """Oikein määritellyllä datalla backtest on harhaton ja ennusteväli kattaa ~80 %."""
    shares, errs = [], []
    for s in range(8):
        bt = backtest_capability(make_scores(slope=1.2, noise=0.1, seed=s),
                                 ForecastConfig(n_boot=150, seed=s), AS_OF)  # fmt: skip
        assert bt["cutoff"] == add_months(AS_OF, -12)
        assert bt["n_realized_frontier_points"] > 0
        shares.append(bt["share_inside_p10_p90"])
        errs.append(bt["mean_error"])
    assert 0.6 <= np.mean(shares) <= 0.95, np.mean(shares)
    assert abs(np.mean(errs)) < 0.1, np.mean(errs)


# --- Kustannus -------------------------------------------------------------------


def cost_data(halving=8.0, increasing=False, seed=0):
    """Joka kuukausi uusi malli (θ = 1) hinnalla 10·2^(−t/halving); lisäksi halpa mutta
    heikko malli (θ = −2), jota ei saa valita tasolla 0."""
    rng = np.random.default_rng(seed)
    months = month_starts(START, AS_OF)
    models, price_rows = [], []
    for k, m in enumerate(months[::3]):
        t = (m - START).days / (365.25 / 12)
        c = 10 * 2 ** ((t if increasing else -t) / halving) * np.exp(rng.normal(0, 0.05))
        mid = f"m{k:02d}"
        models.append((mid, m, 1.0, 0.05))
        price_rows += [(at, mid, float(c)) for at in months if at >= m]
    models.append(("weak", START, -2.0, 0.05))
    price_rows += [(at, "weak", 0.001) for at in months]
    scores = pl.DataFrame(models, schema=["model_id", "release_date", "theta", "theta_se"],
                          orient="row")  # fmt: skip
    prices = pl.DataFrame(price_rows, schema=["at", "model_id", "blended"], orient="row")
    return scores, prices


def test_halving_time_recovered():
    scores, prices = cost_data(halving=8.0)
    fc = cost_forecast(scores, prices, 0.0, CFG, AS_OF)
    assert fc["status"] == "ok"
    assert fc["halving_months"] == pytest.approx(8.0, rel=0.15)
    lo, mid, hi = fc["halving_months_p10_p50_p90"]
    assert lo < mid < hi
    assert all(r["model_id"] != "weak" for r in fc["monthly_cheapest"])  # θ < taso
    # Sovitus ennätyksiin, ei toistuviin kuukausiarvoihin (A16)
    assert len(fc["record_lows"]) < len(fc["monthly_cheapest"])


def test_halving_interval_coverage_over_seeds():
    """Harhaton piste-estimaatti, P10–P90 kattaa todellisen arvon useimmiten.
    (Mitattu 20 siemenellä: kattavuus ~0.70 – väli on hieman kapea, raportoitu A16:ssa.)"""
    cover, points = [], []
    for s in range(10):
        sc, pr = cost_data(halving=8.0, seed=s)
        fc = cost_forecast(sc, pr, 0.0, ForecastConfig(n_boot=150, seed=s), AS_OF)
        lo, _, hi = fc["halving_months_p10_p50_p90"]
        cover.append(lo <= 8.0 <= hi)
        points.append(fc["halving_months"])
    assert np.mean(cover) >= 0.6
    assert np.mean(points) == pytest.approx(8.0, rel=0.05)


def plateau_data(floor_after_months=24, halving=4.0, seed=0):
    """Hinta puoliintuu `halving` kk:n välein, kunnes pysähtyy lattiaan."""
    rng = np.random.default_rng(seed)
    months = month_starts(START, AS_OF)
    models, rows = [], []
    for k, m in enumerate(months[::2]):
        t = min((m - START).days / (365.25 / 12), floor_after_months)
        c = 10 * 2 ** (-t / halving) * np.exp(rng.normal(0, 0.05))
        mid = f"p{k:02d}"
        models.append((mid, m, 1.0, 0.05))
        rows += [(at, mid, float(c)) for at in months if at >= m]
    scores = pl.DataFrame(models, schema=["model_id", "release_date", "theta", "theta_se"],
                          orient="row")  # fmt: skip
    return scores, pl.DataFrame(rows, schema=["at", "model_id", "blended"], orient="row")


def test_plateau_detected_and_forecast_stays_near_floor():
    """Lasku pysähtyi ~22 kk sitten → palainen malli valitaan, eikä ennuste ekstrapoloi
    vanhaa jyrkkää laskua (backtestissä ~100x virhe ennen korjausta)."""
    scores, prices = plateau_data()
    fc = cost_forecast(scores, prices, 0.0, CFG, AS_OF)
    floor = fc["current_cheapest"]["cost"]
    assert fc["model"] == "piecewise"
    assert fc["historical_halving_months"] is not None
    # Lattia = sarjan oma halvin hinta → taso on lattialla (tai vähintään laskun pysähtynyt)
    assert fc["status"] in {"plateau", "at_floor", "ok"}
    if fc["status"] == "ok":  # hidas lasku on sallittu, mutta paljon historiallista hitaampi
        assert fc["halving_months"] > 5 * fc["historical_halving_months"]
    h12 = next(h for h in fc["horizons"] if h["months"] == 12)["cost_p10_p50_p90"][1]
    assert floor / 3 < h12 < floor * 3
    bt = backtest_cost(scores, prices, 0.0, CFG, AS_OF)
    assert abs(bt["log_error"]) < math.log(3)


def test_forecast_bounded_by_floor_and_current_price():
    """Päätös 2026-11-14: ennusteet ∈ [markkinan lattia, nykyinen halvin]. cost_data:ssa
    heikko malli (0.001 $) on markkinan lattia; tason 0 sarja laskee jyrkästi."""
    scores, prices = cost_data(halving=3.0)
    fc = cost_forecast(scores, prices, 0.0, CFG, AS_OF)
    current = fc["current_cheapest"]["cost"]
    floor = fc["market_floor"]
    assert floor == pytest.approx(min(0.001, current))  # lattia = halvin millä tahansa tasolla
    for h in fc["horizons"]:
        lo, _, hi = h["cost_p10_p50_p90"]
        assert lo >= floor - 1e-12 and hi <= current + 1e-9
    # Vaihtoehto, jossa taso ei ole lattialla: hidas lasku, kallis aloitus
    scores2, prices2 = cost_data(halving=12.0)
    fc2 = cost_forecast(scores2, prices2, 0.0, CFG, AS_OF)
    assert fc2["status"] == "ok" and not fc2["at_floor"]
    cur2 = fc2["current_cheapest"]["cost"]
    assert fc2["months_to_floor"] == pytest.approx(
        math.log(cur2 / fc2["market_floor"]) / fc2["alpha1_per_month"]
    )
    for h in fc2["horizons"]:
        lo, _, hi = h["cost_p10_p50_p90"]
        assert lo >= fc2["market_floor"] - 1e-12 and hi <= cur2 + 1e-9


LEVEL = ForecastConfig(n_boot=200, seed=1, level_floor=True)


def _with_near_model(scores, prices, theta: float, cost: float):
    """Lisää (1) lähitason mallin (θ = theta) kiinteällä hinnalla vuoden 2025 alusta ja
    (2) hinnattomat rintamamallit, jotka nousevat 1.5 θ/v (A26:n kehitysvauhti β)."""
    start = date(2025, 1, 1)
    frontier = [(f"fr{k:02d}", START + timedelta(days=60 * k), 1.2 + 1.5 * 60 * k / 365.25, 0.05)
                for k in range(23)]  # fmt: skip
    sc = pl.concat([scores, pl.DataFrame([("near", start, theta, 0.05), *frontier],
                                         schema=scores.schema, orient="row")])  # fmt: skip
    rows = [(m, "near", cost) for m in prices["at"].unique().sort() if m >= start]
    pr = pl.concat([prices, pl.DataFrame(rows, schema=prices.schema, orient="row")])
    return sc, pr


def _h(fc, months):
    return next(h for h in fc["horizons"] if h["months"] == months)


def test_level_floor_is_horizon_specific():
    """A26: h kk:n alaraja = halvin malli, jolle taso − β·h/12 ≤ θ < taso. β ≈ 1.5 θ/v →
    δ_6 ≈ 0.75, δ_12 ≈ 1.5: lähimalli θ −1.2 on mukana vasta 12 kk:n horisontilla."""
    scores, prices = cost_data(halving=12.0)  # heikko malli (θ −2) maksaa 0.001
    current = cost_forecast(scores, prices, 0.0, CFG, AS_OF)["current_cheapest"]["cost"]
    sc, pr = _with_near_model(scores, prices, theta=-1.2, cost=current / 4)
    fc = cost_forecast(sc, pr, 0.0, LEVEL, AS_OF)
    assert fc["frontier_slope_per_year"] == pytest.approx(1.5, abs=0.15)
    assert _h(fc, 6)["floor_scope"] == "market" and _h(fc, 6)["floor"] == pytest.approx(0.001)
    h12 = _h(fc, 12)
    assert h12["floor_scope"] == "level" and h12["floor"] == pytest.approx(current / 4)
    assert h12["cost_p10_p50_p90"][0] >= current / 4 - 1e-12
    # 18 ja 24 kk: δ ≈ 2.25 / 3 → myös heikko malli (θ −2, 0.001) on lähitasoa
    for m in (18, 24):
        assert _h(fc, m)["floor_scope"] == "level" and _h(fc, m)["floor"] == pytest.approx(0.001)
    assert fc["market_floor"] == pytest.approx(0.001)  # at_floor aina koko markkinasta
    # Asetus pois → koko markkina kaikilla horisonteilla
    fc_m = cost_forecast(sc, pr, 0.0, ForecastConfig(n_boot=200, seed=1), AS_OF)
    assert all(h["floor_scope"] == "market" for h in fc_m["horizons"])


@pytest.mark.parametrize("ratio", [0.99, 0.8])
def test_level_floor_does_not_freeze_forecast(ratio):
    """Katselmoinnin vakava löydös: lähimalli HIEMAN halvempi kuin tason nykyinen →
    ennuste ei saa jäätyä eikä tila muuttua at_flooriksi (lyhyet horisontit = rajaamaton)."""
    scores, prices = cost_data(halving=12.0)
    current = cost_forecast(scores, prices, 0.0, CFG, AS_OF)["current_cheapest"]["cost"]
    sc, pr = _with_near_model(scores, prices, theta=-0.5, cost=current * ratio)
    fc = cost_forecast(sc, pr, 0.0, LEVEL, AS_OF)
    assert fc["status"] == "ok" and not fc["at_floor"]
    for m in (6, 12):
        x = _h(fc, m)
        assert x["floor_scope"] == "market"
        assert x["cost_p10_p50_p90"][1] == pytest.approx(x["unclipped_cost_p10_p50_p90"][1])
        assert x["cost_p10_p50_p90"][1] < 0.9 * current  # hinta jatkaa laskuaan


def test_level_floor_uses_given_frontier_slope():
    """A29: annettu kehitysvauhti (kyvykkyysennusteen mediaani) ohjaa lähitason lattiaa:
    hitaalla vauhdilla (0.5 θ/v → δ_12 = 0.5) lähimalli θ −1.2 jää 12 kk:n joukon ulkopuolelle."""
    scores, prices = cost_data(halving=12.0)
    current = cost_forecast(scores, prices, 0.0, CFG, AS_OF)["current_cheapest"]["cost"]
    sc, pr = _with_near_model(scores, prices, theta=-1.2, cost=current / 4)
    fast = cost_forecast(sc, pr, 0.0, LEVEL, AS_OF)  # OLS-varakeino ≈ 1.5 θ/v
    slow = cost_forecast(sc, pr, 0.0, LEVEL, AS_OF, frontier_slope=0.5)
    assert slow["frontier_slope_per_year"] == 0.5
    assert _h(fast, 12)["floor_scope"] == "level" and _h(slow, 12)["floor_scope"] == "market"


def test_backtest_cost_floor_slope_uses_only_training_data():
    """A29: backtestin lattian kehitysvauhti lasketaan kyvykkyysennusteesta vain cutoffiin
    asti julkaistulla datalla: cutoffin jälkeinen huippumalli ei muuta sitä."""
    scores, prices = cost_data(halving=12.0)
    current = cost_forecast(scores, prices, 0.0, CFG, AS_OF)["current_cheapest"]["cost"]
    sc, pr = _with_near_model(scores, prices, theta=-1.2, cost=current / 4)
    cfg = ForecastConfig(n_boot=150, seed=1, level_floor=True, fit_windows_months=PROD_WINDOWS)
    a = backtest_cost(sc, pr, 0.0, cfg, AS_OF)
    star = pl.DataFrame([("star", add_months(AS_OF, -3), 25.0, 0.05)], schema=sc.schema,
                        orient="row")  # fmt: skip
    b = backtest_cost(pl.concat([sc, star]), pr, 0.0, cfg, AS_OF)
    assert a["trained_frontier_slope_source"] == "capability_median"
    assert a["trained_frontier_slope"] == b["trained_frontier_slope"]


def test_level_at_floor_reported():
    scores, prices = cost_data()
    # Taso −5: halvin (heikko) malli on itse lattia
    fc = cost_forecast(scores, prices, -5.0, CFG, AS_OF)
    assert fc["status"] in {"at_floor", "insufficient"}


def test_not_declining_gives_undefined_halving():
    scores, prices = cost_data(increasing=True)
    fc = cost_forecast(scores, prices, 0.0, CFG, AS_OF)
    assert fc["halving_months"] is None
    # Nousevilla hinnoilla vain ensimmäinen kuukausi on ennätys → ei riittävästi pisteitä
    assert fc["status"] in {"insufficient", "not_declining"}


def test_low_target_uses_cheap_weak_model():
    scores, prices = cost_data()
    fc = cost_forecast(scores, prices, -5.0, CFG, AS_OF)
    assert fc["monthly_cheapest"][0]["model_id"] == "weak"


def test_backtest_cost():
    scores, prices = cost_data(halving=8.0)
    bt = backtest_cost(scores, prices, 0.0, CFG, AS_OF)
    assert bt["status"] == "ok"
    assert abs(bt["log_error"]) < 0.5
    # Rajaamaton virhe raportoidaan erikseen (katselmointi K5)
    assert bt["unclipped_log_error"] is not None
    assert abs(bt["unclipped_log_error"]) < 0.5
    assert bt["p50_at_floor"] is False


# --- Katselmoinnin (K5) regressiotestit ------------------------------------------------


@pytest.mark.parametrize(
    ("d", "n", "expected"),
    [
        (date(2026, 10, 31), 6, date(2027, 4, 30)),
        (date(2026, 1, 30), 1, date(2026, 2, 28)),
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2026, 11, 14), -12, date(2025, 11, 14)),
        (date(2026, 3, 31), -1, date(2026, 2, 28)),
    ],
)
def test_add_months_month_end(d, n, expected):
    assert add_months(d, n) == expected


def test_halving_percentiles_include_non_declining_draws():
    """T½-persentiilit KAIKISTA drawista: ei-laskeva draw = ∞ → None, ei pudoteta pois."""
    from forecaster.models.forecaster import _pct_unbounded

    x = np.array([5.0] * 5 + [np.inf] * 95)
    lo, mid, hi = _pct_unbounded(x)
    assert lo is None and mid is None and hi is None  # 95 % ei laske → kaikki ∞
    x2 = np.array([4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, np.inf, np.inf])
    lo, mid, hi = _pct_unbounded(x2)
    assert lo == 4.0 and mid is not None and hi is None


def test_capability_model_selection_per_draw():
    """Mallivalinta joka drawissa: kiihtyvä data → enemmistö palaisia, lineaarinen → vähemmistö."""
    lin = capability_forecast(make_scores(slope=1.2), CFG, AS_OF)
    acc = capability_forecast(make_scores(slope=0.6, slope_recent=2.5), CFG, AS_OF)
    assert lin["share_piecewise_draws"] < 0.3
    assert acc["share_piecewise_draws"] > 0.5
