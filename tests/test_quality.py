"""K4:n hyväksymiskriteerit.

- Karsinta: liian harvat mallit/benchmarkit pois (aukko löydetty oikeasta datasta)
- Poikkeamat: istutetut kontaminaatiot löytyvät, vääriä hälytyksiä vähän, uudelleenajo kerran
- Vakaus: uuden datan lisäys liikuttaa ei-ankkurimallien θ:aa vähemmän kuin niiden SE
- Domain-ajot linkitetään overall-asteikkoon (A12)
- Kytkentä, siltamallit ja ulkoinen vertailu
"""

import warnings
from dataclasses import replace
from datetime import date

import numpy as np
import polars as pl
import pytest
from synthetic_db import pick_anchors, seed_repo

from forecaster.models.irt_engine import IRTConfig, fit_irt
from forecaster.models.pipeline import PipelineConfig, run_fit, select_exclusions
from forecaster.models.quality import (
    bridge_summary,
    compare_external,
    connectivity,
    detect_outliers,
    leave_one_benchmark_out,
    prune_sparse,
)
from forecaster.models.synthetic import SyntheticSpec, generate


@pytest.fixture(scope="module")
def data():
    return generate(SyntheticSpec(n_models=60, n_benchmarks=18, seed=7))


def obs_from(pairs):
    return pl.DataFrame(
        {
            "model_id": [m for m, _ in pairs],
            "benchmark_id": [b for _, b in pairs],
            "y": 0.5,
            "weight": 1.0,
            "guessing_floor": 0.0,
        }
    )


# --- Karsinta ----------------------------------------------------------------


def test_prune_is_iterative():
    # m3:lla 3 tulosta, mutta b3:lla vain m3 → b3 pois → m3:lle jää 2 → m3 pois
    pairs = [(m, b) for m in ("m1", "m2") for b in ("b1", "b2", "b3x", "b4")]
    pairs += [("m3", "b1"), ("m3", "b2"), ("m3", "b3")]
    pr = prune_sparse(obs_from(pairs), min_model_obs=3, min_benchmark_obs=2)
    assert pr.dropped_benchmarks == ["b3"]
    assert pr.dropped_models == ["m3"]
    assert set(pr.obs["model_id"]) == {"m1", "m2"}


def test_prune_reports_benchmark_lost_via_models():
    # bX:n kaikki 5 tulosta ovat malleilta, joilla on vain se yksi tulos → mallit pois → bX katoaa
    pairs = [(m, b) for m in ("m1", "m2", "m3") for b in ("b1", "b2", "b3")]
    pairs += [(f"lone{i}", "bX") for i in range(5)]
    pr = prune_sparse(obs_from(pairs), min_model_obs=3, min_benchmark_obs=3)
    assert "bX" in pr.dropped_benchmarks


def test_prune_noop_when_dense(data):
    pr = prune_sparse(data.obs, 3, 5)
    assert pr.obs.height == data.obs.height and not pr.dropped_models


# --- Poikkeamat ------------------------------------------------------------------


def contaminate(data, n=6, seed=0):
    """Istuta n "vuotanutta" tulosta: malli saa lähes täydet pisteet testistä,
    jossa sen odotettu tulos on keskitasoa."""
    rng = np.random.default_rng(seed)
    obs = data.obs
    th = np.array([data.theta[m] for m in obs["model_id"]])
    a = np.array([data.a[j] for j in obs["benchmark_id"]])
    b = np.array([data.b[j] for j in obs["benchmark_id"]])
    c = np.array([data.c[j] for j in obs["benchmark_id"]])
    yhat = c + (1 - c) / (1 + np.exp(-a * (th - b)))
    counts = obs.group_by("model_id").len()
    many = set(counts.filter(pl.col("len") >= 6)["model_id"])
    ok = np.where((yhat > 0.3) & (yhat < 0.6) & obs["model_id"].is_in(many).to_numpy())[0]
    idx = rng.choice(ok, size=n, replace=False)
    y = obs["y"].to_numpy().copy()
    y[idx] = 0.98
    planted = {(obs["model_id"][int(i)], obs["benchmark_id"][int(i)]) for i in idx}
    return obs.with_columns(y=pl.Series(y)), planted


