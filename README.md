# AI Benchmark Aggregator & Forecaster

Kokoaa tekoälymallien benchmark-tulokset ja hinnat yhteen DuckDB-tietokantaan, sijoittaa mallit yhteiselle kyvykkyysasteikolle (θ, jatkuva 2PL-IRT) ja ennustaa kyvykkyysrintaman kehitystä sekä API-hintojen laskua.

- Tekninen määrittely: [ai_benchmark_aggregator_forecaster_tekninen_m_rittely.md](ai_benchmark_aggregator_forecaster_tekninen_m_rittely.md)
- Toteutussuunnitelma: [TOTEUTUSSUUNNITELMA.md](TOTEUTUSSUUNNITELMA.md)

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
| `dashboard` | valmis: Streamlit-sovellus, jossa on 6 sivua: Yleiskuva, Mallit, Benchmarkit, Kustannukset, Simulaattori ja Datan laatu. Vaatii `fit`-ajon. Tietokanta avataan vain lukutilassa |

## Data ja lisenssit

Koodi on MIT-lisensoitu (ks. [LICENSE](LICENSE)). Repo ei sisällä ladattua dataa; se haetaan komennolla `forecaster ingest all` kansioon `data/` (ei versionhallinnassa).

| Lähde | Käyttö | Lisenssi |
|---|---|---|
| [Epoch AI](https://epoch.ai/data) | benchmark-tulokset, mallien metatiedot | CC BY 4.0. Diagnostiikkaraportit (`docs/irt_diagnostiikka.md`, `docs/datakokeilu_epoch_ai.md`) on johdettu tästä datasta |
| [OpenRouter](https://openrouter.ai/) | nykyhinnat (API) | palvelun käyttöehdot |
| [simonw/llm-prices](https://github.com/simonw/llm-prices) | hintahistoria | **ei lisenssiä**, joten vain paikalliseen käyttöön. Siksi `docs/ennusteet.md` generoidaan paikallisesti eikä se ole repossa, eikä dashboardia julkaista verkkoon tällä datalla |
| `config/manual_prices.yaml` | vanhojen versioiden julkaisuhinnat | käsin koottu palveluntarjoajien hinnastoista |

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
app/               Streamlit: dashboard.py (navigaatio) + pages/ (6 sivua)
tests/
```
