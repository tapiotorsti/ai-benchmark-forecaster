"""K7: automaattinen päivitys (TOTEUTUSSUUNNITELMA.md, K7).

Hyväksymiskriteeri: viikoittainen ajo menee läpi ilman käsityötä, kun uusia aliaksia ei ole.
Lisäksi: hälytykset (uudet tuntemattomat nimet, epäonnistunut haku, vanha data, kesken
jäänyt ajo) ja CLI:n paluukoodi. Datalähteet ovat keksittyjä – ei verkkoa.
"""

import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from synthetic_db import pick_anchors, seed_repo
from typer.testing import CliRunner

from forecaster.config import get_paths
from forecaster.db.repository import Repository
from forecaster.ingest.base import run_ingest
from forecaster.models.synthetic import SyntheticSpec, generate
from forecaster.update import Alert, Source, UpdateResult, health_alerts, run_update

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 1, 15)


@pytest.fixture(scope="module")
def synth():
    return generate(SyntheticSpec(n_models=60, n_benchmarks=18, seed=7))


@pytest.fixture
def project(tmp_path, synth):
    """Tuore projekti: config, synteettinen kanta (ei vielä IRT-ajoa) ja ankkurit."""
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    anchors = pick_anchors(synth)
    (tmp_path / "config" / "anchors.yaml").write_text(
        "anchors:\n"
        + "".join(f"  - {{model_id: {m}, theta: {v:.4f}}}\n" for m, v in anchors.items()),
        encoding="utf-8",
    )
    (tmp_path / "data").mkdir()
    seed_repo(synth, path=tmp_path / "data" / "benchmark.duckdb").close()
    return get_paths(tmp_path)


def fake_source(name: str, unknown=(), fail: bool = False) -> Source:
    def fetch(paths, today):
        if fail:
            raise ConnectionError("verkko poikki")
        p = paths.raw_dir / name / f"{today}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
        return p

    def ingest(repo, paths, payload, today):
        def work(run_id, report):
            report.unknown_aliases = list(unknown)

        return run_ingest(repo, name, work)

    return Source(name, fetch, ingest)


def _run(paths, sources, **kw) -> UpdateResult:
    return run_update(paths, today=TODAY, sources=sources, n_boot=60, wait_seconds=5, **kw)


# --- Hyväksymiskriteeri ----------------------------------------------------------------


def test_clean_weekly_run_passes_without_manual_work(project):
    res = _run(project, [fake_source("src_a"), fake_source("src_b")])
    assert res.status == "ok", res.summary()
    assert all(s["ok"] for s in res.steps), res.summary()
    names = [s["name"] for s in res.steps]
    assert names[:4] == ["haku:src_a", "haku:src_b", "tuonti:src_a", "tuonti:src_b"]
    assert "irt:overall" in names and "irt:math" in names and "irt:coding" in names
    assert names[-1] == "ennusteet"
    assert not res.alerts

    with Repository.connect(project.db_path, read_only=True) as r:
        runs = r.get_pipeline_runs()
        assert runs.height == 1 and runs["status"][0] == "ok"
        assert runs["finished_at"][0] is not None
        fc = r.get_latest_forecasts()  # ennusteet tallessa
        assert fc
        # A30: kuukausikaista tallennetaan myöhempää toteumavertailua varten
        assert len(fc["capability"]["monthly"]) == 25 and "prospective" in fc
        assert r.latest_irt_converged("overall") is True
    assert (project.data_dir / "logs" / f"{res.run_id}.log").is_file()
    assert (project.root / "docs" / "ennusteet.md").is_file()

    # Toinen viikko, ei uusia nimiä → yhä ok ilman toimenpiteitä
    res2 = _run(project, [fake_source("src_a"), fake_source("src_b")])
    assert res2.status == "ok", res2.summary()


# --- Hälytykset --------------------------------------------------------------------------