def test_planted_contamination_is_detected(data):
    obs, planted = contaminate(data)
    res = fit_irt(obs, IRTConfig(anchors=pick_anchors(data)))
    out = detect_outliers(obs, res, threshold=3.0)
    found = set(out.select("model_id", "benchmark_id").iter_rows())
    assert len(found & planted) >= 5, f"löytyi {len(found & planted)}/6"
    false_pos = len(found - planted) / (obs.height - len(planted))
    assert false_pos < 0.03, f"väärien hälytysten osuus {false_pos:.3f}"
    assert set(out.filter(pl.col("direction") == "high").select("model_id", "benchmark_id")
               .iter_rows()) >= (found & planted)  # fmt: skip


def test_low_direction_detected_but_not_excluded(data):
    obs = data.obs
    res = fit_irt(obs, IRTConfig(anchors=pick_anchors(data)))
    yhat = res.predict(obs)
    counts = dict(obs.group_by("model_id").len().iter_rows())
    k = next(i for i in range(obs.height)
             if 0.5 < yhat[i] < 0.8 and counts[obs["model_id"][i]] >= 6)  # fmt: skip
    y = obs["y"].to_numpy().copy()
    y[k] = 0.02
    bad = obs.with_columns(y=pl.Series(y))
    out = select_exclusions(detect_outliers(bad, fit_irt(bad, res.config)), bad, 3)
    row = out.filter(model_id=obs["model_id"][k], benchmark_id=obs["benchmark_id"][k])
    assert row.height == 1 and row["direction"][0] == "low"
    assert not row["excluded"][0]


def test_small_benchmarks_are_not_flagged(data):
    res = fit_irt(data.obs, IRTConfig(anchors=pick_anchors(data)))
    assert detect_outliers(data.obs, res, threshold=0.0, min_benchmark_obs=10_000).is_empty()
    # threshold on σ-ekvivalentti: raja 0 liputtaa ~kaiken, raja 3 harvoja
    many = detect_outliers(data.obs, res, threshold=0.0).height
    few = detect_outliers(data.obs, res, threshold=3.0).height
    assert many > 0.9 * data.obs.height and few < 0.03 * data.obs.height


def test_select_exclusions_rules():
    flagged = pl.DataFrame(
        {
            "model_id": ["a", "a", "b", "c"],
            "benchmark_id": ["x", "y", "x", "x"],
            "robust_z": [5.0, 4.0, 6.0, -7.0],
            "direction": ["high", "high", "high", "low"],
        }
    )
    obs = obs_from([("a", b) for b in "xyzw"] + [("b", b) for b in "xyz"] + [("c", "x")])
    out = select_exclusions(flagged, obs, min_model_obs=3)
    ex = set(out.filter("excluded").select("model_id", "benchmark_id").iter_rows())
    # a: vain vahvin (x); b: 3 tulosta → poisto pudottaisi alle rajan; c: low ei poisteta
    assert ex == {("a", "x")}


def test_clean_data_has_few_false_alarms(data):
    res = fit_irt(data.obs, IRTConfig(anchors=pick_anchors(data)))
    out = detect_outliers(data.obs, res)
    assert out.height / data.obs.height < 0.03


