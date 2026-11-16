# AI Benchmark Aggregator & Forecaster

**Suomeksi** · [English](README.en.md)

Kokoaa tekoälymallien benchmark-tulokset ja hinnat yhteen DuckDB-tietokantaan, sijoittaa mallit yhteiselle kyvykkyysasteikolle (θ, jatkuva 2PL-IRT) ja ennustaa kyvykkyysrintaman kehitystä sekä API-hintojen laskua.

- Metodologia ja rajoitukset: [docs/metodologia.md](docs/metodologia.md)
- Tekninen määrittely: [ai_benchmark_aggregator_forecaster_tekninen_m_rittely.md](ai_benchmark_aggregator_forecaster_tekninen_m_rittely.md)
- Toteutussuunnitelma: [TOTEUTUSSUUNNITELMA.md](TOTEUTUSSUUNNITELMA.md)
- Uuden datalähteen lisääminen: [docs/uusi_datalahde.md](docs/uusi_datalahde.md)

## Pikaopas

```powershell
forecaster update        # hae data, sovita IRT, laske ennusteet (noin 30 s – 2 min)
forecaster dashboard     # avaa dashboard selaimeen (http://127.0.0.1:8501)
```

## Asennus

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev,ui]"
python scripts\fix_editable_pth.py   # tarvitaan, jos polussa on ä/ö-kirjaimia (Python < 3.13)
```

## Käyttö

```powershell
forecaster --help
forecaster check-config     # validoi config/*.yaml
pytest                      # testit (nopeat)
pytest -m slow              # Monte Carlo -validoinnit (~30 s)
```

| Komento | Tila |
|---|---|
| `check-config` | valmis |
| `init-db` | valmis: luo `data/benchmark.duckdb` ja synkronoi benchmarkit |
| `ingest` | valmis: `ingest all` (Epoch + OpenRouter + llm-prices), `ingest epoch_ai --from <kansio>`, `ingest llm_prices --from <json>`, `ingest internal --from <csv>` |
| `fit` | valmis: karsinta → sovitus → poikkeamat → uudelleensovitus. `--report` kirjoittaa `docs/irt_diagnostiikka.md` (`--stability none\|key\|all`), `--domain <osa-alue>` linkitetään overall-asteikkoon, `--min-obs`, `--no-outliers`, `--no-anchors` |
| `forecast` | valmis: rintamatrendi, kustannusten puoliintumisaika (3 tasoa) ja backtest. Tulos kirjoitetaan tiedostoon `docs/ennusteet.md` (paikallinen, ei repossa). Asetukset ovat tiedostossa `config/forecast.yaml` |
| `dashboard` | valmis: Streamlit-sovellus, jossa on 6 sivua: Yleiskuva, Mallit, Benchmarkit, Kustannukset, Simulaattori ja Datan laatu. Vaatii `fit`-ajon. Tietokanta avataan vain lukutilassa. Päivitysten hälytykset näkyvät jokaisen sivun yläreunassa. `--port`, `--address` |
| `update` | valmis: koko päivitys yhdellä komennolla: haku, tuonti, IRT (kokonaisuus ja osa-alueet), ennusteet ja tarkistukset. Hälyttää, jos haku tai tuonti kaatuu, tulee uusia tunnistamattomia nimiä tai IRT ei konvergoi. Paluukoodi 1 kriittisestä virheestä. Loki: `data/logs/` |

## Automaattinen päivitys

`forecaster update` ajetaan omalla koneella viikoittain Windowsin Tehtävien ajoituksella:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\schedule_update.ps1                          # maanantaisin klo 7
powershell -ExecutionPolicy Bypass -File scripts\schedule_update.ps1 -Day Sunday -Time 20:00  # oma ajankohta
powershell -ExecutionPolicy Bypass -File scripts\schedule_update.ps1 -Remove                  # poista ajastus
```

Ajo tehdään piilotettuna nykyisen käyttäjän oikeuksin, kun käyttäjä on kirjautuneena. Jos kone on ajankohtana sammuksissa, ajo tehdään seuraavalla käynnistyksellä. Tulokset ja hälytykset näkyvät dashboardin yläreunassa ja Datan laatu -sivulla. Jos viimeisestä onnistuneesta päivityksestä on yli 10 päivää, dashboard varoittaa vanhasta datasta.

