"""6.2 (A35): ajankohtaan sidottu backtest. Ei verkkoa: arkistohaku MockTransportilla."""

import gzip
import io
import json
import zipfile
from datetime import date, timedelta

import httpx
import numpy as np
import polars as pl
import pytest
from synthetic_db import seed_repo

from forecaster.config import Paths
from forecaster.ingest import epoch_archive as ea
from forecaster.ingest.epoch_ai import FileSpec
from forecaster.models.forecaster import ForecastConfig
from forecaster.models.irt_engine import IRTConfig
from forecaster.models.pipeline import run_fit
from forecaster.models.synthetic import SyntheticSpec, generate
from forecaster.models.vintage import (
    METHODS,
    evaluate_origin,
    snapshot_cutoffs,
    vintage_backtest,
    vintage_eval_ids,
    write_report,
)


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


# --- Arkisto -------------------------------------------------------------------------


def test_monthly_and_snapshot_for(tmp_path):
    ts = ["20251103000000", "20251128120000", "20251201000000", "20260115000000"]
    assert ea.monthly(ts) == ["20251128120000", "20251201000000", "20260115000000"]
    snaps = [ea.Snapshot(t, tmp_path) for t in ea.monthly(ts)]
    assert ea.snapshot_for(snaps, date(2025, 11, 27)) is None
    assert ea.snapshot_for(snaps, date(2025, 12, 31)).timestamp == "20251201000000"
    assert ea.snapshot_for(snaps, date(2026, 1, 15)).timestamp == "20260115000000"
    # auto: lähtöpäivinä tilannekuvien päivät, vähintään kuukausi ennen as_ofia
    assert snapshot_cutoffs(snaps, date(2026, 2, 10)) == [date(2025, 11, 28), date(2025, 12, 1)]
    assert snapshot_cutoffs([], date(2026, 3, 1)) == []


def test_presence_reads_zip(tmp_path):
    z = tmp_path / "s.zip"
    z.write_bytes(_zip({
        "data/gpqa.csv": "Model version,Best score\n gpt-x ,0.5\ngpt-y,\n,0.3\n",
        "other.csv": "Model version,score\nzzz,1\n",
        "nover.csv": "Model,score\nq,1\n",
    }))  # fmt: skip
    specs = [FileSpec("gpqa_diamond", "gpqa.csv", "mean_score", 1.0),
             FileSpec("missing", "missing.csv", "x", 1.0),
             FileSpec("nover", "nover.csv", "score", 1.0)]  # fmt: skip
    # Pistesarakkeen nimi voi vaihdella paketeittain: läsnäolo = malliversion rivi
    assert ea.presence(z, specs) == {("gpqa_diamond", "gpt-x"), ("gpqa_diamond", "gpt-y")}


def test_fetch_snapshots_with_mock_transport(tmp_path):
    payload = _zip({"a.csv": "Model version\nm1\n"})
    cdx = [["timestamp", "statuscode"], ["20251110000000", "200"], ["20251120000000", "200"],
           ["20251205000000", "404"], ["20260102000000", "200"],
           ["20260125000000", "200"]]  # fmt: skip
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if "cdx" in req.url.path:
            return httpx.Response(200, json=cdx)
        if "20260125" in str(req.url):  # viallinen (ei zip) → saman kuukauden aiempi
            return httpx.Response(200, content=b"\x00garbage")
        body = gzip.compress(payload) if "20251120" in str(req.url) else payload
        return httpx.Response(200, content=body)

    paths = Paths(root=tmp_path)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snaps = ea.fetch_snapshots(paths, client=client)
    assert [s.timestamp for s in snaps] == ["20251120000000", "20260102000000"]
    for s in snaps:  # gzip-koodattu paketti purettu zipiksi
        assert zipfile.is_zipfile(s.path)
    assert [s.timestamp for s in ea.local_snapshots(paths)] == [s.timestamp for s in snaps]
    n = len(seen)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ea.fetch_snapshots(paths, client=client)
    # Vain CDX-haku ja viallisen uudelleenyritys; kelvolliset ladatut paketit ohitetaan
    assert len(seen) == n + 2
    assert not (paths.raw_dir / ea.ARCHIVE_DIR / "20260125000000").exists()