def test_pipeline_flags_refits_and_prunes(data):
    obs, planted = contaminate(data)
    sparse = generate(SyntheticSpec(n_models=60, n_benchmarks=18, seed=7,
                                    heterogeneous_counts=True))  # fmt: skip
    repo = seed_repo(data, obs)
    anchors = pick_anchors(data)
    s = run_fit(repo, IRTConfig(anchors=anchors), "overall")

    flagged_all = set(s.outliers.select("model_id", "benchmark_id").iter_rows())
    excluded = set(s.outliers.filter("excluded").select("model_id", "benchmark_id").iter_rows())
    in_db = set(repo._run("SELECT model_id, benchmark_id FROM evaluations WHERE is_outlier"))
    assert in_db == excluded  # kantaan vain sovituksesta poistetut
    assert len(flagged_all & planted) >= 5
    assert len(excluded & planted) >= 4  # enintään 1 per malli
    assert s.outliers.filter("excluded")["direction"].unique().to_list() == ["high"]
    # Uudelleensovitus ilman poistettuja
    used = set(s.obs.select("model_id", "benchmark_id").iter_rows())
    assert not (used & excluded)
    assert s.result.converged
    stored = repo._one("SELECT n_observations FROM irt_runs WHERE run_id = ?", [s.run_id])[0]
    assert stored == s.obs.height

    # Uusi ajo nollaa vanhat liput (liput ovat ajon tulos, eivät pysyviä)
    run_fit(repo, IRTConfig(anchors=anchors), "overall", PipelineConfig(detect_outliers=False))
    assert repo._one("SELECT count(*) FROM evaluations WHERE is_outlier")[0] == 0
    repo.close()

    # Karsinta: harvan datan mallit (alle 3 tulosta) pudotetaan ja raportoidaan
    repo2 = seed_repo(sparse)
    s2 = run_fit(repo2, IRTConfig(anchors=pick_anchors(sparse)), "overall")
    counts = sparse.obs.group_by("model_id").len()
    few = set(counts.filter(pl.col("len") < 3)["model_id"])
    assert few and few <= set(s2.dropped_models)
    assert not (few & set(s2.result.model_ids))
    repo2.close()


# --- Vakaus ------------------------------------------------------------------------


