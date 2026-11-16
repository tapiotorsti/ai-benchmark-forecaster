# Uuden datalähteen lisääminen

Ohje uuden benchmark- tai hintalähteen tuomiseen. Esimerkkeinä ovat olemassa olevat adapterit `src/forecaster/ingest/openrouter.py` (hinnat, JSON) ja `epoch_ai.py` (tulokset, ZIP + CSV). Claude Code -käyttäjät voivat ajaa saman työnkulun skillillä `/add-data-source <nimi>`.

## 1. Selvitä ensin: lisenssi ja muoto

- **Lisenssi ja käyttöehdot.** Repo on julkinen (MIT). Lähdettä saa käyttää vain, jos sen ehdot sallivat automaattisen haun. Jos lisenssiä ei ole, data on vain paikalliseen käyttöön, kuten simonw/llm-prices. Silloin siitä johdettuja tulosteita ei saa commitoida, ja ne lisätään `.gitignore`en.
- **Muoto ja kentät:** mallin nimi, benchmark, pistemäärä ja sen asteikko, julkaisupäivä sekä hinnoille input- ja output-hinta ja voimassaolopäivä.
- **Testiaineisto:** tallenna pieni testiaineisto kansioon `tests/fixtures/<lähde>/`. Sen on oltava **keksittyä** tai lisenssin sallimaa, ei kopio lisensoimattomasta lähteestä.

## 2. Adapteri `src/forecaster/ingest/<lähde>.py`

Adapterissa on kaksi funktiota:

```python
SOURCE = "<lähde>"

def fetch(paths: Paths, as_of: date, client=None) -> Path:
    """Lataa raakatiedoston kansioon data/raw/<lähde>/ (ei uudelleen samana päivänä)."""

def ingest(repo: Repository, paths: Paths, payload: Path, ...) -> IngestReport:
    def work(run_id: str, report: IngestReport) -> None:
        ...  # jäsennys, validointi (Pydantic-rivimallit db/models.py), upsertit
    return run_ingest(repo, SOURCE, work)
```

`run_ingest` (`ingest/base.py`) hoitaa lokin `ingest_runs`, tuntemattomien nimien tallennuksen ja virheiden kirjauksen. Adapteri vastaa lisäksi seuraavista:

- **Mallinimet → `model_id`:** nimet muutetaan aliastaulun kautta kanonisiksi tunnisteiksi. Tuntemattomat nimet lisätään listaan `report.unknown_aliases` *pelkkinä tunnisteina*, ja mahdollinen ehdotus kirjataan kenttään `report.alias_suggestions`. Nimiä ei pudoteta hiljaa pois.
- **Puutteelliset rivit:** rivit, joilta puuttuu pakollinen tieto, viedään karanteeniin (`repo.quarantine`).
- **SQL:** kaikki SQL kirjoitetaan tiedostoon `db/repository.py`.
- **API-avaimet:** avaimet luetaan ympäristömuuttujista, eikä niitä kirjoiteta koodiin.

## 3. Konfiguraatio

- **Uudet benchmarkit:** lisää ne tiedostoon `config/benchmarks.yaml` (domain, raw_min/raw_max, guessing_floor normalisoidulla asteikolla, contamination_risk ja weight). Perustele arvausraja.
- **Nimien käsin kohdistus:** lisää kohdistukset tiedostoon `config/model_aliases.yaml`.

## 4. Kytkentä

- **CLI:** lisää lähde komentoon `forecaster ingest <lähde>` ja tarvittaessa `all`-joukkoon (`cli.py`).
- **Viikoittainen päivitys:** lisää `Source` listaan `DEFAULT_SOURCES` tiedostossa `src/forecaster/update.py`. Järjestyksellä on väliä: hintalähde, joka rakentaa täydennyksen muiden päälle, tulee viimeisenä.
- **README:** lisää rivi lisenssitaulukkoon (osio "Data ja lisenssit").

## 5. Testit (ilman verkkoa)

- Jäsennys testiaineistosta tuottaa odotetun rivimäärän ja arvot.
- Kahdesti ajettu tuonti ei tuota duplikaatteja.
- Tuntematon nimi ja puuttuva päivämäärä päätyvät raporttiin tai karanteeniin.

Aja lopuksi `ruff check .`, `ruff format --check .` ja `pytest`.
