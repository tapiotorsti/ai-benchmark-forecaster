"""K6: dashboardin hyväksymiskriteerit (TOTEUTUSSUUNNITELMA.md, K6).

- Kaikki sivut latautuvat alle 3 s välimuistista (AppTest, synteettinen kanta)
- Painojen muutos päivittää tulokset alle 30 s (simulaattori; oikean datan mittaus raportissa)
- Lisäksi: laskentafunktiot, repositoryn dashboard-kyselyt, skeemamigraatio 1 → 2
"""

import shutil
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from synthetic_db import pick_anchors, seed_repo

from forecaster.config import load_forecast
from forecaster.dashboard import charts, data
from forecaster.db.models import PriceRow
from forecaster.db.repository import SCHEMA_VERSION, Repository
from forecaster.models.irt_engine import IRTConfig
from forecaster.models.pipeline import run_fit
from forecaster.models.synthetic import SyntheticSpec, generate

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 1, 1)


@pytest.fixture(scope="module")
def synth():
    return generate(SyntheticSpec(n_models=60, n_benchmarks=18, seed=7))


def _add_prices(repo: Repository, synth) -> None:
    """Hinta laskee julkaisuajan mukaan ja nousee θ:n mukaan (iso-kyvykkyyskäyrälle dataa)."""
    rows = []
    for m, th in synth.theta.items():
        rel = date(2023, 1, 1) + timedelta(days=int(1000 * synth.release_index[m]))
        cost = float(20 * np.exp(0.8 * th) * 2 ** (-synth.release_index[m] * 33 / 8))
        rows.append(PriceRow(model_id=m, valid_from=rel, source_name="manual",
                             input_cost_per_1m=cost, output_cost_per_1m=cost))  # fmt: skip
    repo.upsert_prices(rows)


@pytest.fixture(scope="module")
def project(tmp_path_factory, synth):
    """Tiedostopohjainen projekti: config + kanta, jossa overall-ajo ja hinnat."""
    root = tmp_path_factory.mktemp("proj")
    shutil.copytree(PROJECT_ROOT / "config", root / "config")
    anchors = pick_anchors(synth)
    (root / "config" / "anchors.yaml").write_text(
        "anchors:\n"
        + "".join(f"  - {{model_id: {m}, theta: {v:.4f}}}\n" for m, v in anchors.items()),
        encoding="utf-8",
    )
    (root / "data").mkdir()
    db = root / "data" / "benchmark.duckdb"
    repo = seed_repo(synth, path=db)
    _add_prices(repo, synth)
    summary = run_fit(repo, IRTConfig(anchors=anchors))
    repo.close()
    return {"root": root, "db": db, "run_id": summary.run_id, "anchors": anchors}


@pytest.fixture
def repo(project):
    r = Repository.connect(project["db"], read_only=True)
    yield r
    r.close()


# --- Repository ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "dropped"),
    [(1, ["ingest_unknown_names", "pipeline_runs"]), (2, ["pipeline_runs"])],
)
def test_additive_schema_migration(tmp_path, old, dropped):
    """Versiot 1 ja 2 päivittyvät nykyiseen ajamalla schema.sql uudelleen (vain uusia tauluja)."""
    db = tmp_path / "old.duckdb"
    r = Repository.connect(db)
    r.init_db()
    for t in dropped:
        r._run(f"DROP TABLE {t}")
    r._run("DELETE FROM schema_version")
    r._run("INSERT INTO schema_version (version) VALUES (?)", [old])
    assert r.schema_version() == old
    assert r.init_db() == SCHEMA_VERSION == 3
    assert r.schema_version() == 3
    assert all(r.has_table(t) for t in dropped)
    r.close()


def test_unknown_names_saved_by_ingest_run():
    from forecaster.ingest.base import run_ingest

    r = Repository.connect(":memory:")
    r.init_db()

    def work(run_id, report):
        report.unknown_aliases = ["b-model", "a-model"]
        report.unmapped_benchmarks = ["weird-bench"]

    run_ingest(r, "src_x", work)
    u = r.get_unknown_names()
    assert sorted(u.filter(pl.col("kind") == "alias")["name"]) == ["a-model", "b-model"]
    assert u.filter(pl.col("kind") == "benchmark")["name"].to_list() == ["weird-bench"]


