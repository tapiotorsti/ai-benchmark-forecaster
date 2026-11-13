"""K3:n hyväksymiskriteerit: adapterit toimivat tallennetuilla tiedostoilla (ei verkkoa),
kahdesti ajettu ingest ei tuota duplikaatteja, ja raportti kertoo uudet mallit,
tuntemattomat nimet ja karanteeniin menneet rivit."""

import shutil
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pytest

from forecaster.config import EpochBenchmarksFile, Paths
from forecaster.db.repository import Repository
from forecaster.ingest import epoch_ai, internal, openrouter
from forecaster.ingest.base import download, slugify

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

MAPPING = EpochBenchmarksFile.model_validate(
    {
        "benchmarks": {
            "GPQA diamond": {"benchmark_id": "gpqa_diamond", "domain": "reasoning"},
            "Aider polyglot": {"benchmark_id": "aider_polyglot", "domain": "coding"},
            "HLE": {
                "benchmark_id": "hle",
                "domain": "reasoning",
                "source_file": "hle_external.csv",
                "score_column": "Accuracy",
            },
            "Not In Metadata": {"benchmark_id": "ghost", "domain": "math"},
        }
    }
)


@pytest.fixture
def env(tmp_path):
    """Väliaikainen projektijuuri: oikeat config-tiedostot, tyhjät aliakset."""
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    (tmp_path / "config" / "model_aliases.yaml").write_text("aliases: []\n", encoding="utf-8")
    paths = Paths(root=tmp_path)
    repo = Repository.connect(":memory:")
    repo.init_db()
    yield paths, repo
    repo.close()


def run_epoch(env):
    paths, repo = env
    return epoch_ai.ingest(repo, paths, FIXTURES / "epoch_ai", mapping=MAPPING)


def count(repo, table):
    return repo.table_counts()[table]


# --- Apufunktiot ------------------------------------------------------------


def test_slugify():
    assert slugify("Claude 3.5 Sonnet (October 2024)") == "claude-3-5-sonnet-october-2024"
    assert slugify("Käyttäjä Ä-malli") == "kayttaja-a-malli"


def test_download_with_mock_transport(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=b"payload")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = download("https://example.test/x", tmp_path / "x.bin", client=client, retries=2)
    assert out.read_bytes() == b"payload"
    assert calls["n"] == 2


def test_extract_rejects_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.txt", "x")
    with pytest.raises(ValueError, match="Vaarallinen"):
        epoch_ai.extract(archive, tmp_path / "out")


# --- Epoch AI ---------------------------------------------------------------


def test_epoch_ingest_contents(env):
    paths, repo = env
    report = run_epoch(env)

    assert count(repo, "benchmarks") == 3
    assert report.unmapped_benchmarks == ["Mystery Bench"]
    assert any("ghost" in n or "Not In Metadata" in n for n in report.notes)

    # Mallit: Model C karanteeniin (ei päivämäärää), tunnistetörmäys ratkaistu
    ids = repo.existing_ids("models", "model_id")
    assert ids == {"model-a", "model-b-v2", "model-b-v2-2"}
    assert report.rows_quarantined == 1 and count(repo, "ingest_quarantine") == 1
    assert report.new_models == 3
    a = repo._one("SELECT release_date, provider, estimated_compute_flops FROM models "
                  "WHERE model_id = 'model-a'")  # fmt: skip
    assert str(a[0]) == "2024-01-10" and a[1] == "Acme" and a[2] == pytest.approx(1e24)
    assert repo._one("SELECT is_open_weights FROM models WHERE model_id = 'model-b-v2'")[0]

    # Tulokset: 4 GPQA + 2 Aider + 1 HLE
    assert count(repo, "evaluations") == 7
    assert report.unknown_aliases == ["unknown-model"]
    gpqa_a = repo._one(
        "SELECT raw_score, normalized_score, eval_date FROM evaluations "
        "WHERE model_version = 'model-a_high' AND benchmark_id = 'gpqa_diamond'"
    )
    assert gpqa_a[0] == pytest.approx(0.6)  # toistuvista paras
    assert gpqa_a[1] == pytest.approx(0.6 / 0.97)  # raw_max = score_ceiling (A8)
    assert str(gpqa_a[2]) == "2024-02-01"
    aider = repo._one(
        "SELECT raw_score FROM evaluations WHERE benchmark_id = 'aider_polyglot' "
        "AND model_id = 'model-b-v2'"
    )
    assert aider[0] == pytest.approx(0.60)  # skaala 0.01

    floor = repo._one("SELECT guessing_floor FROM benchmarks WHERE benchmark_id = 'gpqa_diamond'")
    assert floor[0] == pytest.approx(0.25 / 0.97)

    # Havaintomatriisi: versioiden paras (A9)
    m = repo.get_observation_matrix().filter(model_id="model-a", benchmark_id="gpqa_diamond")
    assert m["y"][0] == pytest.approx(0.6 / 0.97)

    status = repo._one("SELECT status, rows_upserted FROM ingest_runs")
    assert status == ("ok", 7)


def test_epoch_ingest_is_idempotent(env):
    _, repo = env
    run_epoch(env)
    before = repo.table_counts()
    second = run_epoch(env)
    after = repo.table_counts()
    for table in ("models", "benchmarks", "evaluations", "model_aliases"):
        assert after[table] == before[table], table
    assert second.new_models == 0
    assert after["ingest_runs"] == 2


