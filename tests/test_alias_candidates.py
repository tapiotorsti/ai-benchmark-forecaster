"""Aliasten kuratointityökalu: tunnistus, hylkäyssäännöt ja ehdotusten valinta.
Data on keksittyä (ei verkkoa, ei lisensoimatonta dataa)."""

import json
from datetime import date

import polars as pl
import pytest

from forecaster.config import Paths
from forecaster.ingest.alias_candidates import (
    SourceName,
    candidates,
    load_source_names,
    match,
    tokenize,
    vendor_matches,
    yaml_line,
)


def test_tokenize_versions_sizes_and_dates():
    t = tokenize("meta-llama/llama-3.2-1b-instruct", "Meta: Llama 3.2 1B Instruct")
    assert t.versions == {"3.2"} and t.sizes == {"1b"} and t.words == {"llama"}
    # slugin väliviiva numeroiden välissä = versiopiste; päivämäärät pudotetaan
    assert tokenize("qwen-3-6-flash").versions == {"3.6"}
    assert tokenize("claude-3-5-sonnet-20241022").versions == {"3.5"}
    assert tokenize("x/model-2025-07-28").versions == frozenset()
    assert "plus" in tokenize("Command R+").words
    assert tokenize("gpt-4o-128k").words == {"gpt", "o"}


@pytest.mark.parametrize(
    ("alias", "model", "reason"),
    [
        ("meta-llama/llama-3.2-3b-instruct", "Llama 3.2 1B", "koko"),
        ("google/gemini-2.0-flash", "Gemini 2.5 Flash", "versio"),
        ("google/gemini-2.5-flash-lite", "Gemini 2.5 Flash", "variantti (lite)"),
        ("cohere/command-r-08-2024", "Command R+", "variantti (plus)"),
        ("morph/morph-v3-large", "Mistral Large 3", "ei yhteistä perhenimeä"),
        ("gryphe/mythomax-l2-13b", "PaLM 2-L", "ei yhteistä perhenimeä"),
    ],
)
def test_match_rejects_different_models(alias, model, reason):
    score, why = match(tokenize(alias), tokenize(model))
    assert score == 0.0 and why == reason


def test_match_accepts_same_model_without_noise_words():
    score, why = match(
        tokenize("meta-llama/llama-3.3-70b-instruct", "Meta: Llama 3.3 70B Instruct"),
        tokenize("llama-3-3-70b", "Llama 3.3 70B"),
    )
    assert why is None and score == 1.0


def test_vendor_matches_prefix_and_family():
    assert vendor_matches("mistralai", "Mistral AI", tokenize("Mistral Large 3"))
    assert vendor_matches("qwen", "Alibaba", tokenize("Qwen 3.6 Flash"))  # perhenimi
    assert not vendor_matches("sao10k", "Meta AI", tokenize("Llama 3-8B"))


def _models(rows):
    return pl.DataFrame(
        rows,
        schema={"model_id": pl.String, "display_name": pl.String, "provider": pl.String,
                "release_date": pl.Date, "theta": pl.Float64, "theta_se": pl.Float64,
                "has_price": pl.Boolean},
        orient="row",
    )  # fmt: skip


MODELS = _models([
    ("nova-apr", "Nova 2.5 (Apr 2025)", "Acme", date(2025, 4, 17), 1.0, 0.1, False),
    ("nova-jun", "Nova 2.5 (Jun 2025)", "Acme", date(2025, 6, 17), 1.2, 0.1, False),
    ("kimi-jul", "Kimi K2 (Jul 2025)", "Moonshot", date(2025, 7, 12), 1.0, 0.1, False),
    ("kimi-sep", "Kimi K2 (Sep 2025)", "Moonshot", date(2025, 9, 5), 1.1, 0.1, True),
    ("llama-8b", "Llama 3-8B", "Meta AI", date(2024, 4, 18), -0.7, 0.1, False),
])  # fmt: skip


