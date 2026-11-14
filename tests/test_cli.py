from pathlib import Path

from typer.testing import CliRunner

from forecaster import __version__
from forecaster.cli import app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["check-config", "init-db", "ingest", "fit", "forecast", "dashboard"]:
        assert cmd in result.output


def test_check_config_ok():
    result = runner.invoke(app, ["check-config", "--root", str(PROJECT_ROOT)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_check_config_reports_invalid_yaml(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "benchmarks.yaml").write_text(
        "benchmarks:\n  - benchmark_id: x\n    name: X\n    domain: poetry\n"
        "    metric_type: accuracy\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "anchors.yaml").write_text(
        "anchors:\n  - {model_id: a, theta: 0}\n  - {model_id: b, theta: 1}\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["check-config", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "virheellinen" in result.output


def test_init_db_creates_database_and_syncs_benchmarks(tmp_path):
    import shutil

    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    for _ in range(2):  # idempotentti
        result = runner.invoke(app, ["init-db", "--root", str(tmp_path)])
        assert result.exit_code == 0, result.output
    assert (tmp_path / "data" / "benchmark.duckdb").is_file()
    assert "benchmarks" in result.output


def test_fit_without_data_fails_cleanly(tmp_path):
    import shutil

    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    result = runner.invoke(app, ["fit", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "Ei havaintoja" in result.output


def test_unimplemented_helper_exits_with_code_2():
    import pytest
    import typer

    from forecaster.cli import _not_implemented

    with pytest.raises(typer.Exit) as exc:
        _not_implemented("x", "K9")
    assert exc.value.exit_code == 2


def test_forecast_without_irt_run_fails_cleanly(tmp_path):
    import shutil

    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    result = runner.invoke(app, ["forecast", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "IRT-ajoa ei ole" in result.output