def test_failed_ingest_rolls_back_and_is_logged(env):
    paths, repo = env
    (paths.config_dir / "model_aliases.yaml").write_text(
        "aliases:\n  - {source_name: epoch_ai, alias: x, model_id: does-not-exist}\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception):  # noqa: B017 – FK-virhe DuckDB:ltä
        run_epoch(env)
    assert count(repo, "evaluations") == 0
    assert count(repo, "models") == 0
    assert repo._one("SELECT status FROM ingest_runs")[0] == "failed"


# --- OpenRouter -------------------------------------------------------------


def test_openrouter_parse_skips_variants():
    import json

    payload = json.loads((FIXTURES / "openrouter" / "models.json").read_text())
    models, skipped = openrouter.parse(payload)
    assert [m.id for m in models] == ["acme/model-a", "acme/model-a-fast", "beta/model-b-v2",
                                      "x/model-aa"]  # fmt: skip
    assert skipped == 4
    assert models[0].input_per_1m == pytest.approx(3.0)
    assert models[0].output_per_1m == pytest.approx(15.0)


def test_openrouter_ingest(env):
    paths, repo = env
    run_epoch(env)
    report = openrouter.ingest(
        repo, paths, FIXTURES / "openrouter" / "models.json", as_of=date(2026, 11, 13)
    )
    assert report.rows_upserted == 2
    price = repo.get_price_at("model-a", date(2026, 11, 13))
    assert price["blended"] == pytest.approx((3 * 3.0 + 15.0) / 4)
    assert repo.get_price_at("model-b-v2", date(2026, 11, 13)) is not None
    assert len(report.unknown_aliases) == 2
    assert any("x/model-aa" in u and "ehdotus: model-a" in u for u in report.unknown_aliases)

    # Toinen päivä → hintahistoria kasvaa, sama päivä → ei duplikaatteja
    openrouter.ingest(repo, paths, FIXTURES / "openrouter" / "models.json", date(2026, 11, 13))
    openrouter.ingest(repo, paths, FIXTURES / "openrouter" / "models.json", date(2026, 11, 14))
    assert count(repo, "model_prices") == 4


def test_openrouter_manual_alias_overrides(env):
    paths, repo = env
    run_epoch(env)
    (paths.config_dir / "model_aliases.yaml").write_text(
        "aliases:\n  - {source_name: openrouter, alias: x/model-aa, model_id: model-b-v2-2}\n",
        encoding="utf-8",
    )
    report = openrouter.ingest(repo, paths, FIXTURES / "openrouter" / "models.json", date.today())
    assert report.rows_upserted == 3
    assert not any("x/model-aa" in u for u in report.unknown_aliases)


def test_openrouter_requires_models(env):
    paths, repo = env
    with pytest.raises(ValueError, match="epoch_ai"):
        openrouter.ingest(repo, paths, FIXTURES / "openrouter" / "models.json", date.today())
    assert repo._one("SELECT status FROM ingest_runs")[0] == "failed"


# --- Sisäiset tulokset --------------------------------------------------------


def test_internal_csv(env, tmp_path):
    paths, repo = env
    run_epoch(env)
    csv = tmp_path / "own.csv"
    csv.write_text(
        "model_id,benchmark_id,raw_score,eval_date\n"
        "model-a,hle,0.3,2026-10-01\n"
        "nobody,hle,0.3,\n"
        "model-a,no_such_bench,0.3,\n"
        "model-b-v2,hle,not-a-number,\n",
        encoding="utf-8",
    )
    report = internal.ingest(repo, paths, csv)
    assert report.rows_upserted == 1
    assert report.rows_quarantined == 3
    assert report.unknown_aliases == ["nobody"]
    reasons = {r[0] for r in repo._run(
        "SELECT reason FROM ingest_quarantine WHERE source_name = 'internal'")}  # fmt: skip
    assert "tuntematon benchmark_id" in reasons
    # Sama rivi uudelleen → ei duplikaattia
    internal.ingest(repo, paths, csv)
    n = repo._one("SELECT count(*) FROM evaluations WHERE source_name = 'internal'")[0]
    assert n == 1


def test_internal_csv_missing_columns(env, tmp_path):
    paths, repo = env
    csv = tmp_path / "bad.csv"
    csv.write_text("model,score\nx,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="pakollisia"):
        internal.ingest(repo, paths, csv)


# --- Oikea konfiguraatio --------------------------------------------------------


def test_repository_epoch_mapping_covers_real_metadata():
    """Jos oikea Epoch-data on ladattu, jokainen ECI-benchmark on kuvattu."""
    raw = sorted((PROJECT_ROOT / "data" / "raw" / "epoch_ai").glob("*/extracted"))
    if not raw:
        pytest.skip("Epoch-dataa ei ole ladattu")
    import polars as pl

    from forecaster.config import load_epoch_benchmarks

    meta = pl.read_csv(raw[-1] / "benchmark_metadata.csv", infer_schema_length=0)
    mapping = load_epoch_benchmarks(Paths(root=PROJECT_ROOT))
    _, specs, unmapped, _ = epoch_ai.build_benchmarks(meta, mapping)
    assert unmapped == []
    missing_files = [s.source_file for s in specs if not (raw[-1] / s.source_file).is_file()]
    assert missing_files == []
