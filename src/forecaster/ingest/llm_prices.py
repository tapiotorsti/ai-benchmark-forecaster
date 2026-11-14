"""Hintahistoria: simonw/llm-prices (https://www.llm-prices.com/historical-v1.json).

LISENSSI PUUTTUU (repossa ei LICENSE-tiedostoa) → data vain omaan käyttöön; julkaisu vaatii
luvan (TOTEUTUSSUUNNITELMA, riski 3). Raakadata tallennetaan data/raw/llm_prices/.

Muoto: prices[] = {id, vendor, name, input, output (USD / 1M tokenia), from_date, to_date}.
from_date = null → hinta voimassa julkaisusta alkaen → valid_from = mallin release_date.

Täydennys (päätös 2026-11-13): mallit, joilla ei ole llm-prices-hintaa, saavat OpenRouterin
varhaisimman havaitun hinnan julkaisupäivästä alkaen lähteellä `openrouter_backfill`
(oletus: hinta ei ole muuttunut). Täydennys rakennetaan aina uudelleen.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import httpx

from forecaster.config import Paths, load_manual_prices
from forecaster.db.models import AliasRow, PriceRow
from forecaster.db.repository import Repository
from forecaster.ingest.base import IngestReport, download, manual_aliases, run_ingest, slugify

SOURCE = "llm_prices"
MANUAL_SOURCE = "manual"
URL = "https://www.llm-prices.com/historical-v1.json"
_DATE_SUFFIX = re.compile(r"-(\d{8}|\d{4}-\d{2}-\d{2}|\d{4})$")
# Kontekstitason lisähinnat (esim. "gpt-5.4-272k", "claude-sonnet-4-200k") → ohitetaan,
# perushinta on omalla rivillään.
_CONTEXT_TIER = re.compile(r"-\d+k$")
_RELEASE_TAG = re.compile(r"-(preview|latest|exp)(-\d{2}-\d{2,4}|-\d{2}-\d{4})?$")
_FULL_DATE = re.compile(r"-(\d{4})-?(\d{2})-?(\d{2})$")
_YYMM = re.compile(r"-(\d{2})(\d{2})$")
_LATEST = re.compile(r"-latest$")
MAX_DATE_GAP_DAYS = 180
_MONTH_YEAR = re.compile(
    r"-(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|"
    r"june|july|august|september|october|november|december)-\d{4}$"
)


def dated_versions(base: str, known: set[str]) -> list[str]:
    """Epochin päivätyt versiot päivämäärättömälle tunnisteelle: 'gpt-4o' → gpt-4o-may-2024, …
    (ei gpt-4o-mini: vaatii, että loppuosa on pelkkä kuukausi-vuosi)."""
    return [k for k in known if k.startswith(base + "-") and _MONTH_YEAR.fullmatch(k[len(base) :])]


def fetch(paths: Paths, as_of: date, client: httpx.Client | None = None) -> Path:
    dest = paths.raw_dir / SOURCE / f"historical-v1_{as_of.isoformat()}.json"
    if not dest.is_file():
        download(URL, dest, client=client)
    return dest


def candidates(row: dict) -> list[str]:
    """Mahdolliset model_id:t: slug(id) ja slug(nimi), myös ilman päivämäärä-/preview-päätettä."""
    out = []
    for base in (slugify(row.get("id") or ""), slugify(row.get("name") or "")):
        if base:
            stripped = _RELEASE_TAG.sub("", base)
            out += [base, _DATE_SUFFIX.sub("", base), stripped, _DATE_SUFFIX.sub("", stripped)]
    return list(dict.fromkeys(out))


def explicit_date(slug: str) -> date | None:
    """Tunnisteen päiväyspääte: -YYYYMMDD / -YYYY-MM-DD → päivä, -YYMM (vuodet 23–35, esim.
    mistral-medium-2505) → kuun 1. päivä. -MMDD (gpt-4-0613) ei kerro vuotta → None."""
    m = _FULL_DATE.search(slug)
    if m:
        try:
            d = date(*map(int, m.groups()))
            if 2020 <= d.year <= 2035:
                return d
        except ValueError:
            pass
    m = _YYMM.search(slug)
    if m:
        yy, mm = map(int, m.groups())
        if 23 <= yy <= 35 and 1 <= mm <= 12:
            return date(2000 + yy, mm, 1)
    return None


def resolve(row: dict, release: dict[str, date]) -> str | None:
    """Tunnisteen ratkaisu Epochin model_id:ksi (A19, tarkennettu K5-katselmoinnissa):

    1) Päiväyspääte (explicit_date) → se ehdokasversio, jonka julkaisupäivä on lähimpänä
       päiväystä (enint. MAX_DATE_GAP_DAYS); muuten tuntematon. (Ei: mistral-medium-2505 →
       vuoden 2023 mistral-medium.)
    2) -latest ilman from_datea: hinta on NYKYINEN → vain uusin päivätty Epoch-versio
       kelpaa; päivämäärätön vanha malli saisi nykyhinnan julkaisupäivästään alkaen
       (mistral-large-latest → 2024-02 hinnalla 3 $ vs. todellinen 12 $). Muuten tuntematon
       → julkaisuhinta manual_prices.yaml:iin.
    3) Muuten tarkka/riisuttu vastine, sitten päivämäärätön → UUSIN päivätty versio.
    """
    cands = candidates(row)
    known = set(release)
    exact = [c for c in cands if c in release]
    dated = [v for c in cands for v in dated_versions(c, known)]
    rid = slugify(row.get("id") or "")

    d = explicit_date(rid)
    if d is not None:
        pool = set(exact) | set(dated)
        if not pool:
            return None
        best = min(pool, key=lambda v: (abs((release[v] - d).days), v))
        return best if abs((release[best] - d).days) <= MAX_DATE_GAP_DAYS else None

    if _LATEST.search(rid) and not row.get("from_date"):
        return max(dated, key=lambda v: release[v]) if dated else None

    if exact:
        return exact[0]
    for c in cands:
        versions = dated_versions(c, known)
        if versions:
            return max(versions, key=lambda v: release[v])
    return None


def ingest(repo: Repository, paths: Paths, payload_path: Path) -> IngestReport:
    def work(run_id: str, report: IngestReport) -> None:
        rows = json.loads(payload_path.read_text(encoding="utf-8")).get("prices", [])
        report.rows_fetched = len(rows)
        release = dict(repo._run("SELECT model_id, release_date FROM models"))
        if not release:
            raise ValueError(
                "Tietokannassa ei ole malleja – aja ensin 'forecaster ingest epoch_ai'"
            )

        manual = {a.alias: a.model_id for a in manual_aliases(paths, SOURCE)}
        aliases: dict[str, AliasRow] = {}
        prices: dict[tuple, PriceRow] = {}
        unknown: set[str] = set()
        for r in rows:
            rid = r.get("id") or ""
            if _CONTEXT_TIER.search(rid):
                report.rows_skipped += 1  # kontekstitason lisähinta
                continue
            model_id = manual.get(rid) or resolve(r, release)
            if model_id is None:
                unknown.add(rid)
                continue
            valid_from = (
                date.fromisoformat(r["from_date"]) if r.get("from_date") else release[model_id]
            )
            try:
                row = PriceRow(model_id=model_id, valid_from=valid_from, source_name=SOURCE,
                               input_cost_per_1m=float(r["input"]),
                               output_cost_per_1m=float(r["output"]))  # fmt: skip
            except (TypeError, ValueError, KeyError):
                report.rows_skipped += 1
                continue
            key = (model_id, valid_from)
            # Usea llm-prices-id samalle mallille samana päivänä → halvin (vrt. OpenRouter)
            if key not in prices or row.blended_cost_per_1m < prices[key].blended_cost_per_1m:
                prices[key] = row
            aliases[rid] = AliasRow(source_name=SOURCE, alias=rid, model_id=model_id)

        manual_rows, bad_manual = [], []
        for mp in load_manual_prices(paths).prices:
            if mp.model_id not in release:
                bad_manual.append(mp.model_id)
                continue
            manual_rows.append(
                PriceRow(
                    model_id=mp.model_id,
                    valid_from=mp.valid_from or release[mp.model_id],
                    source_name=MANUAL_SOURCE,
                    input_cost_per_1m=mp.input,
                    output_cost_per_1m=mp.output,
                )  # fmt: skip
            )

        with repo.transaction():
            repo.upsert_aliases(aliases.values())
            repo._run("DELETE FROM model_prices WHERE source_name = ?", [MANUAL_SOURCE])
            report.rows_upserted = repo.upsert_prices(prices.values())
            report.rows_upserted += repo.upsert_prices(manual_rows)
            n_backfill = repo.rebuild_price_backfill()
        if bad_manual:
            report.notes.append(f"manual_prices.yaml: tuntemattomat model_id:t {bad_manual}")
        report.unknown_aliases = sorted(unknown)
        report.notes += [
            f"{len({k[0] for k in prices})} mallille hintahistoria llm-prices-datasta, "
            f"{len(manual_rows)} käsin syötettyä julkaisuhintaa",
            f"{n_backfill} mallille täydennys: OpenRouter-hinta julkaisupäivästä (oletus)",
            "Lisenssi puuttuu: vain omaan käyttöön",
        ]

    return run_ingest(repo, SOURCE, work)
