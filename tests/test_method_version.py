"""6.1 (A34): ennustemenetelmän sormenjälki.

Kiinteällä synteettisellä datalla ja siemenellä lasketut avainluvut sekä menetelmäparametrit
(`config/forecast.yaml`:n menetelmäkentät ja koodin oletusarvot) on tallennettu tähän
menetelmäversioittain. Jos testi kaatuu, ennusteen tulos muuttui samalla datalla eli
menetelmä muuttui. Silloin:
  1. nosta forecaster.models.forecaster.METHOD_VERSION,
  2. lisää rivi TOTEUTUSSUUNNITELMA.md:n menetelmäversiotaulukkoon (peruste, validointi),
  3. lisää uusi avain FINGERPRINTS-sanakirjaan (aja: python tests/test_method_version.py).
     Vanhojen versioiden tietueita ei muokata: ne näkyvät diffissä historiana.
Myös satunnaislukujen kulutusjärjestyksen muutos nostaa version, koska toteumaseuranta
vertaa tarkkoja kaistoja. Poikkeus: jos testi kaatuu riippuvuuden (NumPy, SciPy,
statsmodels) päivityksen jälkeen ilman menetelmäkoodin muutosta, kyse ei ole
menetelmämuutoksesta (NumPyn Generator-virta voi muuttua versioiden välillä); tarkista
erikseen ja kirjaa havainto taulukkoon. Toleranssi sallii alustojen väliset liukulukuerot.
"""

import inspect
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from forecaster.config import get_paths, load_forecast
from forecaster.models import forecaster as fm
from forecaster.models.forecaster import (
    METHOD_VERSION,
    ForecastConfig,
    add_months,
    capability_forecast,
    cost_forecast,
    debias_capability,
    month_starts,
    selection_bias,
)

AS_OF = date(2026, 11, 1)
START = date(2023, 1, 1)
ROOT = Path(__file__).resolve().parents[1]


def method_params() -> dict:
    """Menetelmään vaikuttavat asetukset: YAML-kentät ja koodin oletusarvot."""
    ff = load_forecast(get_paths(ROOT))
    sb = inspect.signature(selection_bias).parameters
    return {
        "fit_start": str(ff.fit_start),
        "horizons_months": list(ff.horizons_months),
        "n_boot": ff.n_boot,
        "seed": ff.seed,
        "piecewise_window_months": ff.piecewise_window_months,
        "piecewise_alpha": ff.piecewise_alpha,
        "level_floor": ff.level_floor,
        "small_sample": ff.small_sample,
        "small_sample_max_points": ff.small_sample_max_points,
        "fit_windows_months": list(ff.fit_windows_months),
        "max_theta_se": ff.max_theta_se,
        "min_points": ForecastConfig().min_points,
        "floor_tolerance": fm.FLOOR_TOLERANCE,
        "selection_bias_n_sim": sb["n_sim"].default,
        "selection_bias_inner_boot": sb["inner_boot"].default,
    }


def _cfg() -> ForecastConfig:
    """Tuotannon asetukset YAMLista (testin nopeuden vuoksi n_boot 200, horisontit 6/12/24)."""
    p = method_params()
    return ForecastConfig(
        fit_start=date.fromisoformat(p["fit_start"]), horizons_months=(6, 12, 24), n_boot=200,
        seed=0, piecewise_window_months=p["piecewise_window_months"],
        piecewise_alpha=p["piecewise_alpha"], level_floor=p["level_floor"],
        small_sample=p["small_sample"], small_sample_max_points=p["small_sample_max_points"],
        fit_windows_months=tuple(p["fit_windows_months"]),
    )  # fmt: skip


def _scores(n: int = 90, seed: int = 42) -> pl.DataFrame:
    """Hidas alkujakso ja kiihtyminen (sarana ja ikkunat aktiivisia), mittausvirhe."""
    rng = np.random.default_rng(seed)
    span = (AS_OF - START).days
    t_break = (add_months(AS_OF, -20) - START).days / 365.25
    rows = []
    for k in range(n):
        d = START + timedelta(days=int(rng.uniform(0, span)))
        t = (d - START).days / 365.25
        mu = 0.5 + 0.6 * min(t, t_break) + 1.8 * max(0.0, t - t_break)
        th = mu + rng.normal(0, 0.25) - abs(rng.normal(0, 0.8))
        rows.append((f"m{k:03d}", d, th + rng.normal(0, 0.15), 0.15))
    return pl.DataFrame(rows, schema=["model_id", "release_date", "theta", "theta_se"],
                        orient="row")  # fmt: skip


