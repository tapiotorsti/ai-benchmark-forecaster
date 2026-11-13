"""K1:n hyväksymiskriteerit: skeema, upsertien idempotenssi, viiteavaimet, Pareto-kysely."""

from datetime import date

import duckdb
import pytest
from pydantic import ValidationError

from forecaster.config import BenchmarkConfig
from forecaster.db.models import AliasRow, EvaluationRow, ModelRow, PriceRow
from forecaster.db.repository import (
    EPSILON,
    SCHEMA_VERSION,
    Repository,
    SchemaVersionError,
    normalize_score,
)


@pytest.fixture
def repo():
    r = Repository.connect(":memory:")
    r.init_db()
    yield r
    r.close()


def bench(bid="b1", **kw):
    base = dict(benchmark_id=bid, name=bid.upper(), domain="math", metric_type="accuracy")
    return BenchmarkConfig(**(base | kw))


def model(mid, released, **kw):
    return ModelRow(model_id=mid, display_name=mid, provider="acme", release_date=released, **kw)


def ev(mid, bid, raw, source="src", version=""):
    return EvaluationRow(
        model_id=mid, benchmark_id=bid, raw_score=raw, source_name=source, model_version=version
    )


@pytest.fixture
def seeded(repo):
    repo.upsert_benchmarks([bench("b1"), bench("b2", domain="coding", weight=2.0)])
    repo.upsert_models(
        [
            model("m1", date(2024, 1, 1)),
            model("m2", date(2024, 6, 1)),
            model("m3", date(2025, 1, 1)),
        ]
    )
    return repo


# --- Skeema ---------------------------------------------------------------


def test_init_db_creates_all_tables_and_is_idempotent(repo):
    assert repo.init_db() == SCHEMA_VERSION  # toinen kerta
    expected = {
        "schema_version", "models", "model_aliases", "benchmarks", "evaluations",
        "model_prices", "irt_runs", "capability_scores", "benchmark_irt_params",
        "ingest_runs", "ingest_quarantine", "forecast_runs",
    }  # fmt: skip
    assert expected <= set(repo.table_counts())
    assert repo.table_counts()["schema_version"] == 1


def test_newer_schema_version_is_rejected(repo):
    repo._run("INSERT INTO schema_version (version) VALUES (?)", [SCHEMA_VERSION + 1])
    with pytest.raises(SchemaVersionError, match="uudempi"):
        repo.init_db()


def test_file_database_persists(tmp_path):
    path = tmp_path / "sub" / "t.duckdb"
    with Repository.connect(path) as r:
        r.init_db()
        r.upsert_benchmarks([bench()])
    with Repository.connect(path, read_only=True) as r:
        assert r.table_counts()["benchmarks"] == 1


# --- Normalisointi --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(50, 0.5), (0, EPSILON), (100, 1 - EPSILON), (-5, EPSILON), (120, 1 - EPSILON)],
)
def test_normalize_score(raw, expected):
    assert normalize_score(raw, 0, 100) == pytest.approx(expected)


def test_normalized_score_computed_from_benchmark_range(seeded):
    seeded.upsert_benchmarks([bench("elo", metric_type="elo", raw_min=800, raw_max=1600)])
    seeded.upsert_evaluations([ev("m1", "elo", 1200)])
    y = seeded._run("SELECT normalized_score FROM evaluations")[0][0]
    assert y == pytest.approx(0.5)


# --- Upsertien idempotenssi ----------------------------------------------


def test_upserts_are_idempotent_and_update_values(seeded):
    rows = [ev("m1", "b1", 40), ev("m2", "b1", 60)]
    seeded.upsert_evaluations(rows)
    seeded.upsert_evaluations(rows)
    assert seeded.table_counts()["evaluations"] == 2

    seeded.upsert_evaluations([ev("m1", "b1", 45)])
    assert seeded.table_counts()["evaluations"] == 2
    raw = seeded._run("SELECT raw_score FROM evaluations WHERE model_id = 'm1'")[0][0]
    assert raw == 45

    seeded.upsert_models([model("m1", date(2024, 1, 2))])
    assert seeded.table_counts()["models"] == 3


