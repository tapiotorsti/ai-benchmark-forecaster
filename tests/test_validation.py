"""6.3 (A36): θ-asteikon validointi synteettisellä datalla (tunnettu totuus)."""

from datetime import date

import numpy as np
import polars as pl
import pytest
from synthetic_db import pick_anchors

from forecaster.models.irt_engine import IRTConfig
from forecaster.models.quality import prune_sparse
from forecaster.models.synthetic import SyntheticSpec, generate
from forecaster.models.validation import (
    ValidationConfig,
    _fit_item,
    additive_baseline,
    assign_folds,
    cold_start,
    holdout_cv,
    summarize_cold_start,
    summarize_holdout,
    write_report,
)


@pytest.fixture(scope="module")
def synth():
    data = generate(SyntheticSpec(n_models=60, n_benchmarks=18, seed=3))
    obs = prune_sparse(data.obs, 3, 5).obs
    return {"data": data, "obs": obs, "irt": IRTConfig(anchors=pick_anchors(data))}


def test_assign_folds_keeps_training_above_pruning_limits(synth):
    cfg = ValidationConfig(folds=5, seed=1)
    o = assign_folds(synth["obs"], cfg)
    assert set(o["fold"].unique()) <= {-1, 0, 1, 2, 3, 4}
    assert o.filter(pl.col("fold") >= 0).height > 0.5 * o.height
    for f in range(cfg.folds):
        train = o.filter(pl.col("fold") != f)
        test = o.filter(pl.col("fold") == f)
        nm = dict(train.group_by("model_id").len().iter_rows())
        nb = dict(train.group_by("benchmark_id").len().iter_rows())
        assert all(nm.get(m, 0) >= cfg.min_model_obs for m in test["model_id"])
        assert all(nb.get(b, 0) >= cfg.min_benchmark_obs for b in test["benchmark_id"])
    assert o.equals(assign_folds(synth["obs"], cfg))  # toistettava


def test_holdout_irt_beats_baselines_on_2pl_data(synth):
    """Data on 2PL-mallista: IRT ennustaa pois jätetyt tulokset paremmin kuin additiivinen
    logit-malli (ei erottelukykyä) ja selvästi paremmin kuin testin keskiarvo."""
    pred = holdout_cv(synth["obs"], synth["irt"], ValidationConfig(folds=4, seed=0))
    s = summarize_holdout(pred)
    o = s["overall"]
    assert s["n"] == pred.height > 100
    assert s["n_folds"] == 4 and s["n_folds_not_converged"] == 0
    assert o["irt"]["rmse"] < o["additive"]["rmse"] < o["bench_mean"]["rmse"]
    assert o["irt"]["rmse"] < 0.6 * o["bench_mean"]["rmse"]
    assert all(o[k]["rmse_macro"] > 0 for k in o)
    # Palautuminen: lähes yhtä hyvä kuin oraakkeli (todelliset parametrit)
    d = synth["data"]
    pairs = pred.select("model_id", "benchmark_id").iter_rows()
    oracle = np.array([d.c[b] + (1 - d.c[b]) / (1 + np.exp(-d.a[b] * (d.theta[m] - d.b[b])))
                       for m, b in pairs])  # fmt: skip
    oracle_rmse = float(np.sqrt(np.mean((pred["y"].to_numpy() - oracle) ** 2)))
    assert o["irt"]["rmse"] < 1.25 * oracle_rmse, (o["irt"]["rmse"], oracle_rmse)
    # Kalibraatio: desiilien ennuste ≈ havaittu
    cal = s["calibration"]
    assert len(cal) == 10 and sum(c["n"] for c in cal) == s["n"]
    assert max(abs(c["pred_mean"] - c["obs_mean"]) for c in cal) < 0.08


def test_additive_baseline_recovers_additive_structure():
    """Puhtaasti additiivisella logit-datalla vertailukohta palauttaa pois jätetyn arvon."""
    rng = np.random.default_rng(0)
    m = {f"m{i}": rng.normal() for i in range(12)}
    d = {f"b{j}": rng.normal() for j in range(8)}
    rows = [(mi, bj, 1 / (1 + np.exp(-(0.3 + m[mi] + d[bj]))), 0.0)
            for mi in m for bj in d]  # fmt: skip
    df = pl.DataFrame(rows, schema=["model_id", "benchmark_id", "y", "guessing_floor"],
                      orient="row")  # fmt: skip
    test = df.filter((pl.col("model_id") == "m0") & (pl.col("benchmark_id") == "b0"))
    train = df.filter(~((pl.col("model_id") == "m0") & (pl.col("benchmark_id") == "b0")))
    pred = additive_baseline(train, test, ridge=1e-6)
    assert pred[0] == pytest.approx(test["y"][0], abs=0.01)