def _prices_floor(scores: pl.DataFrame) -> pl.DataFrame:
    """Taso päätyy markkinan lattialle (testaa leikkauksen ja at_floor-tilan)."""
    months = month_starts(START, AS_OF)
    rows = []
    for mid, rel, th, _ in scores.iter_rows():
        base = 20 * 2 ** (-((rel - START).days / 30.44) / 9) * np.exp(0.6 * th)
        rows += [(m, mid, float(base)) for m in months if m >= rel]
    return pl.DataFrame(rows, schema=["at", "model_id", "blended"], orient="row")


def _prices_trend(scores: pl.DataFrame) -> pl.DataFrame:
    """Hinta laskee kalenteriajan mukaan ja kiihtyy saranan jälkeen; jyrkkä kyvykkyysvaikutus
    → tila ok, palainen malli, lähitason lattia (A26) kaikilla horisonteilla."""
    months = month_starts(START, AS_OF)
    kn = (add_months(AS_OF, -12) - START).days / 30.44
    rows = []
    for mid, rel, th, _ in scores.iter_rows():
        for m in months:
            if m >= rel:
                t = (m - START).days / 30.44
                lg = -(min(t, kn) / 14 + max(0.0, t - kn) / 5) * np.log(2)
                rows.append((m, mid, float(20 * np.exp(lg) * np.exp(1.2 * th))))
    return pl.DataFrame(rows, schema=["at", "model_id", "blended"], orient="row")


def _r(v):
    if v is None or isinstance(v, (str, bool)):
        return v
    return round(float(v), 6)


def _band(prefix: str, band, out: dict) -> None:
    for q, v in zip((10, 50, 90), band, strict=True):
        out[f"{prefix}_p{q}"] = _r(v)


def _capability(out: dict, tag: str, sc: pl.DataFrame, cfg: ForecastConfig) -> dict:
    cap = capability_forecast(sc, cfg, AS_OF, eval_dates=[add_months(AS_OF, 3)])
    out[f"{tag}_model"] = cap["model"]
    out[f"{tag}_n_frontier"] = cap["n_frontier"]
    out[f"{tag}_small_sample_applied"] = cap["small_sample_applied"]
    _band(f"{tag}_slope", cap["slope_per_year_p10_p50_p90"], out)
    for h in cap["horizons"]:
        _band(f"{tag}_pred_{h['months']}m", h["prediction_p10_p50_p90"], out)
    _band(f"{tag}_eval_3m", cap["eval"][0]["prediction_p10_p50_p90"], out)
    return cap


def _cost(out: dict, tag: str, fc: dict) -> None:
    for k in ("status", "model", "halving_months", "months_to_floor"):
        out[f"{tag}_{k}"] = _r(fc.get(k))
    _band(f"{tag}_halving", fc.get("halving_months_p10_p50_p90") or [None] * 3, out)
    for x in fc.get("horizons", []):
        h = x["months"]
        _band(f"{tag}_{h}m", x["cost_p10_p50_p90"], out)
        _band(f"{tag}_{h}m_unclipped", x.get("unclipped_cost_p10_p50_p90") or [None] * 3, out)
        out[f"{tag}_{h}m_floor"] = _r(x.get("floor"))
        out[f"{tag}_{h}m_floor_scope"] = x.get("floor_scope")


def compute() -> dict:
    cfg = _cfg()
    out: dict = {}
    sc = _scores()
    cap = _capability(out, "main", sc, cfg)
    sb = selection_bias(sc, cfg, AS_OF, n_sim=4, inner_boot=40)
    out["bias_slope_per_year"] = _r(sb["slope_bias_per_year"])
    out["bias_current_frontier"] = _r(sb["current_frontier_bias"])
    true = debias_capability(cap, sb)
    out["true_12m_p50"] = _r(next(t for t in true if t["months"] == 12)
                             ["prediction_p10_p50_p90"][1])  # fmt: skip
    # Pieni rintama (< small_sample_max_points): pienen otoksen korjaus koko jakson draweissa
    _capability(out, "small", _scores(n=28, seed=7), cfg)
    level = float(sc.filter(pl.col("release_date") <= add_months(AS_OF, -12))["theta"].max())
    _cost(out, "cost_floor", cost_forecast(sc, _prices_floor(sc), level, cfg, AS_OF,
                                           cap["slope_per_year"]))  # fmt: skip
    _cost(out, "cost_trend", cost_forecast(sc, _prices_trend(sc), 2.0, cfg, AS_OF,
                                           cap["slope_per_year"]))  # fmt: skip
    return out


