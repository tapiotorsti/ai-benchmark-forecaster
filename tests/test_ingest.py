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
from forecaster.ingest import epoch_ai, internal, llm_prices, openrouter
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


def test_epoch_manual_alias_maps_evaluations(env):
    """Käsin tehty Epoch-alias (versio puuttuu model_metadata.csv:stä) kohdistaa myös
    tulosrivit, ei vain aliastaulua (bugi: Llama-2-70b-hf jäi tunnistamattomaksi)."""
    paths, repo = env
    (paths.config_dir / "model_aliases.yaml").write_text(
        "aliases:\n  - {source_name: epoch_ai, alias: unknown-model, model_id: model-a}\n",
        encoding="utf-8",
    )
    report = run_epoch(env)
    assert report.unknown_aliases == []
    row = repo._one(
        "SELECT model_id, raw_score FROM evaluations "
        "WHERE model_version = 'unknown-model' AND benchmark_id = 'gpqa_diamond'"
    )
    assert row == ("model-a", pytest.approx(0.9))


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
    # Tuntemattomat nimet ovat pelkkiä tunnisteita (vertailtavia ajojen välillä, K7);
    # ehdotukset erikseen ja yhteenvedossa
    assert "x/model-aa" in report.unknown_aliases
    assert report.alias_suggestions["x/model-aa"] == "model-a"
    assert "x/model-aa  → ehdotus: model-a" in report.summary()

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


# --- llm-prices (hintahistoria) + OpenRouter-täydennys --------------------------


def test_llm_prices_history_and_backfill(env):
    paths, repo = env
    run_epoch(env)
    openrouter.ingest(repo, paths, FIXTURES / "openrouter" / "models.json", date(2026, 11, 13))
    report = llm_prices.ingest(repo, paths, FIXTURES / "llm_prices" / "historical-v1.json")

    assert report.unknown_aliases == ["mystery-model"]
    # from_date = null → julkaisupäivä (model-a: 2024-01-10); päivätyt id:t → sama malli
    jan = repo.get_price_at("model-a", date(2024, 3, 1))
    assert jan["source_name"] == "llm_prices"
    assert jan["blended"] == pytest.approx((3 * 3 + 15) / 4)  # halvempi kahdesta saman päivän
    assert repo.get_price_at("model-a", date(2024, 7, 1))["blended"] == pytest.approx(2.0)
    assert repo.get_price_at("model-a", date(2024, 1, 9)) is None  # ennen julkaisua

    # model-b-v2: ei llm-prices-hintaa → OpenRouter-hinta voimaan julkaisupäivästä
    b = repo.get_price_at("model-b-v2", date(2024, 7, 1))
    assert b["source_name"] == "openrouter_backfill"
    assert b["blended"] == pytest.approx((3 * 0.5 + 1.5) / 4)
    # model-a:lla on historia → ei täydennystä
    n = repo._one("SELECT count(*) FROM model_prices WHERE source_name = 'openrouter_backfill' "
                  "AND model_id = 'model-a'")[0]  # fmt: skip
    assert n == 0

    # Uudelleenajo: ei duplikaatteja, täydennys rakennetaan uudelleen
    before = repo.table_counts()["model_prices"]
    llm_prices.ingest(repo, paths, FIXTURES / "llm_prices" / "historical-v1.json")
    assert repo.table_counts()["model_prices"] == before


def test_llm_prices_resolution_rules():
    release = {
        "gpt-4o-may-2024": date(2024, 5, 13),
        "gpt-4o-nov-2024": date(2024, 11, 20),
        "gpt-4o-mini": date(2024, 7, 18),
        "gemini-2-5-pro": date(2025, 6, 17),
    }
    # päivämäärätön → uusin päivätty versio (ei gpt-4o-mini)
    assert llm_prices.resolve({"id": "gpt-4o", "name": "GPT-4o"}, release) == "gpt-4o-nov-2024"
    assert llm_prices.resolve({"id": "gpt-4o-mini"}, release) == "gpt-4o-mini"
    # preview/latest-pääte riisutaan
    assert llm_prices.resolve({"id": "gemini-2.5-pro-preview-03-25"}, release) == "gemini-2-5-pro"
    assert llm_prices.resolve({"id": "unknown-x"}, release) is None


def test_llm_prices_explicit_date_and_latest():
    """K5-katselmointi: päiväyspääte valitsee lähimmän version; -latest ei kiinnity vanhaan."""
    release = {
        "mistral-large": date(2024, 2, 26),
        "mistral-medium": date(2023, 12, 11),
        "claude-3-5-sonnet-june-2024": date(2024, 6, 20),
        "claude-3-5-sonnet-october-2024": date(2024, 10, 22),
    }
    # -latest + päivämäärätön vanha malli → tuntematon (nykyhinta ei saa kiinnittyä 2024-02:een)
    assert llm_prices.resolve({"id": "mistral-large-latest"}, release) is None
    # from_date annettu → hinta on päivätty, kohdistus sallittu
    assert (
        llm_prices.resolve({"id": "mistral-large-latest", "from_date": "2024-11-18"}, release)
        == "mistral-large"
    )
    # -YYMM: Medium 3 (2025-05) ≠ vuoden 2023 mistral-medium
    assert llm_prices.resolve({"id": "mistral-medium-2505"}, release) is None
    # -YYYYMMDD → lähin versio, ei uusin
    assert (
        llm_prices.resolve({"id": "claude-3-5-sonnet-20240620"}, release)
        == "claude-3-5-sonnet-june-2024"
    )
    assert (
        llm_prices.resolve({"id": "claude-3-5-sonnet-20241022"}, release)
        == "claude-3-5-sonnet-october-2024"
    )
    # -MMDD ei kerro vuotta → vanha sääntö
    assert llm_prices.explicit_date("gpt-4-0613") is None
    assert llm_prices.explicit_date("mistral-large-2411") == date(2024, 11, 1)


def test_manual_prices_and_context_tiers(env):
    paths, repo = env
    run_epoch(env)
    (paths.config_dir / "manual_prices.yaml").write_text(
        "prices:\n  - {model_id: model-b-v2-2, input: 30, output: 60}\n"
        "  - {model_id: not-a-model, input: 1, output: 1}\n",
        encoding="utf-8",
    )
    payload = paths.root / "p.json"
    payload.write_text(
        '{"prices": [{"id": "model-a-200k", "name": "Model A long", "input": 9, "output": 9,'
        ' "from_date": null, "to_date": null}]}',
        encoding="utf-8",
    )
    report = llm_prices.ingest(repo, paths, payload)
    assert report.rows_skipped == 1  # kontekstitaso ohitettu
    assert repo.get_price_at("model-a", date(2025, 1, 1)) is None
    m = repo.get_price_at("model-b-v2-2", date(2024, 6, 2))  # julkaisupäivä 2024-06-02
    assert m["source_name"] == "manual" and m["blended"] == pytest.approx(37.5)
    assert any("not-a-model" in n for n in report.notes)


def test_price_at_dates_asof(env):
    paths, repo = env
    run_epoch(env)
    llm_prices.ingest(repo, paths, FIXTURES / "llm_prices" / "historical-v1.json")
    df = repo.get_price_at_dates([date(2024, 1, 1), date(2024, 3, 1), date(2024, 7, 1)])
    a = df.filter(model_id="model-a").sort("at")
    assert a["at"].to_list() == [date(2024, 3, 1), date(2024, 7, 1)]  # 1.1. ennen julkaisua
    assert a["blended"].to_list() == pytest.approx([6.0, 2.0])


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