# --- Tulosten rajaus ----------------------------------------------------------------


def test_vintage_eval_ids_rules():
    keys = pl.DataFrame(
        [
            ("e1", "m1", "b1", "v1", "epoch_ai", None, date(2025, 1, 1)),  # tilannekuvassa
            ("e2", "m1", "b2", "v1", "epoch_ai", date(2025, 2, 1), date(2025, 1, 1)),  # ei
            ("e3", "m2", "b1", "v2", "epoch_ai", None, date(2025, 8, 1)),  # julkaistu myöhemmin
            ("e4", "m1", "b3", "", "internal", date(2025, 3, 1), date(2025, 1, 1)),  # ok
            ("e5", "m1", "b4", "", "internal", date(2025, 9, 1), date(2025, 1, 1)),  # myöhään
            ("e6", "m1", "b5", "", "internal", None, date(2025, 1, 1)),  # päiväys puuttuu
        ],
        schema=["eval_id", "model_id", "benchmark_id", "model_version", "source_name",
                "eval_date", "release_date"], orient="row",
    )  # fmt: skip
    present = {("b1", "v1"), ("b1", "v2")}
    assert vintage_eval_ids(keys, present, date(2025, 6, 1)) == ["e1", "e4"]
    assert vintage_eval_ids(keys, set(), date(2025, 6, 1)) == ["e4"]


# --- Päästä päähän synteettisellä datalla --------------------------------------------