def test_adding_new_models_moves_theta_less_than_se(data):
    """Hyväksymiskriteeri: kun uutta dataa (malleja) lisätään, ankkurien ulkopuolisten
    mallien θ muuttuu ajojen välillä vähemmän kuin niiden SE."""
    anchors = pick_anchors(data)
    rng = np.random.default_rng(1)
    models = sorted(set(data.obs["model_id"]) - set(anchors))
    later = set(rng.choice(models, size=len(models) // 6, replace=False))
    before = fit_irt(data.obs.filter(~pl.col("model_id").is_in(later)), IRTConfig(anchors=anchors))
    after = fit_irt(data.obs, IRTConfig(anchors=anchors))
    tb, ta, se = before.theta_by_model(), after.theta_by_model(), after.theta_se_by_model()
    common = [m for m in tb if m not in anchors]
    share = np.mean([abs(ta[m] - tb[m]) < se[m] for m in common])
    assert share >= 0.9, f"vain {share:.2f} malleista pysyi SE:n sisällä"


def test_leave_one_benchmark_out(data):
    anchors = pick_anchors(data)
    benches = sorted(data.b)[5:8]
    df = leave_one_benchmark_out(data.obs, IRTConfig(anchors=anchors), benches)
    assert df.height == 3
    assert set(df.columns) >= {"benchmark_id", "n_tested", "median_z", "share_z_within_1_96",
                               "untested_median_shift_se"}  # fmt: skip
    # Vain testatut mallit lasketaan: n_tested = benchmarkin havaintojen määrä (- ankkurit)
    for b, n in df.select("benchmark_id", "n_tested").iter_rows():
        n_obs = data.obs.filter(benchmark_id=b).height
        assert n_obs - 2 <= n <= n_obs
    # Oikein määritellyllä datalla z ~ N(0,1): suurin osa |z| < 1.96
    assert (df["share_z_within_1_96"] > 0.8).all(), df


def test_misfitting_benchmark_gets_low_discrimination(data):
    """Huonosti sopiva testi (puolet tuloksista vuotaneita) ei vääristä asteikkoa, vaan saa
    pienen a:n → pieni vaikutus θ:aan. Siksi LOBO mittaa vaikutusta, ei sopivuutta, ja
    sopimattomat testit näkyvät raportissa matalana a:na."""
    anchors = pick_anchors(data)
    b = sorted(data.b)[8]
    tested = sorted(set(data.obs.filter(benchmark_id=b)["model_id"]) - set(anchors))
    bad = data.obs.with_columns(
        y=pl.when((pl.col("benchmark_id") == b) & pl.col("model_id").is_in(tested[::2]))
        .then(0.99)
        .otherwise(pl.col("y"))
    )
    a_clean = fit_irt(data.obs, IRTConfig(anchors=anchors)).a_by_benchmark()[b]
    a_bad = fit_irt(bad, IRTConfig(anchors=anchors)).a_by_benchmark()[b]
    assert a_bad < 0.5 * a_clean


# --- Domain-ajot: kiinteät osioparametrit (A12) -----------------------------------------


def test_domain_run_uses_overall_item_parameters(data):
    """Synteettisessä datassa jokaisella mallilla on yksi θ → domain-ajon θ:n pitää osua
    TODELLISEEN θ:aan samalla asteikolla (kulmakerroin ≈ 1, sijainti ≈ 0). Testi ei nojaa
    linkitysjoukkoon, joten asteikon kutistuminen näkyisi (math-reviewer K4)."""
    repo = seed_repo(data)
    anchors = pick_anchors(data)
    with pytest.raises(ValueError, match="overall"):
        run_fit(repo, IRTConfig(anchors=anchors), "coding")
    overall = run_fit(repo, IRTConfig(anchors=anchors), "overall")
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # ei "ilman ankkureita" -varoitusta
        coding = run_fit(repo, IRTConfig(anchors=anchors), "coding")
    assert coding.fixed_from == overall.run_id
    # Osioparametrit ovat täsmälleen overall-ajon arvot
    ob, cb = overall.result.b_by_benchmark(), coding.result.b_by_benchmark()
    for j, v in cb.items():
        assert v == pytest.approx(ob[j], abs=1e-12)
    tc = coding.result.theta_by_model()
    true = np.array([data.theta[m] for m in tc])
    est = np.array(list(tc.values()))
    slope = np.polyfit(true, est, 1)[0]
    assert 0.85 < slope < 1.15, f"kulmakerroin {slope:.2f}"
    assert abs((est - true).mean()) < 0.2
    stored = repo._one("SELECT count(*) FROM capability_scores WHERE run_id = ? AND domain = "
                       "'coding'", [coding.run_id])[0]  # fmt: skip
    assert stored == len(tc)
    repo.close()


# --- Kytkentä, sillat, ulkoinen vertailu ------------------------------------------------


def test_connectivity_detects_islands():
    pairs = [("m1", "b1"), ("m2", "b1"), ("m3", "b2"), ("m3", "b3")]
    assert connectivity(obs_from(pairs)) == [(2, 1), (1, 2)]


def test_bridge_summary():
    pairs = [("m1", "old"), ("m1", "new"), ("m2", "old"), ("m3", "new"), ("m3", "mid")]
    dates = {"old": date(2020, 1, 1), "mid": date(2024, 1, 1), "new": date(2025, 6, 1)}
    s = bridge_summary(obs_from(pairs), dates)
    assert s == {"bridge_1_1": 1, "bridge_2_2": 0, "only_old": 1, "only_new": 1}


def test_compare_external():
    t = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 0.0}
    assert compare_external(t, {"a": 10, "b": 20, "c": 30, "x": 5}) == {"n": 3, "spearman": 1.0}
    assert compare_external(t, {"a": 1})["spearman"] is None


def test_min_obs_config_is_respected():
    d = generate(SyntheticSpec(seed=7, heterogeneous_counts=True))
    repo = seed_repo(d)
    s = run_fit(repo, IRTConfig(anchors=pick_anchors(d)), "overall",
                replace(PipelineConfig(), min_model_obs=6, detect_outliers=False))  # fmt: skip
    assert s.dropped_models
    assert s.obs.group_by("model_id").len()["len"].min() >= 6
    with pytest.raises(ValueError, match="karsinnan jälkeen"):
        run_fit(repo, IRTConfig(), "overall", replace(PipelineConfig(), min_model_obs=99))
    repo.close()
