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


@app.callback()
def _setup_output() -> None:
    """Uudelleenohjattu tuloste (esim. ajastettu ajo → tiedosto) UTF-8:ksi: Windowsin
    oletusmerkistö cp1252 ei sisällä θ-merkkiä, ja tulostus kaatui UnicodeEncodeErroriin
    vasta onnistuneen päivityksen jälkeen. Konsoli (tty) kirjoittaa Unicodea jo valmiiksi."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # esim. testien CliRunner-virrat


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
    full = c["slope_per_year_full_period"]
    typer.echo(f"Rintama: {c['slope_per_year']:.2f} θ/vuosi (mediaani; koko jakson sovitus "
               f"{c['model']} {full:.2f}; {c['n_frontier']} pistettä)")  # fmt: skip
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


@app.command("backtest-vintage")
def backtest_vintage(
    fetch: Annotated[
        bool, typer.Option("--fetch/--no-fetch", help="Lataa puuttuvat arkistotilannekuvat")
    ] = True,
    origins: Annotated[
        str,
        typer.Option(
            help="'auto' = lähtöpäivinä tilannekuvien päivät (data ei vanhene eikä toistu), "
            "tai kk taaksepäin pilkuin (esim. 3,6,9)"
        ),
    ] = "auto",
    n_boot: Annotated[int | None, typer.Option(help="Ohita forecast.yaml:n n_boot")] = None,
    root: RootOption = None,
) -> None:
    """Ajankohtaan sidottu backtest (6.2): IRT jokaiselle lähtöpäivälle vain silloin
    julkaistuilla tuloksilla (Epochin arkistoidut tilannekuvat) → docs/backtest_ajankohta.md."""
    import warnings
    from datetime import date

    import polars as pl

    from forecaster.config import load_epoch_benchmarks, load_forecast
    from forecaster.db.repository import Repository
    from forecaster.ingest import epoch_ai, epoch_archive
    from forecaster.models.forecast_run import forecast_config
    from forecaster.models.forecaster import METHOD_VERSION, add_months
    from forecaster.models.irt_engine import IRTConfig
    from forecaster.models.vintage import snapshot_cutoffs, vintage_backtest, write_report

    paths = get_paths(root)
    ff = load_forecast(paths)
    if n_boot is not None:
        ff = ff.model_copy(update={"n_boot": n_boot})
    # Verkko ennen kannan avaamista (K7)
    snaps = (epoch_archive.fetch_snapshots(paths) if fetch
             else epoch_archive.local_snapshots(paths))  # fmt: skip
    if not snaps:
        typer.secho("Ei arkistotilannekuvia (aja ilman --no-fetch)", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    as_of = date.today()
    try:
        cutoffs = (
            snapshot_cutoffs(snaps, as_of)
            if origins == "auto"
            else [add_months(as_of, -int(x)) for x in origins.split(",")]
        )
    except ValueError as exc:
        typer.secho("--origins: esim. 3,6,9 tai auto", err=True)
        raise typer.Exit(code=1) from exc
    latest = max((d for d in (paths.raw_dir / epoch_ai.SOURCE).iterdir() if d.is_dir()),
                 key=lambda d: d.name)  # fmt: skip
    meta = pl.read_csv(latest / "extracted" / "benchmark_metadata.csv", infer_schema_length=0)
    _, specs, _, _ = epoch_ai.build_benchmarks(meta, load_epoch_benchmarks(paths))
    anchors = load_anchors(paths)
    irt_cfg = IRTConfig(anchors={a.model_id: a.theta for a in anchors.anchors},
                        anchor_mode=anchors.mode)  # fmt: skip
    with Repository.connect(paths.db_path, read_only=True) as repo:
        irt_run = repo.latest_run_id("overall")
        if irt_run is None:
            typer.secho("IRT-ajoa ei ole – aja ensin 'forecaster fit'", err=True)
            raise typer.Exit(code=1)
        current = repo.get_capability_with_dates(irt_run).filter(pl.col("release_date") <= as_of)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = vintage_backtest(repo, snaps, specs, irt_cfg, forecast_config(ff), as_of,
                                   current, cutoffs=cutoffs,
                                   holdout_months=ff.backtest_holdout_months,
                                   max_theta_se=ff.max_theta_se)  # fmt: skip
    out = write_report(res, paths.root / "docs" / "backtest_ajankohta.md", irt_run=irt_run,
                       method_version=METHOD_VERSION)  # fmt: skip
    for name in ("vintage", "lookahead", "vintage_mature", "lookahead_mature"):
        a = res[name]
        if a:
            typer.echo(
                f"{name}: sisällä {a['mean_origin_share']:.2f} (lähtöpäivät) / "
                f"{a['pooled_share']:.2f} (pisteet), virhe {a['mean_error']:+.2f}, "
                f"itseisvirhe {a['mean_abs_error']:.2f}, {a['n_points']} pistettä / "
                f"{a['n_unique_models']} mallia"
            )
    for o in res["origins"]:
        if o.get("status") != "ok":
            typer.secho(f"{o['cutoff']}: {o['status']} {o.get('note', '')}",
                        fg=typer.colors.YELLOW, err=True)  # fmt: skip
    typer.echo(f"Raportti: {out}")


@app.command()
def aliases(
    source: Annotated[str, typer.Option(help="Hintalähde: openrouter | llm_prices | all")] = "all",
    all_models: Annotated[
        bool, typer.Option("--all-models", help="Myös mallit, joilla on jo hinta")
    ] = False,
    top: Annotated[int, typer.Option(help="Ehdokkaita mallia kohden")] = 3,
    min_score: Annotated[float, typer.Option(help="Pienin hyväksytty pistemäärä (0–1)")] = 0.4,
    root: RootOption = None,
) -> None:
    """Ehdota aliaksia hintalähteiden tunnistamattomille nimille (tarkista käsin).

    Oletuksena vain mallit, joilla on θ mutta ei hintaa. Hyväksytyt rivit lisätään
    tiedostoon config/model_aliases.yaml, ja ne tulevat käyttöön seuraavassa tuonnissa.
    """
    from forecaster.db.repository import Repository
    from forecaster.ingest.alias_candidates import (
        PRICE_SOURCES,
        candidates,
        load_source_names,
        yaml_line,
    )

    if source != "all" and source not in PRICE_SOURCES:
        typer.secho(f"--source: {' | '.join(PRICE_SOURCES)} | all", err=True)
        raise typer.Exit(code=1)
    paths = get_paths(root)
    with Repository.connect(paths.db_path, read_only=True) as repo:
        run_id = repo.latest_run_id("overall")
        if run_id is None:
            typer.secho("IRT-ajoa ei ole – aja ensin 'forecaster fit'", err=True)
            raise typer.Exit(code=1)
        models = repo.get_models_with_price_status(run_id)
        unknown = repo.get_unknown_names()
    if source != "all":
        unknown = unknown.filter(unknown["source_name"] == source)
    names = load_source_names(paths, unknown)
    cand = candidates(names, models, only_unpriced=not all_models, top=top, min_score=min_score)
    n_target = models.height if all_models else int((~models["has_price"]).sum())
    typer.echo(
        f"Tunnistamattomia nimiä {len(names)}, kohdemalleja {n_target}, "
        f"ehdokkaita {cand.height} ({cand['model_id'].n_unique() if cand.height else 0} mallille)"
    )
    for (model_id,), g in cand.group_by("model_id", maintain_order=True):
        m = g.row(0, named=True)
        typer.secho(
            f"\n{m['malli']} [{model_id}] – {m['valmistaja']}, julkaistu {m['julkaistu']}, "
            f"θ {m['theta']}",
            bold=True,
        )
        for r in g.iter_rows(named=True):
            gap = f"{r['päivä_ero']:+d} pv" if r["päivä_ero"] is not None else "pvm ?"
            note = f"  ⚠ {r['huom']}" if r["huom"] else ""
            typer.echo(
                f"  {r['pisteet']:.2f}  {r['source_name']:<10} {r['alias']}  "
                f'"{r["nimi_lähteessä"]}" ({r["vendor"]}, {gap}){note}'
            )
            typer.echo(f"        {yaml_line(r)}")
    if cand.height:
        typer.echo(
            "\nTarkista valmistaja, versio, koko ja päivä ennen hyväksyntää. Hyväksytyt "
            "YAML-rivit → config/model_aliases.yaml, sitten 'forecaster update'."
        )


@app.command()
def update(
    no_domains: Annotated[
        bool, typer.Option("--no-domains", help="Ohita osa-aluekohtaiset IRT-ajot")
    ] = False,
    n_boot: Annotated[int | None, typer.Option(help="Ohita forecast.yaml:n n_boot")] = None,
    root: RootOption = None,
) -> None:
    """Koko päivitys: haku → tuonti → IRT (overall + osa-alueet) → ennusteet → tarkistukset.

    Tarkoitettu ajastettavaksi (scripts/schedule_update.ps1). Loki: data/logs/.
    Paluukoodi 1, jos jokin kriittinen vaihe epäonnistui.
    """
    from forecaster.update import run_update

    paths = get_paths(root)
    res = run_update(paths, domains=not no_domains, n_boot=n_boot)
    color = {"ok": "green", "warning": "yellow"}.get(res.status, "red")
    typer.secho(res.summary(), fg=color)
    if res.status == "failed":
        raise typer.Exit(code=1)


@app.command()
def dashboard(
    port: Annotated[int, typer.Option(help="Portti")] = 8501,
    address: Annotated[
        str,
        typer.Option(
            help="Kuunneltava osoite. Oletus 127.0.0.1 = vain tämä kone (etäkäyttö Tailscale "
            "Servella, ks. README). 0.0.0.0 näkyy koko lähiverkkoon."
        ),
    ] = "127.0.0.1",
    root: RootOption = None,
) -> None:
    """Käynnistä Streamlit-dashboard (vaatii ui-riippuvuudet). Pysäytys: Ctrl+C."""
    import os

    paths = get_paths(root)
    script = paths.root / "app" / "dashboard.py"
    if not paths.db_path.is_file():
        typer.secho("Tietokantaa ei ole – aja ensin 'forecaster init-db', 'ingest all' ja 'fit'.",
                    fg=typer.colors.YELLOW, err=True)  # fmt: skip
    # POLARS_MAX_THREADS=1: ks. app/dashboard.py (Polars + OpenMP -kaatuminen säikeissä)
    env = os.environ | {"FORECASTER_ROOT": str(paths.root), "POLARS_MAX_THREADS": "1"}
    raise typer.Exit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(script),
                f"--server.port={port}",
                f"--server.address={address}",
                "--browser.gatherUsageStats=false",
            ],
            env=env,
        )  # fmt: skip
    )


if __name__ == "__main__":
    app()