def test_upsert_keeps_outlier_flag(seeded):
    seeded.upsert_evaluations([ev("m1", "b1", 40)])
    seeded._run("UPDATE evaluations SET is_outlier = TRUE")
    seeded.upsert_evaluations([ev("m1", "b1", 41)])
    assert seeded._run("SELECT is_outlier FROM evaluations")[0][0] is True


def test_duplicate_keys_in_one_batch_rejected(seeded):
    with pytest.raises(ValueError, match="toistuvia"):
        seeded.upsert_evaluations([ev("m1", "b1", 40), ev("m1", "b1", 50)])


def test_eval_id_is_deterministic_and_version_sensitive():
    assert ev("m1", "b1", 1).eval_id == ev("m1", "b1", 2).eval_id
    assert ev("m1", "b1", 1).eval_id != ev("m1", "b1", 1, version="_max").eval_id


# --- Viiteavaimet ja validointi -------------------------------------------


def test_unknown_model_violates_foreign_key(seeded):
    with pytest.raises(duckdb.ConstraintException):
        seeded.upsert_evaluations([ev("ghost", "b1", 50)])


def test_unknown_benchmark_rejected_with_clear_message(seeded):
    with pytest.raises(ValueError, match="nope"):
        seeded.upsert_evaluations([ev("m1", "nope", 50)])


def test_alias_to_unknown_model_violates_foreign_key(seeded):
    with pytest.raises(duckdb.ConstraintException):
        seeded.upsert_aliases([AliasRow(source_name="s", alias="x", model_id="ghost")])


def test_row_models_reject_bad_input():
    with pytest.raises(ValidationError):
        ModelRow(model_id="", display_name="x", provider="p", release_date=date(2024, 1, 1))
    with pytest.raises(ValidationError):
        PriceRow(model_id="m", valid_from=date(2024, 1, 1), source_name="s",
                 input_cost_per_1m=-1, output_cost_per_1m=1)  # fmt: skip


# --- Aliakset -------------------------------------------------------------


def test_resolve_aliases_reports_unknown(seeded):
    seeded.upsert_aliases(
        [
            AliasRow(source_name="epoch", alias="gpt-x_max", model_id="m1"),
            AliasRow(source_name="epoch", alias="gpt-x_high", model_id="m1"),
            AliasRow(source_name="other", alias="mystery", model_id="m2"),
        ]
    )
    found, unknown = seeded.resolve_aliases("epoch", ["gpt-x_max", "gpt-x_high", "mystery"])
    assert found == {"gpt-x_max": "m1", "gpt-x_high": "m1"}
    assert unknown == ["mystery"]


# --- Havaintomatriisi -----------------------------------------------------


def test_observation_matrix_aggregates_versions_and_sources(seeded):
    seeded.upsert_evaluations(
        [
            ev("m1", "b1", 40, source="a", version="_low"),
            ev("m1", "b1", 60, source="a", version="_max"),  # paras versio → 0.6
            ev("m1", "b1", 80, source="b"),  # toinen lähde → keskiarvo (0.6+0.8)/2
            ev("m2", "b2", 30),
        ]
    )
    m = seeded.get_observation_matrix()
    assert m.height == 2
    r = m.filter(model_id="m1").row(0, named=True)
    assert r["y"] == pytest.approx(0.7)
    assert r["n_sources"] == 2
    assert m.filter(benchmark_id="b2")["weight"][0] == 2.0


def test_observation_matrix_domain_filter_and_outliers(seeded):
    seeded.upsert_evaluations([ev("m1", "b1", 40), ev("m1", "b2", 50)])
    assert seeded.get_observation_matrix("coding")["benchmark_id"].to_list() == ["b2"]
    assert seeded.get_observation_matrix("overall").height == 2
    seeded._run("UPDATE evaluations SET is_outlier = TRUE WHERE benchmark_id = 'b1'")
    assert seeded.get_observation_matrix().height == 1
    assert seeded.get_observation_matrix(exclude_outliers=False).height == 2