def test_dashboard_queries(repo, project, synth):
    runs = repo.latest_runs_by_domain()
    assert runs == {"overall": project["run_id"]}
    m = repo.get_models_overview(project["run_id"])
    assert m.height == repo.get_run_info(project["run_id"])["n_models"]
    assert m["theta"].is_sorted(descending=True)
    p = repo.get_benchmark_params(project["run_id"])
    assert p.height == len(synth.b)
    assert (p["n_models"] > 0).all()
    assert repo.get_latest_forecasts() == {}
    assert repo.get_ingest_runs().is_empty()


# --- Laskenta -------------------------------------------------------------------------


def test_model_detail_matches_irt_prediction(repo, project):
    run = project["run_id"]
    models = repo.get_models_overview(run)
    mid, theta = models.row(0)[0], models["theta"][0]
    params = repo.get_benchmark_params(run)
    obs = repo.get_observation_matrix("overall", exclude_outliers=False)
    d = data.model_detail(mid, theta, params, obs, repo.get_outliers())
    assert d.height == params.height  # ennuste kaikille benchmarkeille
    row = d.row(0, named=True)
    expected = row["guessing_floor"] + (1 - row["guessing_floor"]) / (
        1 + np.exp(-row["discrimination_a"] * (theta - row["difficulty_b"]))
    )
    assert row["y_pred"] == pytest.approx(expected)
    seen = d.filter(pl.col("y_obs").is_not_null())
    assert seen.height == obs.filter(pl.col("model_id") == mid).height


def test_saturation_bounds_and_monotonic(repo, project):
    params = repo.get_benchmark_params(project["run_id"])
    lo = data.with_saturation(params, -2.0)["saturation"]
    hi = data.with_saturation(params, 3.0)["saturation"]
    assert ((lo >= 0) & (lo <= 1)).all() and ((hi >= 0) & (hi <= 1)).all()
    assert (hi >= lo).all()


def test_sim_settings_key_stable_and_distinct():
    a = data.SimSettings(weights={"x": 2.0, "y": 0.5})
    b = data.SimSettings(weights={"y": 0.5, "x": 2.0})
    assert a.key() == b.key()
    assert a.key() != data.SimSettings(weights={"x": 2.0}).key()
    assert a.key() != data.SimSettings(weights={"x": 2.0, "y": 0.5}, exclude_outliers=False).key()
    assert (
        data.SimSettings().key() != data.SimSettings(drop_contamination=frozenset({"high"})).key()
    )


def test_apply_settings_weights_and_filters():
    obs = pl.DataFrame({"model_id": ["m1", "m1", "m2"], "benchmark_id": ["a", "b", "c"],
                        "y": [0.5, 0.6, 0.7], "weight": [1.0, 1.0, 1.0],
                        "guessing_floor": [0.0, 0.0, 0.0]})  # fmt: skip
    bench = pl.DataFrame({"benchmark_id": ["a", "b", "c"],
                          "contamination_risk": ["low", "high", None]})  # fmt: skip
    out = data.apply_settings(obs, bench, data.SimSettings(weights={"a": 3.0, "c": 0.0}))
    assert dict(out.select("benchmark_id", "weight").iter_rows()) == {"a": 3.0, "b": 1.0}
    out2 = data.apply_settings(obs, bench, data.SimSettings(drop_contamination=frozenset({"high"})))
    assert sorted(out2["benchmark_id"]) == ["a", "c"]  # riskitieto puuttuu → säilyy


