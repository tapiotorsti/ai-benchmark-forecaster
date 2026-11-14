"""Ingest-adapterien yhteinen runko: raportti, ajon lokitus, lataus, tunnisteet."""

from __future__ import annotations

import re
import time
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from forecaster.config import Paths, load_model_aliases
from forecaster.db.models import AliasRow
from forecaster.db.repository import Repository

USER_AGENT = "ai-benchmark-forecaster/0.1"


@dataclass
class IngestReport:
    source_name: str
    run_id: str = ""
    rows_fetched: int = 0
    rows_upserted: int = 0
    rows_quarantined: int = 0
    rows_skipped: int = 0
    new_models: int = 0
    unknown_aliases: list[str] = field(default_factory=list)
    unmapped_benchmarks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self, max_items: int = 20) -> str:
        lines = [
            f"Lähde: {self.source_name} (ajo {self.run_id})",
            f"  rivejä haettu:        {self.rows_fetched}",
            f"  rivejä tallennettu:   {self.rows_upserted}",
            f"  karanteeniin:         {self.rows_quarantined}",
            f"  ohitettu:             {self.rows_skipped}",
            f"  uusia malleja:        {self.new_models}",
        ]
        for title, items in (
            ("Tuntemattomat nimet (lisää config/model_aliases.yaml:iin)", self.unknown_aliases),
            (
                "Kuvaamattomat benchmarkit (lisää epoch_benchmarks.yaml:iin)",
                self.unmapped_benchmarks,
            ),
        ):
            if items:
                lines.append(f"  {title}: {len(items)}")
                lines += [f"    - {x}" for x in items[:max_items]]
                if len(items) > max_items:
                    lines.append(f"    … ja {len(items) - max_items} muuta")
        lines += [f"  Huom: {n}" for n in self.notes]
        return "\n".join(lines)


def slugify(text: str) -> str:
    """'Claude 3.5 Sonnet (October 2024)' → 'claude-3-5-sonnet-october-2024'."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def new_run_id(source_name: str) -> str:
    return f"ingest_{source_name}_{datetime.now():%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:6]}"


def run_ingest(
    repo: Repository, source_name: str, work: Callable[[str, IngestReport], None]
) -> IngestReport:
    """Kirjaa ajon ingest_runs-tauluun ja suorittaa työn. Virhe kirjataan ja nostetaan."""
    report = IngestReport(source_name=source_name, run_id=new_run_id(source_name))
    repo.start_ingest_run(report.run_id, source_name)
    try:
        work(report.run_id, report)
    except Exception as exc:
        repo.finish_ingest_run(
            report.run_id,
            status="failed",
            rows_fetched=report.rows_fetched,
            rows_upserted=report.rows_upserted,
            rows_quarantined=report.rows_quarantined,
            message=f"{type(exc).__name__}: {exc}"[:2000],
        )
        raise
    repo.save_unknown_names(report.run_id, source_name, "alias", report.unknown_aliases)
    repo.save_unknown_names(report.run_id, source_name, "benchmark", report.unmapped_benchmarks)
    repo.finish_ingest_run(
        report.run_id,
        status="ok",
        rows_fetched=report.rows_fetched,
        rows_upserted=report.rows_upserted,
        rows_quarantined=report.rows_quarantined,
        message=f"{len(report.unknown_aliases)} tuntematonta nimeä, "
        f"{len(report.unmapped_benchmarks)} kuvaamatonta benchmarkia",
    )
    return report


def download(url: str, dest: Path, *, client: httpx.Client | None = None, retries: int = 3) -> Path:
    """Lataa tiedoston uudelleenyrityksin. Kirjoittaa ensin .part-tiedostoon."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    own = client is None
    client = client or httpx.Client(
        timeout=60.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    try:
        for attempt in range(1, retries + 1):
            try:
                resp = client.get(url)
                resp.raise_for_status()
                tmp = dest.with_suffix(dest.suffix + ".part")
                tmp.write_bytes(resp.content)
                tmp.replace(dest)
                return dest
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                retryable = isinstance(exc, httpx.TransportError) or (
                    exc.response.status_code >= 500 or exc.response.status_code == 429
                )
                if attempt == retries or not retryable:
                    raise
                time.sleep(2**attempt)
    finally:
        if own:
            client.close()
    raise AssertionError("unreachable")


def manual_aliases(paths: Paths, source_name: str) -> list[AliasRow]:
    return [
        AliasRow(source_name=a.source_name, alias=a.alias, model_id=a.model_id)
        for a in load_model_aliases(paths).aliases
        if a.source_name == source_name
    ]
