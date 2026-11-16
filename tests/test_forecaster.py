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
