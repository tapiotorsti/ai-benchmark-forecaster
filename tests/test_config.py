from pathlib import Path

import pytest
from pydantic import ValidationError

from forecaster.config import (
    AnchorsFile,
    BenchmarkConfig,
    BenchmarksFile,
    find_project_root,
    get_paths,
    load_anchors,
    load_benchmarks,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_project_root_found_from_subdirectory(monkeypatch):
    monkeypatch.delenv("FORECASTER_ROOT", raising=False)
    assert find_project_root(PROJECT_ROOT / "tests") == PROJECT_ROOT


def test_repository_config_files_are_valid():
    paths = get_paths(PROJECT_ROOT)
    from forecaster.config import (
        check_benchmark_id_conflicts,
        load_epoch_benchmarks,
        load_model_aliases,
    )

    benchmarks = load_benchmarks(paths)
    anchors = load_anchors(paths)
    epoch = load_epoch_benchmarks(paths)
    load_model_aliases(paths)
    check_benchmark_id_conflicts(benchmarks, epoch)
    assert len(epoch.benchmarks) > 0
    assert len(anchors.anchors) >= 2


def test_benchmark_id_conflict_detected():
    from forecaster.config import EpochBenchmarksFile, check_benchmark_id_conflicts

    manual = BenchmarksFile(benchmarks=[_bench(benchmark_id="mmlu")])
    epoch = EpochBenchmarksFile(benchmarks={"MMLU": {"benchmark_id": "mmlu", "domain": "general"}})
    with pytest.raises(ValueError, match="mmlu"):
        check_benchmark_id_conflicts(manual, epoch)


def _bench(**overrides):
    base = dict(
        benchmark_id="x",
        name="X",
        domain="math",
        metric_type="accuracy",
    )
    return base | overrides


def test_raw_range_must_be_increasing():
    with pytest.raises(ValidationError, match="raw_max"):
        BenchmarkConfig(**_bench(raw_min=100, raw_max=0))


@pytest.mark.parametrize("floor", [-0.1, 1.0])
def test_guessing_floor_bounds(floor):
    with pytest.raises(ValidationError):
        BenchmarkConfig(**_bench(guessing_floor=floor))


def test_unknown_domain_rejected():
    with pytest.raises(ValidationError):
        BenchmarkConfig(**_bench(domain="poetry"))


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        BenchmarkConfig(**_bench(wieght=2.0))


def test_duplicate_benchmark_ids_rejected():
    with pytest.raises(ValidationError, match="Toistuvat"):
        BenchmarksFile(benchmarks=[_bench(), _bench()])


def test_anchors_need_two_distinct_points():
    with pytest.raises(ValidationError, match="vähintään 2"):
        AnchorsFile(anchors=[{"model_id": "a", "theta": 0.0}])
    with pytest.raises(ValidationError, match="eri θ"):
        AnchorsFile(anchors=[{"model_id": "a", "theta": 1.0}, {"model_id": "b", "theta": 1.0}])
    with pytest.raises(ValidationError, match="useammin"):
        AnchorsFile(anchors=[{"model_id": "a", "theta": 0.0}, {"model_id": "a", "theta": 1.0}])
