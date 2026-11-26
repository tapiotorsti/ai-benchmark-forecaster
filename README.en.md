# AI Benchmark Aggregator & Forecaster

[Suomeksi](README.md) · **English**

Collects benchmark results and API prices for AI models into a single DuckDB database, places all models on a common capability scale (θ, continuous 2PL item response theory), and forecasts how the capability frontier and API prices will develop.

**What it does**

- **Common scale across benchmarks.** Benchmarks differ in difficulty, and no model has been run on every test, so raw percentages are not comparable. A continuous 2PL-IRT model estimates benchmark difficulty, discrimination and model capability jointly, with standard errors. The scale is anchored at GPT-4 (June 2023) = 0 and GPT-5 = 3.5.
- **Capability forecast.** Fits a trend to the Pareto frontier (models that beat everything released before them), bootstraps it with θ uncertainty, and reports trend and prediction intervals 6–24 months ahead. Backtested against what actually happened.
- **Cost forecast.** For a chosen capability level, tracks the cheapest qualifying model over time, estimates the price halving time, and detects when a level has reached the market price floor.
- **Dashboard and automation.** A Streamlit dashboard (overview, models, benchmarks, costs, a weight simulator, data quality), plus a one-command weekly update with alerts.

> **Language note:** the code and identifiers are in English, and so are the methodology and the data-source guide. Docstrings, the dashboard UI, the generated reports, the technical specification and the implementation plan are in Finnish.

## Documentation

- Methodology and limitations: [docs/metodologia.en.md](docs/metodologia.en.md). **Read the limitations section before interpreting forecasts.**
- Adding a data source: [docs/uusi_datalahde.en.md](docs/uusi_datalahde.en.md)
- Technical specification (Finnish): [ai_benchmark_aggregator_forecaster_tekninen_m_rittely.md](ai_benchmark_aggregator_forecaster_tekninen_m_rittely.md)
- Implementation plan and design decisions, numbered gaps A1, A2, … (Finnish): [TOTEUTUSSUUNNITELMA.md](TOTEUTUSSUUNNITELMA.md)

## Quick start

```powershell
forecaster update        # fetch data, fit IRT, compute forecasts (about 30 s – 2 min)
forecaster dashboard     # open the dashboard (http://127.0.0.1:8501)
```

## Installation

