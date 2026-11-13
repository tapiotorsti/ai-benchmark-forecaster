# CLAUDE.md – AI Benchmark Aggregator & Forecaster

Projekti kokoaa AI-mallien benchmark-tulokset DuckDB:hen, sijoittaa mallit yhteiselle θ-asteikolle jatkuvalla 2PL-IRT-mallilla (PyTorch) ja ennustaa kyvykkyysrintaman sekä hintojen kehitystä.

## Lähdedokumentit (lue ennen isoja muutoksia)
- [ai_benchmark_aggregator_forecaster_tekninen_m_rittely.md](ai_benchmark_aggregator_forecaster_tekninen_m_rittely.md) – matematiikka, skeema ja arkkitehtuuri. **Totuuden lähde kaavoille.**
- [TOTEUTUSSUUNNITELMA.md](TOTEUTUSSUUNNITELMA.md) – kokonaisuudet K0–K7, hyväksymiskriteerit, määrittelyn aukot A1–A7 ja **tilataulukko**. Päivitä tilataulukko, kun kokonaisuus etenee.

## Ympäristö (Windows)
- Virtuaaliympäristö: `.venv` (Python 3.12). Kutsu aina `.venv\Scripts\python.exe` – ei järjestelmän Pythonia.
- Asennus: `.venv\Scripts\python.exe -m pip install -e ".[dev,ui]"` **ja heti perään** `.venv\Scripts\python.exe scripts\fix_editable_pth.py`. Polussa on ä-kirjaimia, ja Python 3.12 lukee editable-asennuksen .pth-tiedoston väärällä merkistöllä. Ilman korjausta `forecaster`-komento kaatuu virheeseen `ModuleNotFoundError`. Testit toimivat ilmankin (`pythonpath = ["src"]`).
- Testit: `.venv\Scripts\python.exe -m pytest`
- Lint: `.venv\Scripts\python.exe -m ruff check .` ja `ruff format --check .`
- CLI: `.venv\Scripts\forecaster.exe <komento>` (esim. `check-config`)

## Rakenne
- `src/forecaster/` – paketti (src-layout). Määrittelyn `src/ingest`, `src/models`, `src/db` = `src/forecaster/{ingest,models,db}`.
- `config/*.yaml` – benchmarkien metatiedot ja ankkurit. Validoidaan Pydantic-malleilla `forecaster/config.py`:ssä; uusi kenttä YAMLiin vaatii myös kentän malliin (`extra="forbid"`).
- `data/` – ei versionhallinnassa. Älä commitoi `.duckdb`- tai `.parquet`-tiedostoja.
- `app/` – Streamlit-dashboard.

## Konventiot
- Koodi, tunnisteet ja SQL englanniksi; docstringit, kommentit ja käyttäjälle näkyvät viestit suomeksi.
- Taulukkodata Polarsilla (ei pandasia, ellei kirjasto vaadi).
- Kaikki satunnaisuus siemenellä (`seed`-parametri) – tulosten on oltava toistettavia.
- Testit eivät saa käyttää verkkoa: ingest-testit ajetaan tallennetuilla raakatiedostoilla `tests/fixtures/`-kansiosta.
- Testit käyttävät muistinvaraista DuckDB:tä (`duckdb.connect(":memory:")`) tai `tmp_path`ia, ei `data/benchmark.duckdb`:tä.
- Toteuttamaton CLI-komento palauttaa koodin 2 (`_not_implemented`).
- Kaikki SQL kirjoitetaan `forecaster/db/repository.py`:hyn. **DuckDB 2.x** vaatii, että jokaisen lauseen tulos luetaan loppuun ennen seuraavaa työtä samalla yhteydellä (virhe `ResourceInUseException`). Käytä aina apufunktioita `_run`, `_one` ja `_pl`, älä `conn.execute(...).fetchone()`:a. Yhteysolio ei enää tarjoa `.pl()`:tä, vaan Polars-tulos saadaan muodossa `conn.sql(q, params=...).pl()`.

## Matemaattiset sudenkuopat (älä "korjaa" näitä huomaamatta)
- Normalisoitu pistemäärä leikataan välille [1e-3, 1-1e-3] ennen BCE:tä.
- Erottelukyky `a = softplus(a_raw)`; arvausraja `c_j` on kiinteä konfiguraatiosta, ei opittava.
- Poikkeamaresiduaali on **standardoitu** (aukko A4), MAD lasketaan benchmark-kohtaisesti.
- Hinnat luetaan `model_prices`-historiasta (A1), ei `models`-taulun yksittäisestä hinnasta.
- Bootstrapissa θ-epävarmuus otetaan mukaan (A5).
- Epochin `processed_data_for_eci.csv`:n `performance` on **jo** arvausrajalla korjattu. Älä syötä sitä IRT:hen niin, että c_j > 0, koska korjaus tehtäisiin kahdesti (A8). Tuo raakatiedostot.

## Data
- Epoch AI: `https://epoch.ai/data/benchmark_data.zip` (CC BY 4.0). Puretut tiedostot ovat kansiossa `data/raw/epoch_ai/<pvm>/extracted/`. Tärkeimmät: `benchmark_metadata.csv` (arvausraja, katto, julkaisupäivä), `model_metadata.csv` (versio → malli), `<benchmark>.csv` (raakatulokset) ja `epoch_capabilities_index/eci_scores.csv` (vertailukohta).
- Konfiguraatio: `config/epoch_benchmarks.yaml` (Epochin benchmark → tunniste ja domain; rajat luetaan metatiedoista), `config/benchmarks.yaml` (vain omat testit; sama tunniste ei saa olla kummassakin), `config/model_aliases.yaml` (käsin tehdyt alias-ohitukset).
- `model_id` = slug Epochin malliryhmän nimestä, esim. `claude-3-5-sonnet-october-2024`. Versio (`claude-3-5-sonnet-20241022`) on alias.
- `guessing_floor` on **normalisoidulla** asteikolla: Epochin benchmarkeille random_baseline / score_ceiling.
- Datan kartoitus: [docs/datakokeilu_epoch_ai.md](docs/datakokeilu_epoch_ai.md), joka luodaan skriptillä `scripts/spike_epoch_bridges.py`.

## IRT-ajot (K4)
- Tuotantoajo: `forecaster.models.pipeline.run_fit`, ei `fit_and_store`. `fit_and_store` ohittaa karsinnan ja poikkeamat, ja sitä käytetään vain testeissä.
- Overall-ajo nollaa ja asettaa `evaluations.is_outlier`-liput. Domain-ajot käyttävät näitä lippuja ja vaativat olemassa olevan overall-ajon.
- Diagnostiikka: `forecaster fit --report` tuottaa tiedoston `docs/irt_diagnostiikka.md`. Tarkista ECI-Spearman (> 0.9) ja vakaus aina, kun dataa tai ankkureita muutetaan.

## Työnkulku
- Päätökset, jotka kuuluvat käyttäjälle (lähteet, kynnysarvot, rajaukset), kysytään AskUserQuestion-monivalintana.
- Uusi kokonaisuus: skill `/implement-milestone K<n>`.
- Uusi datalähde: skill `/add-data-source <nimi>`.
- Ennen valmiiksi ilmoittamista: skill `/verify`.
- IRT- tai ennustekoodin muutoksen jälkeen: pyydä `math-reviewer`-agentilta katselmointi.
