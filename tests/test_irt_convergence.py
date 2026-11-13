"""K2:n hyväksymiskriteerit: IRT-moottori palauttaa tunnetut parametrit synteettisestä datasta.

Kriteerit (TOTEUTUSSUUNNITELMA.md, K2):
- ≥ 50 mallia, ≥ 15 benchmarkia, 60 % puuttuvuus → corr(θ, θ̂) > 0.95, RMSE(b) < 0.3
- saturoituneen benchmarkin a ja b pysyvät äärellisinä
- ankkurit osuvat täsmälleen
- SE kalibroitu: ~80 % todellisista arvoista 80 %:n luottamusvälillä
- determinismi
"""

from datetime import date, timedelta

import numpy as np
import pytest

from forecaster.db.models import ModelRow
from forecaster.db.repository import Repository
from forecaster.models.irt_engine import IRTConfig, fit_and_store, fit_irt
from forecaster.models.synthetic import SyntheticSpec, generate

Z80 = 1.2816


def pick_anchors(data, k=2):
    """Kaksi paljon havaittua mallia θ-jakauman eri päistä, kiinnitettynä todelliseen arvoon."""
    counts = data.obs.group_by("model_id").len()
    well = set(counts.filter(counts["len"] >= counts["len"].quantile(0.5))["model_id"])
    ordered = sorted((data.theta[m], m) for m in well)
    lo, hi = ordered[len(ordered) // 5], ordered[4 * len(ordered) // 5]
    return {lo[1]: lo[0], hi[1]: hi[0]}


@pytest.fixture(scope="module")
def data():
    return generate(SyntheticSpec(n_models=60, n_benchmarks=18, missing_rate=0.6, seed=7))


@pytest.fixture(scope="module")
def fitted(data):
    anchors = pick_anchors(data)
    return anchors, fit_irt(data.obs, IRTConfig(anchors=anchors, anchor_mode="fixed"))


def test_synthetic_data_has_requested_shape(data):
    n_models = data.obs["model_id"].n_unique()
    n_bench = data.obs["benchmark_id"].n_unique()
    assert n_models == 60 and n_bench == 18
    missing = 1 - data.obs.height / (n_models * n_bench)
    assert 0.55 <= missing <= 0.65
    assert data.obs.group_by("model_id").len()["len"].min() >= 3
    assert data.obs["y"].min() > 0 and data.obs["y"].max() < 1


def test_converges(fitted):
    _, res = fitted
    assert res.converged, f"ei konvergoinut: grad={res.grad_norm:.2e}, iter={res.n_iter}"


def test_theta_recovery(data, fitted):
    _, res = fitted
    est = res.theta_by_model()
    true = np.array([data.theta[m] for m in est])
    corr = np.corrcoef(true, np.array(list(est.values())))[0, 1]
    assert corr > 0.95, f"corr(θ) = {corr:.3f}"


def test_difficulty_recovery(data, fitted):
    _, res = fitted
    b = res.b_by_benchmark()
    err = np.array([b[j] - data.b[j] for j in b])
    rmse = float(np.sqrt(np.mean(err**2)))
    assert rmse < 0.3, f"RMSE(b) = {rmse:.3f}"


def test_anchors_hit_exactly(fitted):
    anchors, res = fitted
    est = res.theta_by_model()
    for m, v in anchors.items():
        assert est[m] == pytest.approx(v, abs=1e-12)
        assert res.theta_se_by_model()[m] == 0.0


def test_saturated_benchmark_parameters_stay_finite(data, fitted):
    _, res = fitted
    a, b = res.a_by_benchmark(), res.b_by_benchmark()
    assert data.saturated, "generaattorin pitää tuottaa saturoituneita benchmarkeja"
    for j in data.saturated:
        share_high = data.obs.filter(benchmark_id=j)["y"].mean()
        assert share_high > 0.85, f"{j} ei ole saturoitunut (keskiarvo {share_high:.2f})"
        assert np.isfinite(a[j]) and 0 < a[j] < 10, f"a[{j}] = {a[j]}"
        assert np.isfinite(b[j]) and abs(b[j]) < 8, f"b[{j}] = {b[j]}"


def test_standard_errors_are_calibrated(data, fitted):
    anchors, res = fitted
    est, se = res.theta_by_model(), res.theta_se_by_model()
    free = [m for m in est if m not in anchors]
    inside = [abs(est[m] - data.theta[m]) <= Z80 * se[m] for m in free]
    coverage = float(np.mean(inside))
    assert 0.65 <= coverage <= 0.95, f"80 %:n välin kattavuus {coverage:.2f}"
    assert all(se[m] > 0 for m in free)


def test_coverage_over_multiple_seeds():
    """Kalibrointi ei saa olla yhden siemenen sattumaa."""
    cover = []
    for seed in (11, 12, 13):
        d = generate(SyntheticSpec(n_models=60, n_benchmarks=18, missing_rate=0.6, seed=seed))
        anchors = pick_anchors(d)
        r = fit_irt(d.obs, IRTConfig(anchors=anchors))
        est, se = r.theta_by_model(), r.theta_se_by_model()
        cover += [abs(est[m] - d.theta[m]) <= Z80 * se[m] for m in est if m not in anchors]
    assert 0.7 <= np.mean(cover) <= 0.9, f"kattavuus {np.mean(cover):.2f}"


def test_discrimination_recovery(data, fitted):
    _, res = fitted
    a = res.a_by_benchmark()
    err = np.array([np.log(a[j]) - np.log(data.a[j]) for j in a if j not in data.saturated])
    rmse = float(np.sqrt(np.mean(err**2)))
    assert rmse < 0.25, f"RMSE(ln a) = {rmse:.3f}"


def test_fully_saturated_benchmark_stays_finite(data):
    """Kaikki havainnot leikattu ylärajaan: b-prior estää karkaamisen (A12)."""
    j = data.saturated[0]
    clipped = data.obs.with_columns(
        y=np.where(data.obs["benchmark_id"] == j, 1 - 1e-3, data.obs["y"].to_numpy())
    )
    res = fit_irt(clipped, IRTConfig(anchors=pick_anchors(data)))
    a, b = res.a_by_benchmark()[j], res.b_by_benchmark()[j]
    assert res.converged
    assert np.isfinite(a) and 0 < a < 10, f"a = {a}"
    assert np.isfinite(b) and abs(b) < 10, f"b = {b}"


def test_post_hoc_standard_errors(data, fitted):
    """Delta-menetelmä: ankkurien SE = 0 ja muiden SE samaa kokoluokkaa kuin fixed-tilassa."""
    anchors, fixed = fitted
    post = fit_irt(data.obs, IRTConfig(anchors=anchors, anchor_mode="post_hoc_linear"))
    se_p, se_f = post.theta_se_by_model(), fixed.theta_se_by_model()
    for m in anchors:
        assert se_p[m] == 0.0
    ratio = np.median([se_p[m] / se_f[m] for m in se_p if m not in anchors])
    assert 0.7 < ratio < 1.4, f"SE post_hoc / fixed = {ratio:.2f}"


def test_unanchored_run_warns(data):
    with pytest.warns(UserWarning, match="ilman ankkureita"):
        fit_irt(data.obs, IRTConfig(anchors={}))


@pytest.mark.slow
@pytest.mark.parametrize(
    ("mode", "heterogeneous"),
    [("fixed", False), ("post_hoc_linear", False), ("fixed", True)],
)
def test_standard_errors_monte_carlo(mode, heterogeneous):
    """Sama rakenne, uudelleenarvottu kohina: SE vastaa estimaattien todellista hajontaa.

    Siemenet eroavat niistä, joilla SE-menetelmä valittiin (11, 13). heterogeneous=True
    jäljittelee Epochin dataa, jossa osalla malleista on vain 2–3 tulosta.
    """
    import polars as pl

    ratios, cover = [], []
    for seed in (21, 22, 23):
        d = generate(SyntheticSpec(seed=seed, heterogeneous_counts=heterogeneous))
        anchors = pick_anchors(d)
        th = np.array([d.theta[m] for m in d.obs["model_id"]])
        a = np.array([d.a[j] for j in d.obs["benchmark_id"]])
        b = np.array([d.b[j] for j in d.obs["benchmark_id"]])
        c = np.array([d.c[j] for j in d.obs["benchmark_id"]])
        rng = np.random.default_rng(seed)
        est, ses = [], []
        for _ in range(25):
            z = a * (th - b) + rng.normal(0, 0.3, len(th))
            y = np.clip(c + (1 - c) / (1 + np.exp(-z)), 1e-3, 1 - 1e-3)
            r = fit_irt(
                d.obs.with_columns(y=pl.Series(y)), IRTConfig(anchors=anchors, anchor_mode=mode)
            )
            ids = [m for m in r.model_ids if m not in anchors]
            est.append([r.theta_by_model()[m] for m in ids])
            ses.append([r.theta_se_by_model()[m] for m in ids])
        e, s = np.array(est), np.array(ses)
        true = np.array([d.theta[m] for m in ids])
        ratios.append(np.median(e.std(0) / s.mean(0)))
        cover.append(np.mean(np.abs(e - true) <= Z80 * s))
    print(
        f"{mode} het={heterogeneous}: sd/SE {np.mean(ratios):.2f}, kattavuus {np.mean(cover):.2f}"
    )
    assert 0.7 <= np.mean(ratios) <= 1.15, f"empiirinen hajonta / SE = {np.mean(ratios):.2f}"
    assert 0.75 <= np.mean(cover) <= 0.9, f"kattavuus {np.mean(cover):.2f}"


def test_post_hoc_linear_anchoring(data, fitted):
    anchors, fixed = fitted
    res = fit_irt(data.obs, IRTConfig(anchors=anchors, anchor_mode="post_hoc_linear"))
    est = res.theta_by_model()
    for m, v in anchors.items():
        assert est[m] == pytest.approx(v, abs=1e-9)
    # Ennusteet ovat invariantteja lineaarimuunnokselle → samat kuin ennen skaalausta
    common = list(est)
    corr = np.corrcoef([est[m] for m in common], [fixed.theta_by_model()[m] for m in common])
    assert corr[0, 1] > 0.98


def test_predictions_invariant_under_post_hoc_transform(data):
    anchors = pick_anchors(data)
    unanch = fit_irt(data.obs, IRTConfig(anchors={}))
    post = fit_irt(data.obs, IRTConfig(anchors=anchors, anchor_mode="post_hoc_linear"))
    np.testing.assert_allclose(unanch.predict(data.obs), post.predict(data.obs), atol=1e-9)


def test_deterministic(data):
    anchors = pick_anchors(data)
    r1 = fit_irt(data.obs, IRTConfig(anchors=anchors, seed=3))
    r2 = fit_irt(data.obs, IRTConfig(anchors=anchors, seed=3))
    np.testing.assert_array_equal(r1.theta, r2.theta)
    np.testing.assert_array_equal(r1.b, r2.b)
    d2 = generate(SyntheticSpec(n_models=60, n_benchmarks=18, missing_rate=0.6, seed=7))
    assert d2.obs.equals(data.obs)


def test_missing_anchor_models_are_reported(data):
    anchors = pick_anchors(data) | {"not-in-data": 5.0}
    res = fit_irt(data.obs, IRTConfig(anchors=anchors))
    assert res.missing_anchors == ["not-in-data"]
    with pytest.raises(ValueError, match="ankkuri"):
        fit_irt(data.obs, IRTConfig(anchors={"not-in-data": 1.0, "nor-this": 2.0}))


def test_weights_change_fit(data):
    anchors = pick_anchors(data)
    heavy = data.obs.with_columns(
        weight=np.where(data.obs["benchmark_id"] == "bench_00", 50.0, 1.0)
    )
    r1 = fit_irt(data.obs, IRTConfig(anchors=anchors))
    r2 = fit_irt(heavy, IRTConfig(anchors=anchors))
    assert not np.allclose(r1.theta, r2.theta)


# --- Tallennus tietokantaan -----------------------------------------------


def test_fit_and_store_writes_run(data):
    from forecaster.config import BenchmarkConfig
    from forecaster.db.models import EvaluationRow

    repo = Repository.connect(":memory:")
    repo.init_db()
    bids = sorted(data.obs["benchmark_id"].unique())
    repo.upsert_benchmarks(
        [
            BenchmarkConfig(
                benchmark_id=j, name=j, domain="math" if i % 2 else "coding",
                metric_type="accuracy", raw_min=0, raw_max=1,
                guessing_floor=data.c[j],
            )
            for i, j in enumerate(bids)
        ]
    )  # fmt: skip
    start = date(2023, 1, 1)
    repo.upsert_models(
        [
            ModelRow(model_id=m, display_name=m, provider="synthetic",
                     release_date=start + timedelta(days=int(30 * data.release_index[m])))
            for m in sorted(data.theta)
        ]
    )  # fmt: skip
    repo.upsert_evaluations(
        [
            EvaluationRow(model_id=m, benchmark_id=j, raw_score=y, source_name="synthetic")
            for m, j, y in data.obs.select("model_id", "benchmark_id", "y").iter_rows()
        ]
    )
    anchors = pick_anchors(data)
    run_id = fit_and_store(repo, IRTConfig(anchors=anchors), domain="overall")

    n_scores = repo._one("SELECT count(*) FROM capability_scores WHERE run_id = ?", [run_id])[0]
    assert n_scores == 60
    n_params = repo._one("SELECT count(*) FROM benchmark_irt_params WHERE run_id = ?", [run_id])[0]
    assert n_params == 18
    converged, n_obs = repo._one(
        "SELECT converged, n_observations FROM irt_runs WHERE run_id = ?", [run_id]
    )
    assert converged and n_obs == data.obs.height
    assert repo.get_pareto_frontier(run_id).height >= 3

    # Osa-aluekohtainen ajo (A6) käyttää vain sen domainin benchmarkeja
    coding_run = fit_and_store(repo, IRTConfig(anchors={}), domain="coding")
    n_coding = repo._one(
        "SELECT count(*) FROM benchmark_irt_params WHERE run_id = ?", [coding_run]
    )[0]
    assert n_coding == len(bids[0::2])
    repo.close()
