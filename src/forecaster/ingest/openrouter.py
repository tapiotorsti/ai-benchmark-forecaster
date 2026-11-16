"""OpenRouter-hintojen tuonti (A1).

Rajapinta: GET https://openrouter.ai/api/v1/models (julkinen, ei avainta).
Rajapinta antaa VAIN nykyhinnat. Hintahistoria kertyy tallentamalla jokainen ajo
omalla valid_from-päivällään, joten ajo kannattaa ajastaa päivittäiseksi (K7).

Mallien yhdistäminen Epochin malleihin:
1. käsin määritellyt aliakset (config/model_aliases.yaml) ja aiemmin tallennetut aliakset
2. automaattinen tarkka vastaavuus: slug(nimi ilman "Provider: "-etuliitettä) tai
   slug(id ilman "provider/"-etuliitettä) on olemassa oleva model_id
3. muut raportoidaan ehdotuksen kanssa (difflib), eikä niitä tallenneta
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from forecaster.config import Paths
from forecaster.db.models import AliasRow, PriceRow
from forecaster.db.repository import Repository
from forecaster.ingest.base import IngestReport, download, manual_aliases, run_ingest, slugify

SOURCE = "openrouter"
URL = "https://openrouter.ai/api/v1/models"


@dataclass(frozen=True)
class OpenRouterModel:
    id: str
    name: str
    canonical_slug: str
    input_per_1m: float
    output_per_1m: float

    def candidates(self) -> list[str]:
        name = self.name.split(": ", 1)[-1]
        return [slugify(name), slugify(self.id.split("/", 1)[-1]),
                slugify(self.canonical_slug.split("/", 1)[-1])]  # fmt: skip


def fetch(paths: Paths, as_of: date, client: httpx.Client | None = None) -> Path:
    dest = paths.raw_dir / SOURCE / f"models_{as_of.isoformat()}.json"
    if not dest.is_file():
        download(URL, dest, client=client)
    return dest


def parse(payload: dict) -> tuple[list[OpenRouterModel], int]:
    """Palauttaa (mallit, ohitettujen määrä). Ohitetaan muunnelmat (':free', ':batch'),
    reitittimet ('~'-alkuiset, negatiivinen hinta) ja ilmaiset."""
    out, skipped = [], 0
    for m in payload.get("data", []):
        mid = m.get("id", "")
        pricing = m.get("pricing") or {}
        try:
            prompt = float(pricing.get("prompt", "nan"))
            completion = float(pricing.get("completion", "nan"))
        except (TypeError, ValueError):
            skipped += 1
            continue
        if (
            ":" in mid
            or mid.startswith("~")
            or not (prompt >= 0 and completion >= 0)
            or (prompt == 0 and completion == 0)
        ):
            skipped += 1
            continue
        out.append(
            OpenRouterModel(
                id=mid,
                name=m.get("name") or mid,
                canonical_slug=m.get("canonical_slug") or mid,
                input_per_1m=prompt * 1e6,
                output_per_1m=completion * 1e6,
            )
        )
    return out, skipped


def ingest(repo: Repository, paths: Paths, payload_path: Path, as_of: date) -> IngestReport:
    def work(run_id: str, report: IngestReport) -> None:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        models, skipped = parse(payload)
        report.rows_fetched = len(payload.get("data", []))
        report.rows_skipped = skipped

        known_ids = repo.existing_ids("models", "model_id")
        if not known_ids:
            raise ValueError(
                "Tietokannassa ei ole malleja – aja ensin 'forecaster ingest epoch_ai'"
            )

        manual = manual_aliases(paths, SOURCE)
        found, _ = repo.resolve_aliases(SOURCE, [m.id for m in models])
        found |= {a.alias: a.model_id for a in manual}

        new_aliases: list[AliasRow] = list(manual)
        prices: dict[str, PriceRow] = {}
        unknown: list[str] = []
        for m in models:
            model_id = found.get(m.id)
            if model_id is None:
                model_id = next((c for c in m.candidates() if c in known_ids), None)
                if model_id is not None:
                    new_aliases.append(AliasRow(source_name=SOURCE, alias=m.id, model_id=model_id))
            if model_id is None:
                guess = difflib.get_close_matches(m.candidates()[0], known_ids, n=1, cutoff=0.75)
                unknown.append(m.id)
                if guess:
                    report.alias_suggestions[m.id] = guess[0]
                continue
            row = PriceRow(
                model_id=model_id,
                valid_from=as_of,
                source_name=SOURCE,
                input_cost_per_1m=m.input_per_1m,
                output_cost_per_1m=m.output_per_1m,
            )
            # Usea OpenRouter-id samalle mallille → halvin (eri tarjoajat/aliakset)
            if (
                model_id not in prices
                or row.blended_cost_per_1m < prices[model_id].blended_cost_per_1m
            ):
                prices[model_id] = row

        with repo.transaction():
            repo.upsert_aliases({(a.source_name, a.alias): a for a in new_aliases}.values())
            report.rows_upserted = repo.upsert_prices(prices.values())

        report.unknown_aliases = unknown
        report.notes = [
            f"{len(prices)} mallille hinta päivälle {as_of}",
            "OpenRouter antaa vain nykyhinnat – hintahistoria kertyy päivittäisistä ajoista",
        ]

    return run_ingest(repo, SOURCE, work)
