"""K7: automaattinen päivitys `forecaster update` = haku → ingest → IRT → ennusteet → tarkistukset.

Ajetaan omalla koneella ajastettuna (scripts/schedule_update.ps1), ei GitHub Actionsissa:
tietokanta ja dashboard ovat paikallisia, eikä llm-prices-dataa (ei lisenssiä) saa tallentaa
julkiseen repoon tai sen artefakteihin (A23).

Periaatteet
- Jokainen vaihe ajetaan erikseen; virhe kirjataan hälytykseksi ja putki jatkaa niin pitkälle
  kuin järkevää (esim. haku kaatuu → sovitus vanhalla datalla, mutta ajo = failed).
- Verkkohaut tehdään ennen kuin kanta avataan, ja kanta avataan vaihe kerrallaan: DuckDB:n
  tiedostolukko on prosessien välillä poissulkeva, joten dashboard ei pysty lukemaan, kun
  päivitys kirjoittaa. Yhteys odottaa (wait_seconds) dashboardin lyhyiden lukujen ohi.
- Tulos tallennetaan tauluun pipeline_runs ja lokiin data/logs/; dashboard näyttää hälytykset.

Hälytystasot: critical (ajo = failed, CLI-koodi 1), warning (ajo = warning), info.
"""

from __future__ import annotations

import time
import warnings
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from forecaster.config import Paths, load_anchors, load_benchmarks, load_forecast
from forecaster.db.repository import Repository
from forecaster.ingest import epoch_ai, llm_prices, openrouter
from forecaster.ingest.base import IngestReport

Level = Literal["critical", "warning", "info"]
STALE_AFTER_DAYS = 10  # viikoittainen ajo + kolmen päivän pelivara
RUNNING_TIMEOUT_HOURS = 3  # tätä vanhempi "running"-ajo on kaatunut kesken


@dataclass(frozen=True)
class Alert:
    level: Level
    code: str
    message: str


@dataclass(frozen=True)
class Source:
    """Datalähde: verkkohaku (ilman kantaa) ja tuonti kantaan."""

    name: str
    fetch: Callable[[Paths, date], Path]
    ingest: Callable[[Repository, Paths, Path, date], IngestReport]


# Järjestys merkitsee: llm_prices rakentaa OpenRouter-täydennyksen, joten se tulee viimeisenä.
DEFAULT_SOURCES: tuple[Source, ...] = (
    Source("epoch_ai", epoch_ai.fetch, lambda r, p, f, d: epoch_ai.ingest(r, p, f)),
    Source("openrouter", openrouter.fetch, lambda r, p, f, d: openrouter.ingest(r, p, f, d)),
    Source("llm_prices", llm_prices.fetch, lambda r, p, f, d: llm_prices.ingest(r, p, f)),
)


@dataclass
class UpdateResult:
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"
    steps: list[dict] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Päivitys {self.run_id}: {self.status.upper()}"]
        for s in self.steps:
            mark = "ok " if s["ok"] else "VIRHE"
            lines.append(f"  [{mark}] {s['name']:<22} {s['seconds']:6.1f} s  {s['message']}")
        if self.alerts:
            lines.append("Hälytykset:")
            lines += [f"  {a.level.upper():<8} {a.code}: {a.message}" for a in self.alerts]
        return "\n".join(lines)


def _status(alerts: Sequence[Alert]) -> str:
    levels = {a.level for a in alerts}
    return "failed" if "critical" in levels else "warning" if "warning" in levels else "ok"


