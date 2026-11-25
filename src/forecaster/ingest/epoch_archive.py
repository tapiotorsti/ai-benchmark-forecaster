"""Epochin datapaketin arkistoidut tilannekuvat (Internet Archive) ajankohtaan sidottuun
backtestiin (6.2, A35). Data: Epoch AI, CC BY 4.0; arkisto: web.archive.org.

Tilannekuvasta käytetään vain tietoa siitä, MITKÄ tulokset (testi, malliversio) olivat
julkaistuja sillä hetkellä. Pistemäärät ja metatiedot (arvausrajat, versioiden ryhmittely)
tulevat nykyisestä datasta: metatiedot eivät paljasta tuloksia. Pisteiden myöhemmät
korjaukset eivät näy (oletus: pieniä, ei tarkistettu; rajoitus kirjattu A35:een).
Vanhimmat paketit (ennen 2025-11) ovat eri muodossa, eikä niistä löydy nykyisiä tiedostoja;
ne ohitetaan.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import httpx
import polars as pl

from forecaster.config import Paths
from forecaster.ingest.base import USER_AGENT, download
from forecaster.ingest.epoch_ai import URL, FileSpec

ARCHIVE_DIR = "epoch_ai_archive"
CDX_URL = "https://web.archive.org/cdx/search/cdx"
SNAPSHOT_URL = "https://web.archive.org/web/{ts}id_/" + URL
DEFAULT_SINCE = date(2025, 11, 1)


@dataclass(frozen=True)
class Snapshot:
    timestamp: str  # Internet Archiven aikaleima YYYYMMDDhhmmss
    path: Path

    @property
    def day(self) -> date:
        return datetime.strptime(self.timestamp[:8], "%Y%m%d").date()


def _client() -> httpx.Client:
    return httpx.Client(timeout=120.0, follow_redirects=True,
                        headers={"User-Agent": USER_AGENT})  # fmt: skip


def list_archived(client: httpx.Client | None = None, since: date = DEFAULT_SINCE) -> list[str]:
    """Arkistoidut aikaleimat (sisällöltään erilaiset, tila 200), vanhimmasta uusimpaan."""
    own = client is None
    client = client or _client()
    try:
        resp = client.get(CDX_URL, params={
            "url": URL.removeprefix("https://"), "output": "json", "fl": "timestamp,statuscode",
            "collapse": "digest", "from": since.strftime("%Y%m%d"),
        })  # fmt: skip
        resp.raise_for_status()
        rows = resp.json()
    finally:
        if own:
            client.close()
    return sorted(ts for ts, status in rows[1:] if status == "200")


def monthly(timestamps: list[str]) -> list[str]:
    """Kunkin kalenterikuukauden viimeinen tilannekuva (riittää kuukausittaisille lähtöpäiville)."""
    return [c[0] for c in by_month(timestamps)]


def by_month(timestamps: list[str]) -> list[list[str]]:
    """Kuukausittaiset ehdokkaat uusimmasta vanhimpaan (viallisen tilalle kokeillaan seuraavaa)."""
    groups: dict[str, list[str]] = {}
    for ts in sorted(timestamps):
        groups.setdefault(ts[:6], []).append(ts)
    return [sorted(g, reverse=True) for _, g in sorted(groups.items())]


def _fetch_one(ts: str, dest: Path, client: httpx.Client) -> bool:
    """Lataa ja tarkistaa paketin. False = ei kelvollinen zip (poistetaan)."""
    download(SNAPSHOT_URL.format(ts=ts), dest, client=client)
    raw = dest.read_bytes()
    if raw[:2] == b"\x1f\x8b":  # arkisto palauttaa osan paketeista gzip-koodattuna
        with contextlib.suppress(OSError):  # ei gzip sittenkään → tarkistetaan zipinä
            dest.write_bytes(gzip.decompress(raw))
    if zipfile.is_zipfile(dest):
        return True
    dest.unlink()
    dest.parent.rmdir()
    return False


def fetch_snapshots(
    paths: Paths, client: httpx.Client | None = None, since: date = DEFAULT_SINCE
) -> list[Snapshot]:
    """Lataa puuttuvat kuukausitilannekuvat kansioon data/raw/epoch_ai_archive/<aikaleima>/.
    Kuukauden uusimman ollessa viallinen (esim. tuntematon siirtokoodaus) kokeillaan
    saman kuukauden aiempaa."""
    own = client is None
    client = client or _client()
    try:
        out = []
        for candidates in by_month(list_archived(client, since)):
            for ts in candidates:
                dest = paths.raw_dir / ARCHIVE_DIR / ts / "benchmark_data.zip"
                if (dest.is_file() and zipfile.is_zipfile(dest)) or _fetch_one(ts, dest, client):
                    out.append(Snapshot(ts, dest))
                    break
        return out
    finally:
        if own:
            client.close()


def local_snapshots(paths: Paths) -> list[Snapshot]:
    """Jo ladatut kelvolliset tilannekuvat (ei verkkoa)."""
    root = paths.raw_dir / ARCHIVE_DIR
    if not root.is_dir():
        return []
    return [Snapshot(d.name, d / "benchmark_data.zip") for d in sorted(root.iterdir())
            if zipfile.is_zipfile(d / "benchmark_data.zip")]  # fmt: skip


def presence(snapshot: Path, specs: list[FileSpec]) -> set[tuple[str, str]]:
    """Tilannekuvassa olleet (benchmark_id, malliversio) -parit. ZIPiä ei pureta levylle."""
    found: set[tuple[str, str]] = set()
    with zipfile.ZipFile(snapshot) as zf:
        by_name = {Path(n).name: n for n in zf.namelist()}
        for spec in specs:
            member = by_name.get(spec.source_file)
            if member is None:
                continue
            df = pl.read_csv(io.BytesIO(zf.read(member)), infer_schema_length=0)
            if "Model version" not in df.columns:
                continue
            versions = df["Model version"].drop_nulls().str.strip_chars()
            found |= {(spec.benchmark_id, v) for v in versions.unique().to_list() if v}
    return found


def snapshot_for(snapshots: list[Snapshot], cutoff: date) -> Snapshot | None:
    """Uusin tilannekuva, joka on otettu viimeistään lähtöpäivänä."""
    before = [s for s in snapshots if s.day <= cutoff]
    return max(before, key=lambda s: s.timestamp) if before else None
