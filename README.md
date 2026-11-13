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
| `ingest` | valmis: `ingest all` (Epoch + OpenRouter), `ingest epoch_ai --from <kansio>`, `ingest internal --from <csv>` |
| `fit` | valmis: karsinta → sovitus → poikkeamat → uudelleensovitus. `--report` kirjoittaa `docs/irt_diagnostiikka.md` (`--stability none\|key\|all`), `--domain <osa-alue>` linkitetään overall-asteikkoon, `--min-obs`, `--no-outliers`, `--no-anchors` |
| `forecast` | K5 |
| `dashboard` | runko, varsinainen sisältö K6 |

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
app/dashboard.py   Streamlit (K6)
tests/
```