def test_new_unknown_names_raise_warning_only_for_new(project):
    first = _run(project, [fake_source("src_a", unknown=["old-model"])], domains=False)
    assert [a.code for a in first.alerts] == ["new_unknown_names"]  # ensimmäinen ajo: kaikki uusia
    second = _run(project, [fake_source("src_a", unknown=["old-model", "fresh-model"])],
                  domains=False)  # fmt: skip
    assert second.status == "warning"
    (a,) = second.alerts
    assert a.code == "new_unknown_names" and "fresh-model" in a.message
    assert "old-model" not in a.message
    third = _run(project, [fake_source("src_a", unknown=["old-model", "fresh-model"])],
                 domains=False)  # fmt: skip
    assert third.status == "ok"


def test_fetch_failure_is_critical_but_pipeline_continues(project):
    res = _run(project, [fake_source("src_a"), fake_source("src_down", fail=True)], domains=False)
    assert res.status == "failed"
    assert [a.code for a in res.alerts] == ["fetch_failed"]
    assert "verkko poikki" in res.alerts[0].message
    ok = {s["name"]: s["ok"] for s in res.steps}
    assert ok["haku:src_down"] is False
    assert ok["tuonti:src_a"] and ok["irt:overall"] and ok["ennusteet"]  # muu putki ajettiin
    assert "tuonti:src_down" not in ok
    with Repository.connect(project.db_path, read_only=True) as r:
        stored = r.get_pipeline_runs().row(0, named=True)
    assert stored["status"] == "failed"
    assert json.loads(stored["alerts_json"])[0]["code"] == "fetch_failed"


def _runs(rows):
    return pl.DataFrame(
        rows,
        schema={"run_id": pl.String, "started_at": pl.Datetime, "finished_at": pl.Datetime,
                "status": pl.String, "steps_json": pl.String, "alerts_json": pl.String},
        orient="row",
    )  # fmt: skip


def test_health_alerts():
    now = datetime(2026, 2, 1, 12, 0)
    assert [a.code for a in health_alerts(_runs([]), now)] == ["no_updates"]

    fresh = now - timedelta(days=2)
    ok = ("u1", fresh, fresh, "ok", "[]", "[]")
    assert health_alerts(_runs([ok]), now) == []

    old = now - timedelta(days=15)
    stale = ("u0", old, old, "ok", "[]", "[]")
    assert [a.code for a in health_alerts(_runs([stale]), now)] == ["stale_data"]

    warn = json.dumps([{"level": "warning", "code": "new_unknown_names", "message": "x"}])
    assert health_alerts(_runs([("u2", fresh, fresh, "warning", "[]", warn)]), now) == [
        Alert("warning", "new_unknown_names", "x")
    ]

    running = ("u3", now - timedelta(minutes=5), None, "running", "[]", "[]")
    codes = [a.code for a in health_alerts(_runs([running, ok]), now)]
    assert codes == ["update_running"]  # edellinen ajo ok → ei muita hälytyksiä

    crashed = ("u4", now - timedelta(hours=8), None, "running", "[]", "[]")
    assert [a.code for a in health_alerts(_runs([crashed, ok]), now)] == ["update_crashed"]


# --- CLI ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("status", "code"), [("ok", 0), ("warning", 0), ("failed", 1)])
def test_cli_update_exit_code(monkeypatch, status, code):
    import forecaster.update as upd
    from forecaster.cli import app

    def fake_run_update(paths, **kw):
        r = UpdateResult(run_id="update_x", started_at=datetime(2026, 1, 1), status=status)
        if status != "ok":
            r.alerts.append(Alert("critical" if status == "failed" else "warning", "c", "m"))
        return r

    monkeypatch.setattr(upd, "run_update", fake_run_update)
    result = CliRunner().invoke(app, ["update", "--root", str(PROJECT_ROOT)])
    assert result.exit_code == code, result.output
    assert status.upper() in result.output