def test_each_name_goes_to_closest_model_only():
    """Päivämäärätön nimi → lähin julkaisu; nimi, joka sopii paremmin hinnalliseen malliin,
    ei päädy hinnattomalle (kimi-k2-0905 → syyskuun versio, ei heinäkuun)."""
    names = [
        SourceName("openrouter", "acme/nova-2.5", "Acme: Nova 2.5", "acme", date(2025, 6, 18)),
        SourceName("openrouter", "moonshotai/kimi-k2-0905", "MoonshotAI: Kimi K2 0905",
                   "moonshotai", date(2025, 9, 5)),
    ]  # fmt: skip
    c = candidates(names, MODELS)
    assert c.select("model_id", "alias").rows() == [("nova-jun", "acme/nova-2.5")]
    allc = candidates(names, MODELS, only_unpriced=False)
    assert ("kimi-sep", "moonshotai/kimi-k2-0905") in allc.select("model_id", "alias").rows()


def test_candidate_notes_and_yaml_line():
    names = [
        SourceName("openrouter", "sao10k/l3-lunaris-8b", "Sao10K: Llama 3 8B Lunaris", "sao10k",
                   date(2024, 8, 13)),
        SourceName("llm_prices", "nova-2.5-latest", "Nova 2.5 Latest", "acme", None),
    ]  # fmt: skip
    c = candidates(names, MODELS)
    notes = dict(zip(c["alias"], c["huom"], strict=True))
    assert "eri valmistaja" in notes["sao10k/l3-lunaris-8b"]
    assert "liukuva nimi" in notes["nova-2.5-latest"]
    row = c.filter(pl.col("alias") == "sao10k/l3-lunaris-8b").row(0, named=True)
    assert yaml_line(row) == (
        "  - {source_name: openrouter, alias: sao10k/l3-lunaris-8b, model_id: llama-8b}"
    )


def test_load_source_names_reads_latest_raw_files(tmp_path):
    paths = Paths(root=tmp_path)
    (paths.raw_dir / "openrouter").mkdir(parents=True)
    (paths.raw_dir / "llm_prices").mkdir(parents=True)
    (paths.raw_dir / "openrouter" / "models_2026-11-01.json").write_text(
        json.dumps({"data": [{"id": "acme/old", "name": "Old"}]}), encoding="utf-8")  # fmt: skip
    (paths.raw_dir / "openrouter" / "models_2026-11-16.json").write_text(
        json.dumps({"data": [
            {"id": "acme/flash-2.5", "name": "Acme: Flash 2.5", "created": 1750204800},
            {"id": "acme/known", "name": "Known"},
        ]}), encoding="utf-8")  # fmt: skip
    (paths.raw_dir / "llm_prices" / "historical-v1_2026-11-16.json").write_text(
        json.dumps({"prices": [
            {"id": "flash-x", "vendor": "acme", "name": "Flash X", "from_date": "2025-01-02"},
            {"id": "flash-x", "vendor": "acme", "name": "Flash X", "from_date": "2025-06-01"},
        ]}), encoding="utf-8")  # fmt: skip
    unknown = pl.DataFrame({
        "source_name": ["openrouter", "openrouter", "llm_prices"],
        "kind": ["alias", "alias", "alias"],
        "name": ["acme/flash-2.5", "acme/old", "flash-x"],
        "run_id": ["r1", "r1", "r2"],
    })  # fmt: skip
    names = {(n.source_name, n.alias): n for n in load_source_names(paths, unknown)}
    assert set(names) == {("openrouter", "acme/flash-2.5"), ("llm_prices", "flash-x")}
    assert names[("openrouter", "acme/flash-2.5")].seen_date == date(2025, 6, 18)
    assert names[("openrouter", "acme/flash-2.5")].vendor == "acme"
    assert names[("llm_prices", "flash-x")].seen_date == date(2025, 1, 2)  # ensimmäinen rivi