@pytest.fixture(scope="module")
def world():
    """Synteettinen kanta: julkaisupäivä kasvaa θ:n mukana (rintama kasvaa ajassa),
    tulokset Epochin lähteenä versionimellä = model_id."""
    synth = generate(SyntheticSpec(n_models=60, n_benchmarks=18, seed=7))
    repo = seed_repo(synth)
    rng = np.random.default_rng(0)
    order = sorted(synth.theta, key=lambda m: synth.theta[m] + rng.normal(0, 0.4))
    for i, m in enumerate(order):
        repo._run("UPDATE models SET release_date = ? WHERE model_id = ?",
                  [date(2023, 1, 1) + timedelta(days=int(i * 1000 / len(order))), m])  # fmt: skip
    repo._run("UPDATE evaluations SET source_name = 'epoch_ai', model_version = model_id")
    # Ankkurit varhain julkaistuista, paljon havaituista malleista (lähtöpäivän sovitus
    # vaatii molemmat), kiinnitettynä todelliseen arvoon
    counts = dict(synth.obs.group_by("model_id").len().iter_rows())
    early = sorted(order[: len(order) // 3], key=lambda m: -counts.get(m, 0))[:6]
    lo, hi = min(early, key=synth.theta.get), max(early, key=synth.theta.get)
    anchors = {lo: synth.theta[lo], hi: synth.theta[hi]}
    summary = run_fit(repo, IRTConfig(anchors=anchors))
    current = repo.get_capability_with_dates(summary.run_id)
    yield {"repo": repo, "synth": synth, "anchors": anchors, "current": current}
    repo.close()


def _snapshot(tmp_path, world, cutoff: date, drop: set[str] = frozenset()) -> ea.Snapshot:
    """Tilannekuva: kaikkien ennen lähtöpäivää julkaistujen mallien tulokset, paitsi drop."""
    keys = (
        world["repo"]
        .get_evaluation_keys()
        .filter((pl.col("release_date") <= cutoff) & ~pl.col("model_id").is_in(list(drop)))
    )
    files = {f"{b}.csv": "Model version,score\n" + "".join(
        f"{v},0.5\n" for v in keys.filter(pl.col("benchmark_id") == b)["model_version"])
        for b in keys["benchmark_id"].unique().to_list()}  # fmt: skip
    ts = (cutoff - timedelta(days=3)).strftime("%Y%m%d") + "000000"
    p = tmp_path / ts / "benchmark_data.zip"
    p.parent.mkdir(parents=True)
    p.write_bytes(_zip(files))
    return ea.Snapshot(ts, p)


def _specs(world) -> list[FileSpec]:
    return [FileSpec(b, f"{b}.csv", "score", 1.0) for b in sorted(world["synth"].b)]


def test_vintage_backtest_end_to_end(tmp_path, world):
    as_of = date(2025, 9, 1)
    cutoff = date(2025, 3, 1)
    snaps = [_snapshot(tmp_path, world, cutoff)]
    cfg = ForecastConfig(n_boot=80, seed=0)
    res = vintage_backtest(world["repo"], snaps, _specs(world), IRTConfig(anchors=world["anchors"]),
                           cfg, as_of, world["current"],
                           cutoffs=[date(2025, 2, 1), cutoff])  # fmt: skip
    by_c = {o["cutoff"]: o for o in res["origins"]}
    assert by_c[date(2025, 2, 1)]["status"] == "no_snapshot"  # ennen ensimmäistä tilannekuvaa
    o = by_c[cutoff]
    assert o["status"] == "ok", o
    assert o["snapshot"] == date(2025, 2, 26) and o["end"] == as_of
    # Lähtöpäivän sovitus: vain silloin julkaistut mallit
    assert o["fit"]["n_models"] < world["current"].height
    assert o["n_points"] > 0 and o["level_shift"] is not None
    for key in METHODS:
        assert 0.0 <= o[key]["share_inside_p10_p90"] <= 1.0
        assert res[key]["n_points"] == o["n_points"]
        # Kypsät = julkaistu viimeistään as_of − 3 kk
        n_mature = sum(p["mature"] for p in o["points"])
        assert res[key + "_mature"].get("n_points", 0) == n_mature
    assert res["mature_before"] == date(2025, 6, 1)
    assert res["n_origins_ok"] == 1
    out = write_report(res, tmp_path / "r.md", irt_run="x", method_version="1")
    text = out.read_text(encoding="utf-8")
    assert "ajankohtaan sidottu" in text and "no_snapshot" in text and "Kypsät" in text


def test_vintage_excludes_results_missing_from_snapshot(tmp_path, world):
    """Tilannekuvasta puuttuvan mallin tulokset eivät ole lähtöpäivän sovituksessa."""
    from forecaster.models.pipeline import PipelineConfig
    from forecaster.models.vintage import vintage_scores

    cutoff = date(2025, 3, 1)
    keys = world["repo"].get_evaluation_keys()
    early = keys.filter(pl.col("release_date") <= cutoff)
    gone = early.group_by("model_id").len().sort("len", descending=True)["model_id"][0]
    snap = _snapshot(tmp_path, world, cutoff, drop={gone})
    ids = vintage_eval_ids(keys, ea.presence(snap.path, _specs(world)), cutoff)
    assert len(ids) == early.filter(pl.col("model_id") != gone).height
    scores, info = vintage_scores(world["repo"], ids, IRTConfig(anchors=world["anchors"]),
                                  PipelineConfig())  # fmt: skip
    assert gone not in set(scores["model_id"])
    assert info["n_models"] == scores.height


def test_presence_is_version_level():
    """Saman mallin versio A tilannekuvassa, B ei: vain A:n tulos on mukana."""
    keys = pl.DataFrame(
        [("e1", "m1", "b1", "m1-a", "epoch_ai", None, date(2025, 1, 1)),
         ("e2", "m1", "b1", "m1-b", "epoch_ai", None, date(2025, 1, 1))],
        schema=["eval_id", "model_id", "benchmark_id", "model_version", "source_name",
                "eval_date", "release_date"], orient="row",
    )  # fmt: skip
    assert vintage_eval_ids(keys, {("b1", "m1-a")}, date(2025, 6, 1)) == ["e1"]


def test_evaluate_origin_identical_when_vintage_equals_current(world):
    """Kun lähtöpäivän θ = nykyinen θ, ajankohtaan sidottu = nykyinen backtest, siirtymä 0."""
    cur = world["current"]
    o = evaluate_origin(cur, cur, ForecastConfig(n_boot=60, seed=1), date(2025, 3, 1),
                        date(2025, 9, 1), 1.0)  # fmt: skip
    assert o["status"] == "ok"
    assert o["level_shift"] == pytest.approx(0.0)
    assert o["vintage"] == o["lookahead"] == o["vintage_level_adjusted"]


def test_level_shift_recovers_constant_offset(world):
    """Siirtoinvarianssi: lähtöpäivän asteikko = nykyinen − c → tasosiirtymä c, ja
    tasosiirtymällä korjattu tulos = tavallinen backtest (ennuste on siirtoekvivariantti)."""
    cur, c = world["current"], 0.4
    shifted = cur.with_columns(pl.col("theta") - c)
    o = evaluate_origin(shifted, cur, ForecastConfig(n_boot=60, seed=1), date(2025, 3, 1),
                        date(2025, 9, 1), 1.0)  # fmt: skip
    assert o["level_shift"] == pytest.approx(c)
    for k in ("share_inside_p10_p90", "mean_error", "mean_abs_error"):
        assert o["vintage_level_adjusted"][k] == pytest.approx(o["lookahead"][k], abs=1e-9)
    assert o["vintage"]["mean_error"] == pytest.approx(o["lookahead"]["mean_error"] + c)


def test_aggregate_origin_mean_vs_pooled():
    """Lähtöpäivien keskiarvo ≠ pisteistä laskettu osuus; eri mallit lasketaan kerran."""
    from forecaster.models.vintage import _aggregate

    def pt(m, inside, err, mature=True):
        return {"model_id": m, "mature": mature,
                "vintage": {"inside": inside, "error": err, "width": 1.0}}  # fmt: skip

    origins = [
        {"status": "ok", "points": [pt("a", True, 0.1), pt("b", True, -0.1),
                                    pt("c", True, 0.0), pt("d", False, 0.5, mature=False)]},
        {"status": "ok", "points": [pt("d", False, 0.7, mature=False)]},
        {"status": "no_points"},
    ]  # fmt: skip
    a = _aggregate(origins, "vintage")
    assert a["mean_origin_share"] == pytest.approx((0.75 + 0.0) / 2)
    assert a["pooled_share"] == pytest.approx(3 / 5)
    assert a["n_points"] == 5 and a["n_unique_models"] == 4 and a["n_origins"] == 2
    assert a["origin_share_min_max"] == [0.0, 0.75]
    assert a["mean_error"] == pytest.approx((0.1 - 0.1 + 0.0 + 0.5 + 0.7) / 5)
    m = _aggregate(origins, "vintage", mature_only=True)
    assert m["n_points"] == 3 and m["pooled_share"] == 1.0 and m["n_origins"] == 1


def test_report_has_no_price_data(tmp_path):
    """Julkaistava raportti: vain θ-lukuja (ei hintoja lisensoimattomista lähteistä)."""
    res = {"as_of": date(2025, 9, 1), "holdout_months": 12, "mature_before": date(2025, 6, 1),
           "n_origins_ok": 0, "origins": []}  # fmt: skip
    text = write_report(res, tmp_path / "r.md", irt_run="x", method_version="1").read_text(
        encoding="utf-8")  # fmt: skip
    assert "llm-prices" not in text.lower() and "openrouter" not in text.lower()
    json.dumps(res, default=str)