Päivitys ajetaan omalla koneella eikä GitHub Actionsissa: tietokanta ja dashboard ovat paikallisia, eikä lisensoimatonta hintadataa saa tallentaa julkiseen repoon. GitHub Actions ajaa vain lintin ja testit (`.github/workflows/ci.yml`).

## Dashboard etäkäytössä (vain omille laitteille)

Dashboard kuuntelee oletuksena vain omaa konetta (`127.0.0.1:8501`). Omille laitteille sen saa näkyviin [Tailscalella](https://tailscale.com/) ilman julkista osoitetta:

1. Asenna Tailscale tälle koneelle ja puhelimeen tai muille laitteille, ja kirjaudu samalla tilillä.
2. Käynnistä dashboard: `forecaster dashboard`
3. Jaa se omaan Tailscale-verkkoon: `tailscale serve --bg 8501`
4. Avaa toisella laitteella `https://<koneen-nimi>.<tailnet>.ts.net`. Tarkka osoite näkyy komennolla `tailscale serve status`.

Pysäytys: dashboard suljetaan painamalla Ctrl+C sen ikkunassa, ja jako lopetetaan komennolla `tailscale serve reset`. Käynnissä oleva dashboard vie noin 0,5 GB muistia eikä tyhjäkäynnillä kuormita prosessoria. Laskenta tapahtuu vain sivuja avattaessa: yleiskuvan ensimmäinen lataus kestää noin 13 s ja simulaattorin sovitus noin 5 s.

## Data ja lisenssit

Koodi on MIT-lisensoitu (ks. [LICENSE](LICENSE)). Repo ei sisällä ladattua dataa; se haetaan komennolla `forecaster ingest all` kansioon `data/` (ei versionhallinnassa).

| Lähde | Käyttö | Lisenssi |
|---|---|---|
| [Epoch AI](https://epoch.ai/data) | benchmark-tulokset, mallien metatiedot | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Diagnostiikkaraportit (`docs/irt_diagnostiikka.md`, `docs/datakokeilu_epoch_ai.md`) on johdettu tästä datasta (normalisointi, aggregointi, IRT-sovitus), ja lähdemaininta on jokaisen raportin alussa |
| [OpenRouter](https://openrouter.ai/) | nykyhinnat (API) | palvelun käyttöehdot |
| [simonw/llm-prices](https://github.com/simonw/llm-prices) | hintahistoria | **ei lisenssiä**, joten vain paikalliseen käyttöön. Siksi `docs/ennusteet.md` generoidaan paikallisesti eikä se ole repossa, eikä dashboardia julkaista verkkoon tällä datalla |
| `config/manual_prices.yaml` | vanhojen versioiden julkaisuhinnat | käsin koottu palveluntarjoajien hinnastoista |

Kolmannen osapuolen tiedostot: `.claude/skills/developing-with-streamlit/` on Streamlitin agenttiohje (Streamlit Inc. / Snowflake Inc., Apache License 2.0). Tiedostot ovat muuttamattomia, ja lisenssin teksti on samassa kansiossa (`LICENSE`).

Testit käyttävät vain keksittyä dataa (`tests/fixtures/`), eivätkä ne käytä verkkoa.

## Rakenne

```text
config/            benchmarks.yaml, anchors.yaml
data/raw/          ladattu raakadata (ei versionhallinnassa)
src/forecaster/
  config.py        polut ja YAML-validointi
  cli.py           komentorivi
  db/              DuckDB-skeema ja kyselyt (K1)
  ingest/          datalähteiden adapterit (K3)
  models/          IRT-moottori (K2) ja ennusteet (K5)
  dashboard/       dashboardin laskenta, kuvaajat ja välimuisti (K6)
  update.py        automaattinen päivitys ja hälytykset (K7)
app/               Streamlit: dashboard.py (navigaatio) + pages/ (6 sivua)
scripts/           schedule_update.ps1 (ajastus), fix_editable_pth.py, datakokeilu
docs/              metodologia, datalähdeohje, diagnostiikkaraportit
tests/
```
