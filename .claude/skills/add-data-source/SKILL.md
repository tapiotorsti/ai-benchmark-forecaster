---
name: add-data-source
description: Lisää uusi datalähde (benchmark-tulokset tai hinnat) ingest-putkeen projektin yhteisen rungon mukaisesti. Käytä kun käyttäjä haluaa tuoda dataa uudesta lähteestä, esim. "lisää Artificial Analysis", "tuo SWE-bench leaderboard".
argument-hint: <lähteen_nimi>
---

# Uuden datalähteen lisääminen

Argumentti: lähteen tunnus snake_case-muodossa (esim. `livebench`). Siitä tulee `source_name`-arvo tietokantaan.

## 1. Selvitä lähde ennen koodia
- Datan muoto (CSV / JSON / API), URL ja päivitystiheys.
- Käyttöehdot ja lisenssi. Jos ehdot kieltävät automaattisen haun tai uudelleenjulkaisun, **pysähdy ja kysy käyttäjältä**.
- Mitkä kentät vastaavat tauluja `models`, `evaluations` ja `model_prices`. Erityisesti: mallin nimi, benchmark, pistemäärä ja sen asteikko, julkaisupäivä.
- Lataa yksi esimerkkitiedosto ja tallenna se `tests/fixtures/<lähde>/` -kansioon (pienennä tarvittaessa muutamaan kymmeneen riviin).

## 2. Toteuta adapteri
Tiedosto `src/forecaster/ingest/<lähde>.py`, joka käyttää yhteistä runkoa `ingest/base.py` (jos runkoa ei vielä ole, se kuuluu K3:een – toteuta se ensin). Adapteri määrittää vain lähdekohtaiset osat:
1. `fetch()` – HTTPX, aikakatkaisu ja uudelleenyritykset; ei koskaan kovakoodattuja API-avaimia (ympäristömuuttujat).
2. `parse()` – raakadata Polars-DataFrameksi ja Pydantic-validointi.
3. Mallinimet kanoniseen `model_id`:hen aliastaulun kautta (A2). Tuntemattomat nimet raporttiin, ei hiljaa pois.
4. Rivit ilman pakollista tietoa (esim. `release_date`) karanteeniin (A3).

Yhteinen runko hoitaa raakadatan tallennuksen `data/raw/<lähde>/<päivämäärä>.parquet`, normalisoinnin, upsertin ja `ingest_runs`-lokin.

## 3. Konfiguraatio
- Uudet benchmarkit `config/benchmarks.yaml`:iin: domain, metric_type, raw_min/raw_max, guessing_floor, contamination_risk, weight. Perustele arvausraja (esim. vastausvaihtoehtojen määrä).
- Uudet nimialiakset `config/model_aliases.yaml`:iin.
- Elo-tyyppiset metriikat: muunnos aukon A7 konfiguraatiolla.

## 4. Testit (ei verkkoa)
- Jäsennys fixture-tiedostosta tuottaa odotetun rivimäärän ja arvot.
- Kahdesti ajettu ingest ei tuota duplikaatteja.
- Tuntematon alias ja puuttuva päivämäärä päätyvät raporttiin / karanteeniin.

## 5. Kytke ja varmista
- Lisää lähde `forecaster ingest <lähde>` -komennon vaihtoehtoihin ja `all`-joukkoon.
- Aja `/verify`.
- Kerro käyttäjälle: rivimäärät, uudet mallit ja benchmarkit, tuntemattomat aliakset ja montako uutta siltamallia lähde toi (yhteisiä malleja vanhojen ja uusien testien välillä).
