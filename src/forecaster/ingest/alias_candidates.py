"""Aliasten kuratointi: hintalähteiden tunnistamattomien nimien ehdokasvastineet.

Ingestin difflib-ehdotukset vertaavat pelkkää merkkijonoa, ja ne ovat usein vääriä
(esim. eri koko tai versio). Tämä moduuli rajaa haun ja tekee siitä tarkistettavan:

- Kohteet: ensisijaisesti mallit, joilla on θ mutta ei hintaa (vain niille alias
  tuo ennusteeseen uutta tietoa).
- Nimet: kunkin hintalähteen uusimman ajon tunnistamattomat nimet, ja niiden
  metatiedot (näyttönimi, valmistaja, päivämäärä) lähteen raakatiedostosta.
- Pisteytys: nimien sanat, versionumerot ja koot (esim. 70b). Ehdokas hylätään, jos
  versio tai koko on molemmissa ja eroaa, tai jos vain toisessa on mallin varianttia
  erottava sana (mini, flash, lite, pro, …).

Tulos on ehdotus. Jokainen rivi tarkistetaan käsin ennen kuin se lisätään tiedostoon
config/model_aliases.yaml.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

import polars as pl

from forecaster.config import Paths

PRICE_SOURCES = ("openrouter", "llm_prices")

# Sanat, jotka erottavat saman perheen eri malleja. Jos sana on vain toisessa nimessä,
# kyse on eri mallista (gemini-2.5-flash ≠ gemini-2.5-flash-lite).
VARIANT_WORDS = frozenset({
    "mini", "nano", "lite", "flash", "pro", "max", "plus", "turbo", "instant", "fast",
    "ultra", "small", "medium", "large", "coder", "code", "vision", "vl", "omni", "air",
    "haiku", "sonnet", "opus", "base", "distill", "spark", "heavy", "deep", "research",
    "thinking", "reasoning", "audio", "realtime", "search", "embedding", "guard",
})  # fmt: skip
# Sanat, jotka eivät kerro mallista mitään (toinen lähde jättää ne usein pois).
NOISE_WORDS = frozenset({
    "instruct", "chat", "it", "hf", "model", "the", "beta", "v", "a", "latest", "preview",
    "exp", "experimental", "free",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
})  # fmt: skip
ROLLING_MARKERS = ("latest", "preview", "exp")
MAX_DATE_GAP_DAYS = 180

_TOKEN = re.compile(r"\d+(?:\.\d+)*[bkmt]?|[a-z]+")
_SIZE = re.compile(r"^\d+(?:\.\d+)?[bmt]$")
_VERSION = re.compile(r"^\d+(?:\.\d+)*$")


@dataclass(frozen=True)
class Tokens:
    words: frozenset[str]
    versions: frozenset[str]
    sizes: frozenset[str]

    @property
    def all(self) -> frozenset[str]:
        return self.words | self.versions | self.sizes


def tokenize(*texts: str) -> Tokens:
    """Nimet sanoiksi, versioiksi ja kooiksi. Väliviiva numeroiden välissä on versiopiste
    (slug 'qwen-3-6' = 'Qwen 3.6'). Vuosiluvut ja päivämäärät pudotetaan: ne verrataan
    erikseen julkaisupäivään."""
    words, versions, sizes = set(), set(), set()
    for text in texts:
        t = text.lower().split("/", 1)[-1].split(": ", 1)[-1].replace("+", " plus ")
        # 3-6 → 3.6 (1–2-numeroinen osa), ei 3-1b (koko) eikä -20241022 (päivämäärä)
        t = re.sub(r"(?<=\d)-(?=\d{1,2}(?![\d]|[bkmt]))", ".", t)
        for tok in _TOKEN.findall(t):
            if tok[-1] == "k" and tok[:-1].isdigit():
                continue  # kontekstin pituus (128k)
            if _SIZE.match(tok):
                sizes.add(tok)
            elif _VERSION.match(tok):
                if len(tok.split(".")[0]) < 4:  # 2025, 20250728 → päivämäärä
                    versions.add(tok)
            elif tok not in NOISE_WORDS:
                words.add(tok)
    return Tokens(frozenset(words), frozenset(versions), frozenset(sizes))


def _major(versions: frozenset[str]) -> frozenset[str]:
    """Versiot ilman nollapäätteitä vertailua varten (3.0 = 3)."""
    return frozenset(re.sub(r"(\.0)+$", "", v) for v in versions)


def match(alias: Tokens, model: Tokens) -> tuple[float, str | None]:
    """(pisteet 0–1, hylkäyksen syy). Pisteet = Jaccard kaikista tunnisteista."""
    if alias.versions and model.versions and _major(alias.versions) != _major(model.versions):
        return 0.0, "versio"
    if alias.sizes and model.sizes and alias.sizes != model.sizes:
        return 0.0, "koko"
    variant = (alias.words ^ model.words) & VARIANT_WORDS
    if variant:
        return 0.0, f"variantti ({', '.join(sorted(variant))})"
    family = {w for w in (alias.words & model.words) - VARIANT_WORDS if len(w) >= 3}
    if not family:
        return 0.0, "ei yhteistä perhenimeä"  # pelkkä 'large', 'pro' tai 'l' ei riitä
    union = alias.all | model.all
    return len(alias.all & model.all) / len(union), None


def vendor_matches(vendor: str, provider: str, model: Tokens) -> bool:
    """Lähteen valmistaja (esim. 'meta-llama', 'qwen') vs. Epochin organisaatio ('Meta AI',
    'Alibaba') tai mallin perhenimi. Etuliitevertailu: 'mistralai' ~ 'mistral'."""
    vt = [w for w in re.findall(r"[a-z]+", vendor.lower()) if len(w) >= 3]
    pt = [w for w in re.findall(r"[a-z]+", provider.lower()) if len(w) >= 3] + [
        w for w in model.words if len(w) >= 3
    ]
    return any(a.startswith(b) or b.startswith(a) for a in vt for b in pt)


@dataclass(frozen=True)
class SourceName:
    source_name: str
    alias: str
    display: str
    vendor: str
    seen_date: date | None


def load_source_names(paths: Paths, unknown: pl.DataFrame) -> list[SourceName]:
    """Tunnistamattomien nimien metatiedot uusimmista raakatiedostoista."""
    wanted = {
        s: set(unknown.filter((pl.col("source_name") == s) & (pl.col("kind") == "alias"))["name"])
        for s in PRICE_SOURCES
    }
    out: list[SourceName] = []
    or_files = sorted((paths.raw_dir / "openrouter").glob("models_*.json"))
    if or_files and wanted["openrouter"]:
        for m in json.loads(or_files[-1].read_text(encoding="utf-8")).get("data", []):
            if m.get("id") in wanted["openrouter"]:
                created = m.get("created")
                seen = datetime.fromtimestamp(created, UTC).date() if created else None
                out.append(SourceName("openrouter", m["id"], m.get("name") or m["id"],
                                      m["id"].split("/", 1)[0], seen))  # fmt: skip
    lp_files = sorted((paths.raw_dir / "llm_prices").glob("historical-v1_*.json"))
    if lp_files and wanted["llm_prices"]:
        seen_ids: set[str] = set()
        for r in json.loads(lp_files[-1].read_text(encoding="utf-8")).get("prices", []):
            rid = r.get("id")
            if rid in wanted["llm_prices"] and rid not in seen_ids:
                seen_ids.add(rid)
                fd = r.get("from_date")
                seen = date.fromisoformat(fd) if fd else None
                out.append(SourceName("llm_prices", rid, r.get("name") or rid,
                                      r.get("vendor") or "", seen))  # fmt: skip
    return out


def candidates(
    names: list[SourceName],
    models: pl.DataFrame,
    *,
    only_unpriced: bool = True,
    top: int = 3,
    min_score: float = 0.4,
) -> pl.DataFrame:
    """Parhaat ehdokkaat mallia kohden. Sarakkeet: model_id, malli, valmistaja, julkaistu,
    theta, source_name, alias, nimi lähteessä, vendor, päivä-ero, pisteet, huom.

    Lähteen nimi ehdotetaan vain sille mallille, jolle se sopii parhaiten (pisteet, sitten
    lähin päivämäärä) kaikista malleista, myös hinnallisista. Näin esim. päivämäärätön
    'gemini-2.5-flash' ei päädy kolmelle eri versiolle, eikä 'kimi-k2-0905' heinäkuun
    versiolle, jos syyskuun versio on olemassa."""
    model_rows = list(models.iter_rows(named=True))
    model_tokens = [tokenize(m["model_id"], m["display_name"]) for m in model_rows]
    best: dict[tuple[str, str], tuple[float, int, dict]] = {}  # (lähde, alias) → paras
    for n in names:
        nt = tokenize(n.alias, n.display)
        for m, mt in zip(model_rows, model_tokens, strict=True):
            score, reason = match(nt, mt)
            if reason is not None or score < min_score:
                continue
            gap = (n.seen_date - m["release_date"]).days if n.seen_date else 0
            key = (n.source_name, n.alias)
            if key not in best or (score, -abs(gap)) > best[key][:2]:
                best[key] = (score, -abs(gap), m)
    by_name = {(n.source_name, n.alias): n for n in names}
    per_model: dict[str, list[tuple[float, SourceName]]] = {}
    for key, (score, _, m) in best.items():
        if only_unpriced and m["has_price"]:
            continue
        per_model.setdefault(m["model_id"], []).append((score, by_name[key]))
    rows = []
    for m, mt in zip(model_rows, model_tokens, strict=True):
        scored = per_model.get(m["model_id"], [])
        for score, n in sorted(scored, key=lambda x: (-x[0], x[1].alias))[:top]:
            gap = (n.seen_date - m["release_date"]).days if n.seen_date else None
            notes = []
            if n.vendor and not vendor_matches(n.vendor, m["provider"], mt):
                notes.append("eri valmistaja? (hienosäätö)")
            if any(k in n.alias.lower() for k in ROLLING_MARKERS):
                notes.append("liukuva nimi?")
            if gap is not None and abs(gap) > MAX_DATE_GAP_DAYS:
                notes.append(f"päivä-ero > {MAX_DATE_GAP_DAYS} pv")
            rows.append({
                "model_id": m["model_id"], "malli": m["display_name"],
                "valmistaja": m["provider"], "julkaistu": m["release_date"],
                "theta": round(m["theta"], 2), "source_name": n.source_name,
                "alias": n.alias, "nimi_lähteessä": n.display, "vendor": n.vendor,
                "päivä_ero": gap, "pisteet": round(score, 2), "huom": ", ".join(notes),
            })  # fmt: skip
    schema = {"model_id": pl.String, "malli": pl.String, "valmistaja": pl.String,
              "julkaistu": pl.Date, "theta": pl.Float64, "source_name": pl.String,
              "alias": pl.String, "nimi_lähteessä": pl.String, "vendor": pl.String,
              "päivä_ero": pl.Int64, "pisteet": pl.Float64, "huom": pl.String}  # fmt: skip
    return pl.DataFrame(rows, schema=schema, orient="row") if rows else pl.DataFrame(schema=schema)


def yaml_line(row: dict) -> str:
    return (f"  - {{source_name: {row['source_name']}, alias: {row['alias']}, "
            f"model_id: {row['model_id']}}}")  # fmt: skip