def test_simulate_default_reproduces_stored_run(repo, project):
    """Oletussäädöillä simulaatio ≈ tallennettu ajo; painon muutos muuttaa tuloksia."""
    obs = repo.get_observation_matrix("overall", exclude_outliers=True)
    bench = repo.get_benchmark_params(project["run_id"])
    base = repo.get_models_overview(project["run_id"]).select("model_id", "display_name",
                                                               "release_date", "theta")  # fmt: skip
    res, _ = data.simulate(obs, bench, project["anchors"], data.SimSettings())
    cmp = data.compare_runs(base, res)
    assert cmp["delta"].abs().max() < 1e-3
    heavy = bench["benchmark_id"][0]
    res2, _ = data.simulate(obs, bench, project["anchors"], data.SimSettings(weights={heavy: 5.0}))
    assert data.compare_runs(base, res2)["delta"].abs().max() > 1e-3


def test_forecast_band_and_cost_view(repo, project):
    ff = load_forecast(type("P", (), {"config_dir": project["root"] / "config"})())
    scores = repo.get_models_overview(project["run_id"])
    out = data.forecast_band(scores, ff, AS_OF, n_boot=100)
    assert out is not None
    band, fc = out
    assert band.height > 12
    assert (band["pred_p10"] <= band["trend_p50"]).all()
    assert (band["trend_p50"] <= band["pred_p90"]).all()
    assert "eval" not in fc
    prices = data.price_grid(repo.get_price_at_dates, ff, AS_OF)
    cv = data.cost_view(scores, prices, 0.0, ff, AS_OF, n_boot=100)
    assert cv["status"] != "insufficient"
    assert cv["monthly_df"].height > 0
    assert cv["monthly_df"]["cost"].is_sorted(descending=True) or cv["monthly_df"].height > 1
    empty = data.cost_view(scores, prices.clear(), 0.0, ff, AS_OF)
    assert empty["status"] == "insufficient"


def test_chart_builders(repo, project):
    run = project["run_id"]
    scores = data.with_frontier(repo.get_models_overview(run))
    assert scores["is_frontier"].sum() >= 3
    fig = charts.overview(scores, None, AS_OF)
    assert len(fig.data) == 2
    params = data.with_saturation(repo.get_benchmark_params(run), float(scores["theta"].max()))
    assert len(charts.benchmarks(params).data) == 1
    monthly = pl.DataFrame({"month": [date(2024, 1, 1), date(2024, 2, 1)], "cost": [2.0, 1.0],
                            "model_id": ["a", "b"]})  # fmt: skip
    hz = pl.DataFrame({"date": [date(2024, 8, 1)], "months": [6], "p10": [0.4], "p50": [0.6],
                       "p90": [1.0]})  # fmt: skip
    assert len(charts.cost(monthly, hz, 0.1, "x").data) == 4


# --- Hyväksymiskriteerit: sivut (Streamlit AppTest) ------------------------------------

PAGES = ["overview", "models", "benchmarks", "costs", "simulator", "data_quality"]


@pytest.fixture
def app_env(project, monkeypatch):
    monkeypatch.setenv("FORECASTER_ROOT", str(project["root"]))
    return project


@pytest.mark.parametrize("page", PAGES)
def test_page_loads_under_3s_from_cache(app_env, page):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(PROJECT_ROOT / "app" / "pages" / f"{page}.py"), default_timeout=120)
    at.run()  # 1. ajo täyttää välimuistin
    assert not at.exception, at.exception
    t0 = time.perf_counter()
    at.run()
    elapsed = time.perf_counter() - t0
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert elapsed < 3.0, f"{page}: {elapsed:.2f} s"


def test_entrypoint_shows_update_status_banner(app_env):
    """K7: jokaisen sivun yläreunassa päivitystilan hälytykset (tässä: ei päivitysajoja)."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(PROJECT_ROOT / "app" / "dashboard.py"), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    assert any("Automaattista päivitystä ei ole vielä ajettu" in i.value for i in at.info)


def test_simulator_refit_under_30s(app_env):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(
        str(PROJECT_ROOT / "app" / "pages" / "simulator.py"), default_timeout=120
    )
    at.run()
    at.multiselect[0].select("high").run()
    t0 = time.perf_counter()
    at.button[0].click().run()
    elapsed = time.perf_counter() - t0
    assert not at.exception, at.exception
    assert elapsed < 30.0
    assert any(m.label == "Rintaman kulmakerroin" for m in at.metric)