Requires Python 3.11+ (developed on 3.12, Windows).

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev,ui]"
python scripts\fix_editable_pth.py   # only if the project path contains non-ASCII characters (Python < 3.13)
```

On Linux/macOS, create the virtual environment with `python3 -m venv .venv` and activate it with `source .venv/bin/activate`. The CI runs on Ubuntu.

## Usage

```powershell
forecaster --help
forecaster check-config     # validate config/*.yaml
pytest                      # tests (fast)
pytest -m slow              # Monte Carlo validations (~30 s)
```

| Command | What it does |
|---|---|
| `check-config` | Validates the YAML configuration in `config/`. |
| `init-db` | Creates `data/benchmark.duckdb` and syncs the benchmark definitions. |
| `ingest` | Imports data. `ingest all` = Epoch AI + OpenRouter + llm-prices. Local files: `ingest epoch_ai --from <folder>`, `ingest llm_prices --from <json>`, `ingest internal --from <csv>`. |
| `aliases` | Suggests matches for unrecognised names from the price sources (OpenRouter, llm-prices), limited to models that have θ but no price. Rejects candidates whose version, size or variant (mini, flash, …) differs, and warns about rolling names, a different vendor and large date gaps. Prints ready-made lines for `config/model_aliases.yaml`; check them by hand. Options: `--source`, `--all-models`, `--top`, `--min-score`. |
| `fit` | Fits the IRT model: pruning → fit → outlier detection → refit. `--report` writes `docs/irt_diagnostiikka.md` (`--stability none\|key\|all`). `--domain <domain>` fits one domain on the overall scale. Also `--min-obs`, `--no-outliers`, `--no-anchors`. |
| `forecast` | Frontier trend, cost halving times at capability levels set in `config/forecast.yaml`, backtests, and how earlier forecasts turned out. Writes `docs/ennusteet.md` (local only, not in the repo). Settings: `config/forecast.yaml`. |
| `validate` | Validates the θ scale by predicting results the fit did not see: held-out results (cross-validation, with a 1PL-type model and the benchmark mean as baselines) and cold start for a new benchmark (one benchmark left out at a time, parameters from a few results). Writes `docs/validointi.md`. Options: `--folds`, `--n-cal`, `--no-cold-start`. Takes about 2 min, about 20 s without the cold start. |
| `backtest-vintage` | Point-in-time backtest. The IRT model is refitted for every origin using only the results that were in Epoch's data package on that day (archived snapshots from the Internet Archive, downloaded to `data/raw/epoch_ai_archive/`), and the result is compared with the ordinary backtest. Writes `docs/backtest_ajankohta.md`. Options: `--origins auto` (default: the snapshot dates are the origins) or `3,6,9` (months back; the snapshot can then be weeks old), `--no-fetch`, `--n-boot`. |
| `dashboard` | Streamlit app with six pages. Requires a `fit` run. Opens the database read-only and shows update alerts at the top of every page. Options: `--port`, `--address`. |
| `update` | The full pipeline in one command: fetch, import, IRT (overall and per domain), forecasts and health checks. Alerts on failed fetches or imports, new unrecognised model names and non-converging fits. Exit code 1 on a critical failure. Logs: `data/logs/`. |

## Automatic updates

`forecaster update` runs weekly on your own machine via Windows Task Scheduler:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\schedule_update.ps1                          # Mondays 07:00
powershell -ExecutionPolicy Bypass -File scripts\schedule_update.ps1 -Day Sunday -Time 20:00  # custom time
powershell -ExecutionPolicy Bypass -File scripts\schedule_update.ps1 -Remove                  # remove the schedule
```

- **How it runs:** the task runs hidden, as the current user, while that user is logged in. If the machine is off at the scheduled time, the task runs at the next start-up.
- **Where to see results:** results and alerts appear at the top of the dashboard and on its data quality page. The dashboard warns when the last successful update is more than 10 days old.
- **Other operating systems:** on Linux/macOS you can schedule the same command with cron. That setup is not included or tested.

The update runs locally rather than in GitHub Actions because the database and dashboard are local, and unlicensed price data must not be stored in a public repository. GitHub Actions only runs lint and tests (`.github/workflows/ci.yml`).

## Private remote access to the dashboard