def test_additive_baseline_competitive_on_1pl_data():
    """1PL-datalla (sama erottelukyky kaikilla testeillä) vertailukohta on lähes yhtä hyvä
    kuin IRT: vertailukohta ei ole heikennetty (katselmointi A36)."""
    rng = np.random.default_rng(5)
    theta = {f"m{i:02d}": rng.normal(0, 1.2) for i in range(50)}
    bdiff = {f"b{j:02d}": rng.normal(0, 1) for j in range(14)}
    rows = []
    for m, th in theta.items():
        for b, bb in bdiff.items():
            if rng.random() < 0.55:
                p = 1 / (1 + np.exp(-1.3 * (th - bb)))
                y = float(np.clip(p + rng.normal(0, 0.05), 0.001, 0.999))
                rows.append((m, b, y, 1.0, 0.0))
    schema = ["model_id", "benchmark_id", "y", "weight", "guessing_floor"]
    obs = prune_sparse(pl.DataFrame(rows, schema=schema, orient="row"), 3, 5).obs
    top = obs.group_by("model_id").len().sort("len", descending=True)["model_id"][:2]
    anchors = {top[0]: theta[top[0]], top[1]: theta[top[1]]}
    pred = holdout_cv(obs, IRTConfig(anchors=anchors), ValidationConfig(folds=4, seed=0))
    o = summarize_holdout(pred)["overall"]
    assert o["additive"]["rmse"] < 1.1 * o["irt"]["rmse"], o
    assert o["additive"]["rmse"] < 0.6 * o["bench_mean"]["rmse"]


def test_fit_item_recovers_parameters():
    theta = np.linspace(-3, 3, 40)
    a, b, c = 1.7, 0.5, 0.25
    y = c + (1 - c) / (1 + np.exp(-a * (theta - b)))
    a_hat, b_hat, ok = _fit_item(theta, y, c, None)
    assert ok and a_hat == pytest.approx(a, rel=0.05) and b_hat == pytest.approx(b, abs=0.05)
    a_fix, b_fix, ok = _fit_item(theta, y, c, 1.0)  # vain b
    assert ok and a_fix == 1.0 and b_fix == pytest.approx(b, abs=0.3)


def test_cold_start_beats_calibration_mean(synth):
    obs = synth["obs"]
    big = obs.group_by("benchmark_id").len().sort("len", descending=True)["benchmark_id"][:3]
    cfg = ValidationConfig(n_cal=5, cal_draws=6, seed=0)
    cs = cold_start(obs, synth["irt"], cfg, benchmarks=big.to_list())
    s = summarize_cold_start(cs)
    assert s["n_benchmarks"] == 3 and cs.height == 3 * 6 and s["n_draws"] == 18
    assert s["n_irt_not_converged"] == 0 and s["n_item_fit_failed"] == 0
    assert s["p90_rmse_draws"]["2pl"] >= s["median_rmse"]["2pl"] * 0.5
    # Testikohtainen virta: yksittäisen testin arvonnat eivät riipu muista testeistä
    one = cold_start(obs, synth["irt"], cfg, benchmarks=[big[0]])
    assert (
        one["rmse_2pl"].to_list()
        == cs.filter(pl.col("benchmark_id") == big[0])["rmse_2pl"].to_list()
    )
    assert s["median_rmse"]["2pl"] < s["median_rmse"]["cal_mean"]
    assert s["share_2pl_better_than_mean"] >= 2 / 3


def test_validation_report(tmp_path, synth):
    pred = holdout_cv(synth["obs"], synth["irt"], ValidationConfig(folds=3, seed=0))
    dom = pl.DataFrame({"benchmark_id": synth["obs"]["benchmark_id"].unique().sort(),
                        "name": "x", "domain": "math"})  # fmt: skip
    hold = summarize_holdout(pred, dom)
    assert set(hold["by_domain"]) == {"math"}
    out = write_report(hold, {}, ValidationConfig(folds=3), tmp_path / "v.md",
                       as_of=date(2026, 1, 1), data_note="testi")  # fmt: skip
    text = out.read_text(encoding="utf-8")
    assert "IRT (2PL)" in text and "--no-cold-start" in text and "CC BY 4.0" in text
    assert "openrouter" not in text.lower() and "llm-prices" not in text.lower()
