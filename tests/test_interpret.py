"""6.4 (A37): θ-ennusteen tulkinta vertailutesteillä ja mitattavuusraja."""

import math

import polars as pl
import pytest

from forecaster.dashboard.data import interpretation_table
from forecaster.models.forecast_run import _interpretation_section
from forecaster.models.interpret import (
    expected_score,
    interpret_forecast,
    measurable_ceiling,
    reference_label,
)


def _params(rows):
    return pl.DataFrame(
        rows,
        schema=["benchmark_id", "name", "domain", "discrimination_a", "difficulty_b",
                "guessing_floor", "n_models", "n_models_fit", "best_score"], orient="row",
    )  # fmt: skip


PARAMS = _params([
    ("easy", "Easy", "math", 2.0, 1.0, 0.25, 80, 80, 0.97),
    ("hard", "Hard", "agentic", 1.4, 6.0, 0.0, 20, 20, 0.40),
    ("harder_weak", "Weak", "agentic", 0.4, 7.0, 0.0, 30, 30, 0.30),  # a < 1 → ei rajaan
    ("harder_rare", "Rare", "agentic", 2.0, 6.5, 0.0, 9, 9, 0.20),    # < 15 mallia → ei rajaan
    ("mid", "Mid", "math", 1.5, 4.0, 0.0, 25, 25, 0.80),
])  # fmt: skip


def test_expected_score():
    assert expected_score(1.0, 2.0, 1.0, 0.25) == pytest.approx(0.625)
    assert expected_score(10.0, 1.0, 0.0, 0.0) == pytest.approx(1.0, abs=1e-4)
    assert expected_score(0.0, 1.0, 2.0, 0.2) > 0.2  # arvausraja alaraja


def test_measurable_ceiling_uses_well_measured_benchmarks():
    ce = measurable_ceiling(PARAMS, k=2.0, min_a=1.0, min_models=15)
    assert ce["benchmark_id"] == "hard"
    assert ce["theta"] == pytest.approx(6.0 + 2.0 / 1.4)
    assert ce["score_at_ceiling"] == pytest.approx(1 / (1 + math.exp(-2.0)))
    # Ehdokkaat järjestyksessä: hard 7.43, mid 5.33, easy 2.0
    assert [c["benchmark_id"] for c in ce["candidates"]] == ["hard", "mid", "easy"]
    # Löysemmillä ehdoilla harvoin ajettu testi nostaa rajaa
    assert measurable_ceiling(PARAMS, min_models=5)["benchmark_id"] == "harder_rare"
    assert measurable_ceiling(PARAMS, min_a=5.0) is None


def test_ceiling_counts_models_without_outliers():
    """Kynnys käyttää mallimäärää ilman poikkeamia (n_models_fit)."""
    p = PARAMS.with_columns(
        n_models_fit=pl.when(pl.col("benchmark_id") == "hard").then(12)
        .otherwise(pl.col("n_models_fit"))
    )  # fmt: skip
    assert measurable_ceiling(p)["benchmark_id"] == "mid"


def test_ceiling_score_uses_guessing_floor():
    """Rajalla odotettu tulos on c + (1 − c)·σ(k), ei σ(k), kun c > 0."""
    p = PARAMS.with_columns(
        guessing_floor=pl.when(pl.col("benchmark_id") == "hard").then(0.25)
        .otherwise(pl.col("guessing_floor"))
    )  # fmt: skip
    ce = measurable_ceiling(p)
    s = 1 / (1 + math.exp(-2.0))
    assert ce["c"] == 0.25
    assert ce["score_at_ceiling"] == pytest.approx(0.25 + 0.75 * s)


def test_domain_restricts_ceiling_and_references():
    """Osa-alueen θ tulkitaan vain sen omilla testeillä."""
    horizons = [{"months": 12, "prediction_p10_p50_p90": [5.0, 5.5, 6.0]}]
    it = interpret_forecast(horizons, 4.5, PARAMS, ["hard", "mid"], domain="math")
    assert it["ceiling"]["benchmark_id"] == "mid"
    assert it["beyond_ceiling_p50"] == [12]
    assert [r["benchmark_id"] for r in it["references"]] == ["mid"]
    assert it["missing_references"] == ["hard"]


def test_interpret_forecast_translation_and_beyond():
    horizons = [
        {"months": 6, "prediction_p10_p50_p90": [6.0, 6.5, 7.0]},
        {"months": 12, "prediction_p10_p50_p90": [6.8, 7.3, 7.9]},
        {"months": 24, "prediction_p10_p50_p90": [7.6, 8.4, 9.2]},
    ]
    it = interpret_forecast(horizons, 5.8, PARAMS, ["hard", "easy", "missing"])
    assert it["beyond_ceiling_p50"] == [24]  # raja ≈ 7.43
    assert it["beyond_ceiling_p90"] == [12, 24]
    assert it["missing_references"] == ["missing"]
    hard, easy = it["references"]
    assert hard["current"] == pytest.approx(expected_score(5.8, 1.4, 6.0, 0.0))
    assert hard["best_observed"] == pytest.approx(0.40)
    assert not hard["saturated"] and easy["saturated"]  # easy: raja 2.0 < 5.8
    for h, src in zip(hard["horizons"], horizons, strict=True):
        s = h["score_p10_p50_p90"]
        assert s[0] < s[1] < s[2]  # monotoninen käännös säilyttää persentiilit
        assert s[1] == pytest.approx(expected_score(src["prediction_p10_p50_p90"][1], 1.4, 6.0, 0))
    tbl = interpretation_table(it)
    assert tbl.columns == ["name", "best_observed", "current", "h6", "h12", "h24"]
    assert tbl["name"].to_list() == ["Hard", "Easy (kyllääntynyt)"]
    weak = interpret_forecast(horizons, 5.8, PARAMS, ["harder_weak"])["references"][0]
    assert weak["weak"] and reference_label(weak) == "Weak (heikosti erotteleva, a 0.40)"


def test_missing_percentile_and_exact_ceiling():
    """None-persentiili ei kaada; tasan rajalla oleva P50 ei ylitä (tiukka >)."""
    lim = 6.0 + 2.0 / 1.4
    horizons = [
        {"months": 6, "prediction_p10_p50_p90": [None, lim, None]},
        {"months": 12, "prediction_p10_p50_p90": [None, None, None]},
    ]
    it = interpret_forecast(horizons, 5.8, PARAMS, ["hard"])
    assert it["beyond_ceiling_p50"] == [] and it["beyond_ceiling_p90"] == []
    assert it["references"][0]["horizons"][1]["score_p10_p50_p90"] == [None, None, None]


def test_interpretation_report_section():
    horizons = [{"months": 12, "prediction_p10_p50_p90": [6.8, 7.6, 7.9]}]
    text = "\n".join(_interpretation_section(interpret_forecast(horizons, 5.8, PARAMS, ["hard"])))
    assert "Mitattavuusraja θ 7.43" in text and "Hard" in text
    assert "seuraavat ehdokkaat: Mid θ 5.33, Easy θ 2.00" in text
    assert "P50 ylittää rajan horisonteilla 12 kk" in text
    assert "| Hard | 40 % |" in text
    assert _interpretation_section(None) == []
    none = interpret_forecast(horizons, 5.8, PARAMS, [], min_a=5.0)  # ei rajaa, ei vertailuja
    assert none["ceiling"] is None and none["beyond_ceiling_p50"] == []