By default the dashboard listens only on the local machine (`127.0.0.1:8501`). To reach it from your own phone or laptop without exposing it to the internet, use [Tailscale](https://tailscale.com/):

1. Install Tailscale on this machine and on your other devices, and sign in with the same account.
2. Start the dashboard: `forecaster dashboard`
3. Share it on your tailnet: `tailscale serve --bg 8501`
4. On another device, open `https://<machine-name>.<tailnet>.ts.net`. The exact address is shown by `tailscale serve status`.

To stop, press Ctrl+C in the dashboard window and run `tailscale serve reset`. A running dashboard uses about 0.5 GB of memory and no CPU while idle. Work happens only when pages load: the first overview load takes about 13 s, and a simulator refit about 5 s.

## Data and licences

The code is MIT-licensed (see [LICENSE](LICENSE)). The repository contains no downloaded data. Data is fetched with `forecaster ingest all` (or `update`) into `data/`, which is not version-controlled.

| Source | Used for | Licence / terms |
|---|---|---|
| [Epoch AI](https://epoch.ai/data) | benchmark results, model metadata | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The diagnostic reports (`docs/irt_diagnostiikka.md`, `docs/datakokeilu_epoch_ai.md`, `docs/validointi.md`) are derived from this data (normalised, aggregated, IRT-fitted), and each one carries an attribution at the top. |
| [Internet Archive](https://web.archive.org/) | earlier versions of Epoch's data package (`backtest-vintage`) | The same data as above (Epoch AI, CC BY 4.0). The archive is used only to tell which results were published on a given day. `docs/backtest_ajankohta.md` is derived from this data and carries an attribution at the top. |
| [OpenRouter](https://openrouter.ai/) | current prices (API) | [Terms of service](https://openrouter.ai/terms). The terms prohibit automated collection and copying of information and transferring the materials to others (sections 7 and 12). Collected prices are therefore not published and stay in the local database. The terms do not explicitly address derived statistics. |
| [simonw/llm-prices](https://github.com/simonw/llm-prices) | price history | **No licence**, so local use only. For this reason `docs/ennusteet.md` is generated locally and kept out of the repository, and the dashboard is not published online with this data. |
| `config/manual_prices.yaml` | launch prices of older model versions | Compiled by hand from providers' official pricing pages, with the source noted on each entry. |

**Third-party files:** `.claude/skills/developing-with-streamlit/` is Streamlit's agent guide (Streamlit Inc. / Snowflake Inc., Apache License 2.0). The files are unmodified, and the licence text is in the same folder (`LICENSE`).

**Test data:** the tests use only made-up data (`tests/fixtures/`) and no network access.

## Project layout

```text
config/            benchmark definitions, anchors, forecast settings, manual prices
data/raw/          downloaded raw data (not version-controlled)
src/forecaster/
  config.py        paths and YAML validation
  cli.py           command line
  db/              DuckDB schema and queries
  ingest/          data source adapters
  models/          IRT engine, quality checks, forecasts
  dashboard/       dashboard data functions, charts and caching
  update.py        automated update and alerts
app/               Streamlit: dashboard.py (navigation) + pages/ (six pages)
scripts/           schedule_update.ps1 (scheduling), fix_editable_pth.py, data exploration
docs/              methodology, data source guide, diagnostic reports
tests/
```

## Limitations in brief

- The frontier has only about 20 points, so prediction intervals are wide. The median (P50) is the centre of a distribution, not a prediction.
- Calibration is checked with a rolling backtest from four origins. In the 2026-11-18 run, coverage was 0.93 ± 0.07 (target 0.80). With only four effectively independent origins, the data cannot tell whether the intervals are well calibrated, too wide or too narrow. The November 2024 origin underestimated later progress (5 of 7 points inside the interval). Fit windows take an earlier change in pace into account, but no method anticipates changes after the forecast date. This backtest uses current θ values and therefore looks into the future. In the point-in-time backtest (`forecaster backtest-vintage`, 10 origins 2025-11 … 2026-09), coverage was 0.84 (0.89 over all points; 0.71 / 0.78 when corrected for the scale's level shift), but the realised points come from only 10 different models, so the data cannot tell whether the intervals are slightly narrow or slightly wide. Tracking of genuinely forward-looking forecasts started on 2026-11-19.
- θ is a relative scale (GPT-4 June 2023 = 0, GPT-5 = 3.5). The report and dashboard translate the forecast into expected results on reference benchmarks (HLE, Remote Labor Index, FrontierMath Tier 4, WeirdML v3) and show the measurability ceiling: above it well-measured benchmarks discriminate only weakly, so checking the forecast with current benchmarks is uncertain. The ceiling is a convention and depends on a single benchmark. On 2026-11-25 the ceiling was θ 7.7. The 18- and 24-month forecasts' P50 exceeded it, and the 12-month P50 was practically at the ceiling.
- Models end up on the frontier partly because their measurement error happened to be positive. The forecast is nearly unbiased for observed records but biased upwards relative to true capability, so the report and dashboard also show a bias-corrected forecast.
- Prices are the cheapest listed prices. The price actually paid can be several times higher when requests are routed to more expensive providers, so the cost forecasts describe the best available price, not the typical one.
- Outlier filtering only catches sharp, isolated contamination. Broad, even contamination is not detected.
- IRT assumes a single capability dimension. The per-domain fits give a domain view but share the overall benchmark parameters.

See [docs/metodologia.en.md](docs/metodologia.en.md) for details.