# --- Pareto-rintama -------------------------------------------------------


def test_pareto_frontier(repo):
    released = {
        "a": date(2023, 1, 1),  # 0.0  rintama (ensimmäinen)
        "b": date(2023, 6, 1),  # -1.0 ei
        "c": date(2023, 9, 1),  # 0.5  rintama
        "d": date(2024, 1, 1),  # 0.4  ei (alle 0.5)
        "e": date(2024, 3, 1),  # 1.2  rintama, saman päivän paras
        "f": date(2024, 3, 1),  # 0.9  ei: sama päivä, heikompi (A11)
        "g": date(2024, 6, 1),  # 1.2  ei: tasapeli ei ylitä (tiukka >)
        "h": date(2025, 1, 1),  # 2.0  rintama
    }
    theta = {"a": 0.0, "b": -1.0, "c": 0.5, "d": 0.4, "e": 1.2, "f": 0.9, "g": 1.2, "h": 2.0}
    repo.upsert_models([model(m, d) for m, d in released.items()])
    repo._run("INSERT INTO irt_runs (run_id, domain) VALUES ('r1', 'overall')")
    for mid, th in theta.items():
        repo._run(
            "INSERT INTO capability_scores (run_id, model_id, domain, theta) "
            "VALUES ('r1', ?, 'overall', ?)",
            [mid, th],
        )
    # Toisen ajon data ei saa vuotaa
    repo._run("INSERT INTO irt_runs (run_id, domain) VALUES ('r2', 'overall')")
    repo._run(
        "INSERT INTO capability_scores (run_id, model_id, domain, theta) "
        "VALUES ('r2', 'b', 'overall', 9)"
    )
    frontier = repo.get_pareto_frontier("r1")
    assert frontier["model_id"].to_list() == ["a", "c", "e", "h"]


# --- Hinnat ---------------------------------------------------------------


def test_price_history_lookup(seeded):
    seeded.upsert_prices(
        [
            PriceRow(model_id="m1", valid_from=date(2024, 1, 1), source_name="or",
                     input_cost_per_1m=10, output_cost_per_1m=30),
            PriceRow(model_id="m1", valid_from=date(2024, 6, 1), source_name="or",
                     input_cost_per_1m=2, output_cost_per_1m=6),
            PriceRow(model_id="m1", valid_from=date(2024, 6, 1), source_name="other",
                     input_cost_per_1m=1, output_cost_per_1m=5),
        ]
    )  # fmt: skip
    assert seeded.get_price_at("m1", date(2023, 12, 31)) is None
    jan = seeded.get_price_at("m1", date(2024, 3, 1))
    assert jan["blended"] == pytest.approx((3 * 10 + 30) / 4)
    jun = seeded.get_price_at("m1", date(2024, 7, 1))
    assert jun["source_name"] == "other"  # saman päivän halvin
    assert jun["blended"] == pytest.approx(2.0)


# --- Ingest-loki ----------------------------------------------------------


def test_ingest_run_log_and_quarantine(repo):
    repo.start_ingest_run("run1", "epoch_ai")
    repo.quarantine("run1", "epoch_ai", {"model": "x", "date": None}, "release_date puuttuu")
    repo.finish_ingest_run(
        "run1", status="ok", rows_fetched=10, rows_upserted=9, rows_quarantined=1
    )
    status, q = repo._run("SELECT status, rows_quarantined FROM ingest_runs WHERE run_id = 'run1'")[
        0
    ]
    assert (status, q) == ("ok", 1)
    assert repo.table_counts()["ingest_quarantine"] == 1
    with pytest.raises(ValueError):
        repo.finish_ingest_run("run1", status="weird")