# Menetelmäversio → {"params": method_params(), "values": compute()}. Vanhoja ei muokata.
FINGERPRINTS: dict[str, dict] = {
    "1": {
        "params": {
            "fit_start": "2023-01-01",
            "horizons_months": [6, 12, 18, 24],
            "n_boot": 1000,
            "seed": 0,
            "piecewise_window_months": 12,
            "piecewise_alpha": 0.05,
            "level_floor": True,
            "small_sample": "auto",
            "small_sample_max_points": 12,
            "fit_windows_months": [18, 24, 36],
            "max_theta_se": 1.0,
            "min_points": 4,
            "floor_tolerance": 1.5,
            "selection_bias_n_sim": 30,
            "selection_bias_inner_boot": 100,
        },
        "values": {
            "main_model": "piecewise",
            "main_n_frontier": 15,
            "main_small_sample_applied": False,
            "main_slope_p10": 1.024689,
            "main_slope_p50": 1.509662,
            "main_slope_p90": 2.063026,
            "main_pred_6m_p10": 4.445323,
            "main_pred_6m_p50": 5.168281,
            "main_pred_6m_p90": 5.791666,
            "main_pred_12m_p10": 4.976724,
            "main_pred_12m_p50": 5.931865,
            "main_pred_12m_p90": 6.896122,
            "main_pred_24m_p10": 5.967532,
            "main_pred_24m_p50": 7.440139,
            "main_pred_24m_p90": 8.960061,
            "main_eval_3m_p10": 4.071081,
            "main_eval_3m_p50": 4.788875,
            "main_eval_3m_p90": 5.208298,
            "bias_slope_per_year": -0.127389,
            "bias_current_frontier": -0.00701,
            "true_12m_p50": 6.096493,
            "small_model": "piecewise",
            "small_n_frontier": 8,
            "small_small_sample_applied": True,
            "small_slope_p10": 0.913146,
            "small_slope_p50": 1.259966,
            "small_slope_p90": 2.512869,
            "small_pred_6m_p10": 4.003666,
            "small_pred_6m_p50": 4.982184,
            "small_pred_6m_p90": 6.19395,
            "small_pred_12m_p10": 4.416519,
            "small_pred_12m_p50": 5.584414,
            "small_pred_12m_p90": 7.440749,
            "small_pred_24m_p10": 5.308513,
            "small_pred_24m_p50": 6.794774,
            "small_pred_24m_p90": 9.91236,
            "small_eval_3m_p10": 3.720701,
            "small_eval_3m_p50": 4.61357,
            "small_eval_3m_p90": 5.638729,
            "cost_floor_status": "at_floor",
            "cost_floor_model": "linear",
            "cost_floor_halving_months": 15.841171,
            "cost_floor_months_to_floor": None,
            "cost_floor_halving_p10": 9.796029,
            "cost_floor_halving_p50": 13.627505,
            "cost_floor_halving_p90": 21.18696,
            "cost_floor_6m_p10": 3.160256,
            "cost_floor_6m_p50": 3.160256,
            "cost_floor_6m_p90": 3.82108,
            "cost_floor_6m_unclipped_p10": 2.344433,
            "cost_floor_6m_unclipped_p50": 3.13942,
            "cost_floor_6m_unclipped_p90": 3.964017,
            "cost_floor_6m_floor": 3.160256,
            "cost_floor_6m_floor_scope": "market",
            "cost_floor_12m_p10": 3.160256,
            "cost_floor_12m_p50": 3.160256,
            "cost_floor_12m_p90": 3.165823,
            "cost_floor_12m_unclipped_p10": 1.534859,
            "cost_floor_12m_unclipped_p50": 2.313125,
            "cost_floor_12m_unclipped_p90": 3.246253,
            "cost_floor_12m_floor": 3.160256,
            "cost_floor_12m_floor_scope": "market",
            "cost_floor_24m_p10": 3.160256,
            "cost_floor_24m_p50": 3.160256,
            "cost_floor_24m_p90": 3.160256,
            "cost_floor_24m_unclipped_p10": 0.658862,
            "cost_floor_24m_unclipped_p50": 1.257025,
            "cost_floor_24m_unclipped_p90": 2.274401,
            "cost_floor_24m_floor": 3.160256,
            "cost_floor_24m_floor_scope": "market",
            "cost_trend_status": "ok",
            "cost_trend_model": "piecewise",
            "cost_trend_halving_months": 4.836579,
            "cost_trend_months_to_floor": 29.313362,
            "cost_trend_halving_p10": 4.658771,
            "cost_trend_halving_p50": 5.000411,
            "cost_trend_halving_p90": 5.000411,
            "cost_trend_6m_p10": 3.202224,
            "cost_trend_6m_p50": 3.202224,
            "cost_trend_6m_p90": 3.633054,
            "cost_trend_6m_unclipped_p10": 2.771483,
            "cost_trend_6m_unclipped_p50": 3.167609,
            "cost_trend_6m_unclipped_p90": 3.633054,
            "cost_trend_6m_floor": 3.202224,
            "cost_trend_6m_floor_scope": "level",
            "cost_trend_12m_p10": 1.325792,
            "cost_trend_12m_p50": 1.370268,
            "cost_trend_12m_p90": 1.536745,
            "cost_trend_12m_unclipped_p10": 1.153194,
            "cost_trend_12m_unclipped_p50": 1.370268,
            "cost_trend_12m_unclipped_p90": 1.536745,
            "cost_trend_12m_floor": 1.325792,
            "cost_trend_12m_floor_scope": "level",
            "cost_trend_24m_p10": 0.249794,
            "cost_trend_24m_p50": 0.258767,
            "cost_trend_24m_p90": 0.278563,
            "cost_trend_24m_unclipped_p10": 0.199811,
            "cost_trend_24m_unclipped_p50": 0.258767,
            "cost_trend_24m_unclipped_p90": 0.278563,
            "cost_trend_24m_floor": 0.249794,
            "cost_trend_24m_floor_scope": "level",
        },
    }
}


