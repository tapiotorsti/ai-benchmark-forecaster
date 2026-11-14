"""Komentorivikäyttöliittymä: `forecaster <komento>`.

Komennot, joita ei ole vielä toteutettu, palauttavat virhekoodin 2 ja kertovat,
missä kokonaisuudessa ne toteutetaan.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from forecaster import __version__
from forecaster.config import (
    check_benchmark_id_conflicts,
    get_paths,
    load_anchors,
    load_benchmarks,
    load_epoch_benchmarks,
    load_model_aliases,
)

app = typer.Typer(
    help="AI Benchmark Aggregator & Forecaster",
    no_args_is_help=True,
    add_completion=False,
)

RootOption = Annotated[
    Path | None,
    typer.Option("--root", help="Projektin juurihakemisto (oletus: haetaan automaattisesti)."),
]


def _not_implemented(command: str, milestone: str) -> None:
    typer.secho(
        f"'{command}' ei ole vielä toteutettu (kokonaisuus {milestone}).",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=2)


@app.command()
def version() -> None:
    """Tulosta versio."""
    typer.echo(__version__)


@app.command("check-config")
def check_config(root: RootOption = None) -> None:
    """Validoi config/benchmarks.yaml ja config/anchors.yaml."""
    paths = get_paths(root)
    try:
        benchmarks = load_benchmarks(paths)
        anchors = load_anchors(paths)
        epoch = load_epoch_benchmarks(paths)
        aliases = load_model_aliases(paths)
        check_benchmark_id_conflicts(benchmarks, epoch)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        typer.secho(f"Konfiguraatio virheellinen:\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    domains = sorted({b.domain for b in epoch.benchmarks.values()})
    typer.echo(f"Projektin juuri: {paths.root}")
    typer.echo(f"Omat benchmarkit:  {len(benchmarks.benchmarks)}")
    typer.echo(f"Epoch-benchmarkit: {len(epoch.benchmarks)} ({', '.join(domains)})")
    typer.echo(f"Käsin aliakset:    {len(aliases.aliases)}")
    typer.echo(
        f"Ankkurit:        {len(anchors.anchors)} (tila: {anchors.mode}) - "
        + ", ".join(f"{a.model_id}={a.theta:g}" for a in anchors.anchors)
    )
    typer.secho("OK", fg=typer.colors.GREEN)


@app.command("init-db")
def init_db(root: RootOption = None) -> None:
    """Alusta DuckDB-tietokanta ja synkronoi benchmarkit config/benchmarks.yaml:sta."""
    from forecaster.db.repository import Repository, SchemaVersionError

    paths = get_paths(root)
    try:
        benchmarks = load_benchmarks(paths)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        typer.secho(f"Konfiguraatio virheellinen:\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    with Repository.connect(paths.db_path) as repo:
        try:
            version = repo.init_db()
        except SchemaVersionError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        n = repo.upsert_benchmarks(benchmarks.benchmarks)
        counts = repo.table_counts()

    typer.echo(f"Tietokanta: {paths.db_path} (skeemaversio {version})")
    typer.echo(f"Benchmarkeja synkronoitu: {n}")
    for table, count in counts.items():
        typer.echo(f"  {table:<22} {count}")
    typer.secho("OK", fg=typer.colors.GREEN)


@app.command()
def ingest(
    source: Annotated[
        str, typer.Argument(help="Lähde: epoch_ai | openrouter | llm_prices | internal | all")
    ] = "all",
    from_path: Annotated[
        Path | None,
        typer.Option(
            "--from",
            help="Käytä paikallista tiedostoa verkon sijaan: epoch_ai = purettu kansio, "
            "openrouter/llm_prices = JSON, internal = CSV (pakollinen)",
        ),
    ] = None,
    root: RootOption = None,
) -> None:
    """Hae data lähteistä tietokantaan. 'all' = epoch_ai + openrouter + llm_prices.

    LiveBench-tulokset tulevat Epochin aineiston mukana (epoch_benchmarks.yaml).
    """
    from datetime import date

    from forecaster.db.repository import Repository
    from forecaster.ingest import epoch_ai, internal, llm_prices, openrouter

    valid = {"epoch_ai", "openrouter", "llm_prices", "internal", "all"}
    if source not in valid:
        typer.secho(f"Tuntematon lähde '{source}'. Vaihtoehdot: {sorted(valid)}", err=True)
        raise typer.Exit(code=1)
    if source == "internal" and from_path is None:
        typer.secho("internal vaatii --from <tiedosto.csv>", err=True)
        raise typer.Exit(code=1)
    if source == "all" and from_path is not None:
        typer.secho("--from ei toimi lähteen 'all' kanssa", err=True)
        raise typer.Exit(code=1)

    paths = get_paths(root)
    today = date.today()
    reports = []
    with Repository.connect(paths.db_path) as repo:
        repo.init_db()
        repo.upsert_benchmarks(load_benchmarks(paths).benchmarks)
        try:
            if source in ("epoch_ai", "all"):
                extracted = from_path or epoch_ai.fetch(paths, today)
                reports.append(epoch_ai.ingest(repo, paths, extracted))
            if source in ("openrouter", "all"):
                payload = from_path or openrouter.fetch(paths, today)
                reports.append(openrouter.ingest(repo, paths, payload, today))
            if source in ("llm_prices", "all"):
                hist = from_path or llm_prices.fetch(paths, today)
                reports.append(llm_prices.ingest(repo, paths, hist))
            if source == "internal":
                reports.append(internal.ingest(repo, paths, from_path))
        except Exception as exc:
            for r in reports:
                typer.echo(r.summary())
            typer.secho(f"Ingest epäonnistui: {type(exc).__name__}: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
    for r in reports:
        typer.echo(r.summary())
    typer.secho("OK", fg=typer.colors.GREEN)


@app.command()
def fit(
    domain: Annotated[str, typer.Option(help="'overall' tai yksittäinen osa-alue")] = "overall",
    no_anchors: Annotated[
        bool, typer.Option("--no-anchors", help="Sovita ilman anchors.yaml-kiinnitystä")
    ] = False,
    min_obs: Annotated[int, typer.Option(help="Mallin tulosten vähimmäismäärä")] = 3,
    no_outliers: Annotated[
        bool, typer.Option("--no-outliers", help="Ohita poikkeamien tunnistus")
    ] = False,
    report: Annotated[
        bool, typer.Option("--report/--no-report", help="Kirjoita docs/irt_diagnostiikka.md")
    ] = False,
    stability: Annotated[
        str, typer.Option(help="Vakaustesti raporttiin: none | key | all (hidas)")
    ] = "key",
    root: RootOption = None,
) -> None:
    """Sovita 2PL-IRT (karsinta → sovitus → poikkeamat → uudelleensovitus) ja tallenna θ.

    Domain-ajot linkitetään viimeisimmän overall-ajon asteikkoon.
    """
    import warnings

    from forecaster.db.repository import Repository
    from forecaster.models.diagnostics import write_report
    from forecaster.models.irt_engine import IRTConfig
    from forecaster.models.pipeline import PipelineConfig, run_fit

    if stability not in {"none", "key", "all"}:
        typer.secho("--stability: none | key | all", err=True)
        raise typer.Exit(code=1)
    paths = get_paths(root)
    anchors_cfg = load_anchors(paths)
    cfg = IRTConfig(
        anchors={} if no_anchors else {a.model_id: a.theta for a in anchors_cfg.anchors},
        anchor_mode=anchors_cfg.mode,
    )
    pcfg = PipelineConfig(min_model_obs=min_obs, detect_outliers=not no_outliers)
    with Repository.connect(paths.db_path) as repo:
        repo.init_db()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                summary = run_fit(repo, cfg, domain, pcfg)
            except ValueError as exc:
                typer.secho(f"Sovitus epäonnistui: {exc}", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1) from exc
        seen = list(dict.fromkeys(str(w.message) for w in caught))
        for w in seen:
            typer.secho(f"Varoitus: {w}", fg=typer.colors.YELLOW, err=True)
        run_id = summary.run_id
        typer.echo(
            f"Karsittu {len(summary.dropped_models)} mallia ja "
            f"{len(summary.dropped_benchmarks)} benchmarkia; "
            f"poikkeamia {summary.outliers.height} (sovituksesta poistettu "
            f"{int(summary.outliers['excluded'].sum()) if summary.outliers.height else 0})"
        )
        if report:
            out = write_report(repo, paths, summary, warnings_seen=seen, stability=stability)
            typer.echo(f"Raportti: {out}")
        info = repo._one(
            "SELECT n_models, n_benchmarks, n_observations, converged FROM irt_runs "
            "WHERE run_id = ?",
            [run_id],
        )
        top = repo._pl(
            "SELECT model_id, round(theta, 2) AS theta, round(theta_se, 2) AS se "
            "FROM capability_scores WHERE run_id = ? ORDER BY theta DESC LIMIT 10",
            [run_id],
        )
    n_models, n_bench, n_obs, converged = info
    typer.echo(f"Ajo: {run_id}")
    typer.echo(f"{n_models} mallia, {n_bench} benchmarkia, {n_obs} havaintoa")
    typer.secho(
        "Konvergoi" if converged else "EI KONVERGOINUT",
        fg=typer.colors.GREEN if converged else typer.colors.RED,
    )
    typer.echo("Top 10:")
    for model_id, theta, se in top.iter_rows():
        typer.echo(f"  {theta:>6} ± {se}  {model_id}")


@app.command()
def forecast(
    n_boot: Annotated[int | None, typer.Option(help="Ohita forecast.yaml:n n_boot")] = None,
    root: RootOption = None,
) -> None:
    """Laske kyvykkyys- ja kustannusennusteet + backtest → docs/ennusteet.md."""
    from forecaster.config import load_forecast
    from forecaster.db.repository import Repository
    from forecaster.models.forecast_run import run_forecasts, write_report

    paths = get_paths(root)
    ff = load_forecast(paths)
    if n_boot is not None:
        ff = ff.model_copy(update={"n_boot": n_boot})
    with Repository.connect(paths.db_path) as repo:
        repo.init_db()
        try:
            res = run_forecasts(repo, ff)
        except ValueError as exc:
            typer.secho(f"Ennuste epäonnistui: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
    out = write_report(res, paths)
    c = res.capability
    typer.echo(f"Rintama: {c['model']}, {c['slope_per_year']:.2f} θ/vuosi "
               f"({c['n_frontier']} pistettä)")  # fmt: skip
    for h in c["horizons"]:
        p = h["prediction_p10_p50_p90"]
        typer.echo(f"  +{h['months']:>2} kk: θ {p[1]:.2f} [{p[0]:.2f} – {p[2]:.2f}]")
    for label, fc in res.costs.items():
        hm = fc.get("halving_months")
        ok = fc.get("status") == "ok" and hm
        typer.echo(f"Hinta {label}: " + (f"puoliintuu {hm:.1f} kk" if ok else fc["status"]))
    for w in res.warnings:
        typer.secho(f"Varoitus: {w}", fg=typer.colors.YELLOW, err=True)
    typer.echo(f"Raportti: {out}")


@app.command()
def dashboard(root: RootOption = None) -> None:
    """Käynnistä Streamlit-dashboard (vaatii: pip install -e .[ui])."""
    paths = get_paths(root)
    script = paths.root / "app" / "dashboard.py"
    raise typer.Exit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(script)]))


if __name__ == "__main__":
    app()
