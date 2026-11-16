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
Tiedosto `src/forecaster/ingest/<lähde>.py`, joka käyttää yhteistä runkoa `ingest/base.py` (`download`, `run_ingest`). Ihmisille tarkoitettu ohje ja koodirunko: [docs/uusi_datalahde.md](../../../docs/uusi_datalahde.md). Adapteri määrittää vain lähdekohtaiset osat:
1. `fetch(paths, as_of)` – lataa raakatiedoston `data/raw/<lähde>/`:aan (`download`: aikakatkaisu ja uudelleenyritykset). Ei koskaan kovakoodattuja API-avaimia (ympäristömuuttujat).
2. `ingest(repo, paths, payload, …)` – jäsennys Polarsilla, Pydantic-rivimallit (`db/models.py`), upsertit `run_ingest`-rungon sisällä.
3. Mallinimet kanoniseen `model_id`:hen aliastaulun kautta (A2). Tuntemattomat nimet `report.unknown_aliases`:iin **pelkkinä tunnisteina** (K7 vertaa niitä ajojen välillä), ehdotukset `report.alias_suggestions`:iin.
4. Rivit ilman pakollista tietoa (esim. `release_date`) karanteeniin (A3).

`run_ingest` hoitaa `ingest_runs`-lokin, tuntemattomien nimien tallennuksen (`ingest_unknown_names`) ja virheiden kirjauksen.

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
- Lisää `Source` `src/forecaster/update.py`:n `DEFAULT_SOURCES`-listaan (viikoittainen päivitys, K7).
- Lisää rivi README:n "Data ja lisenssit" -taulukkoon. Lisensoimaton lähde: johdetut tulosteet `.gitignore`en.
- Aja `/verify`.
- Kerro käyttäjälle: rivimäärät, uudet mallit ja benchmarkit, tuntemattomat aliakset ja montako uutta siltamallia lähde toi (yhteisiä malleja vanhojen ja uusien testien välillä).