def run_update(
    paths: Paths,
    *,
    today: date | None = None,
    sources: Sequence[Source] = DEFAULT_SOURCES,
    domains: bool = True,
    n_boot: int | None = None,
    write_report: bool = True,
    wait_seconds: float = 120.0,
) -> UpdateResult:
    from forecaster.models.forecast_run import run_forecasts
    from forecaster.models.forecast_run import write_report as write_forecast_report
    from forecaster.models.irt_engine import IRTConfig
    from forecaster.models.pipeline import run_fit

    today = today or date.today()
    res = UpdateResult(run_id=f"update_{datetime.now():%Y%m%dT%H%M%S}", started_at=datetime.now())

    def db() -> Repository:
        return Repository.connect(paths.db_path, wait_seconds=wait_seconds)

    def step(name: str, fn: Callable[[], str | None]) -> bool:
        t0 = time.perf_counter()
        try:
            msg = fn() or ""
            ok = True
        except Exception as exc:  # noqa: BLE001 – jokainen vaihe kirjataan, putki jatkuu
            msg, ok = f"{type(exc).__name__}: {exc}"[:500], False
        res.steps.append({"name": name, "ok": ok, "seconds": round(time.perf_counter() - t0, 1),
                          "message": msg})  # fmt: skip
        return ok

    def alert(level: Level, code: str, message: str) -> None:
        res.alerts.append(Alert(level, code, message))

    def last_msg() -> str:
        return res.steps[-1]["message"]

    with db() as repo:
        repo.init_db()
        repo.upsert_benchmarks(load_benchmarks(paths).benchmarks)
        before = repo.unknown_name_set()
        repo.save_pipeline_run(res.run_id, res.started_at, None, "running", [], [])

    # 1. Verkkohaut (kanta ei auki)
    payloads: dict[str, Path] = {}
    for s in sources:

        def fetch(s: Source = s) -> str:
            payloads[s.name] = s.fetch(paths, today)
            return payloads[s.name].name

        if not step(f"haku:{s.name}", fetch):
            alert("critical", "fetch_failed", f"{s.name}: {last_msg()}")

    # 2. Tuonti
    with db() as repo:
        for s in sources:
            if s.name not in payloads:
                continue

            def ingest(s: Source = s) -> str:
                r = s.ingest(repo, paths, payloads[s.name], today)
                return f"{r.rows_upserted} riviä, {len(r.unknown_aliases)} tuntematonta nimeä"

            if not step(f"tuonti:{s.name}", ingest):
                alert("critical", "ingest_failed", f"{s.name}: {last_msg()}")
        new_names = sorted(repo.unknown_name_set() - before)
    if new_names:
        shown = ", ".join(f"{n} ({src})" for src, _, n in new_names[:10])
        more = f" … ja {len(new_names) - 10} muuta" if len(new_names) > 10 else ""
        alert("warning", "new_unknown_names",
              f"{len(new_names)} uutta tunnistamatonta nimeä: {shown}{more}. Lisää kohdistus "
              "config/model_aliases.yaml:iin (ks. Datan laatu -sivu).")  # fmt: skip

    # 3. IRT: overall ja osa-alueet
    anchors = load_anchors(paths)
    cfg = IRTConfig(anchors={a.model_id: a.theta for a in anchors.anchors},
                    anchor_mode=anchors.mode)  # fmt: skip

    def fit(domain: str) -> str:
        with db() as repo, warnings.catch_warnings():
            warnings.simplefilter("ignore")  # tunnetut varoitukset näkyvät fit --report:ssa
            summary = run_fit(repo, cfg, domain)
            converged = repo.latest_irt_converged(domain)
        r = summary.result
        if not converged:
            raise RuntimeError(f"IRT ei konvergoinut (ajo {summary.run_id})")
        return f"{len(r.model_ids)} mallia, {len(r.benchmark_ids)} benchmarkia"

    overall_ok = step("irt:overall", lambda: fit("overall"))
    if not overall_ok:
        alert("critical", "irt_failed", f"overall: {last_msg()}")
    if overall_ok and domains:
        with db() as repo:
            domain_list = [d for d in repo.list_domains() if d != "overall"]
        for d in domain_list:
            if not step(f"irt:{d}", lambda d=d: fit(d)):
                alert("warning", "domain_fit_failed", f"{d}: {last_msg()}")

    # 4. Ennusteet
    if overall_ok:
        ff = load_forecast(paths)
        if n_boot is not None:
            ff = ff.model_copy(update={"n_boot": n_boot})

        def forecast() -> str:
            with db() as repo:
                fr = run_forecasts(repo, ff, today)
            if write_report:
                write_forecast_report(fr, paths)
            c = fr.capability
            return f"rintama {c['slope_per_year']:.2f} θ/v ({c['model']})"

        if not step("ennusteet", forecast):
            alert("critical", "forecast_failed", last_msg())

    res.status = _status(res.alerts)
    res.finished_at = datetime.now()
    with db() as repo:
        repo.save_pipeline_run(res.run_id, res.started_at, res.finished_at, res.status, res.steps,
                               [asdict(a) for a in res.alerts])  # fmt: skip
    log_dir = paths.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{res.run_id}.log").write_text(res.summary() + "\n", encoding="utf-8")
    return res


def health_alerts(
    runs, now: datetime | None = None, stale_days: int = STALE_AFTER_DAYS
) -> list[Alert]:
    """Dashboardin tilannekuva: uusimman päivitysajon hälytykset + datan ikä.

    runs: Repository.get_pipeline_runs() (uusin ensin).
    """
    import json

    now = now or datetime.now()
    if runs.is_empty():
        return [Alert("info", "no_updates", "Automaattista päivitystä ei ole vielä ajettu "
                      "(forecaster update).")]  # fmt: skip
    latest = runs.row(0, named=True)
    out: list[Alert] = []
    if latest["status"] == "running" and latest["finished_at"] is None:
        hours = (now - latest["started_at"]).total_seconds() / 3600
        if hours > RUNNING_TIMEOUT_HOURS:  # prosessi kuoli kesken (kone sammui tms.)
            out.append(Alert("critical", "update_crashed",
                             f"Päivitys {latest['run_id']} jäi kesken {hours:.0f} h sitten. "
                             "Katso data/logs/ ja aja forecaster update."))  # fmt: skip
        else:
            out.append(Alert("info", "update_running", f"Päivitys käynnissä ({latest['run_id']})."))
        latest = runs.row(1, named=True) if runs.height > 1 else None
    if latest is not None:
        out += [Alert(**a) for a in json.loads(latest["alerts_json"] or "[]")]
        finished = latest["finished_at"] or latest["started_at"]
        age = (now - finished).days
        if age > stale_days:
            out.append(Alert("warning", "stale_data",
                             f"Data on päivitetty viimeksi {age} päivää sitten "
                             f"({finished:%Y-%m-%d}). Onko ajastus päällä?"))  # fmt: skip
    return out