def test_fingerprint_for_current_version_exists():
    assert METHOD_VERSION in FINGERPRINTS, (
        f"METHOD_VERSION {METHOD_VERSION} puuttuu FINGERPRINTS-sanakirjasta: aja tämä tiedosto "
        "ja lisää uusi avain (vanhoja ei muokata)"
    )
    assert max(FINGERPRINTS, key=lambda s: (len(s), s)) == METHOD_VERSION


def test_method_params_match_fingerprint():
    """YAMLin menetelmäkentän tai koodin oletusarvon muutos on menetelmämuutos."""
    assert method_params() == FINGERPRINTS[METHOD_VERSION]["params"], (
        "Menetelmäparametrit muuttuivat: nosta METHOD_VERSION ja lisää uusi sormenjälki"
    )


def test_method_values_match_fingerprint():
    got, exp = compute(), FINGERPRINTS[METHOD_VERSION]["values"]
    assert got.keys() == exp.keys()

    def differs(e, g):
        if e is None or g is None or isinstance(e, (str, bool)):
            return e != g
        return g != pytest.approx(e, rel=1e-5, abs=1e-6)

    changed = {k: (exp[k], got[k]) for k in exp if differs(exp[k], got[k])}
    assert not changed, (
        f"Ennusteen tulos muuttui samalla datalla (menetelmä muuttui): {changed}. "
        "Nosta METHOD_VERSION, lisää rivi TOTEUTUSSUUNNITELMA.md:n menetelmäversiotaulukkoon "
        "ja uusi avain FINGERPRINTS-sanakirjaan. Ks. tiedoston docstring."
    )


def test_fingerprint_cases_exercise_method_paths():
    """Tapaukset kattavat olennaiset polut (muuten sormenjälki ei huomaisi muutoksia)."""
    v = FINGERPRINTS[METHOD_VERSION]["values"]
    assert v["main_model"] == "piecewise" and not v["main_small_sample_applied"]
    assert v["small_small_sample_applied"]
    assert v["cost_floor_status"] == "at_floor"
    assert v["cost_trend_status"] == "ok" and v["cost_trend_model"] == "piecewise"
    assert {v[f"cost_trend_{h}m_floor_scope"] for h in (6, 12, 24)} == {"level"}
    assert v["cost_trend_24m_p50"] > v["cost_trend_24m_floor"]  # P50 ei leikkaudu


if __name__ == "__main__":
    import json

    print(json.dumps({"params": method_params(), "values": compute()}, indent=4,
                     ensure_ascii=False))  # fmt: skip
