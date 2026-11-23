# Adding a data source

[Suomeksi](uusi_datalahde.md) · **English**

How to bring in a new benchmark or price source. The existing adapters `src/forecaster/ingest/openrouter.py` (prices, JSON) and `epoch_ai.py` (results, ZIP + CSV) serve as examples. Claude Code users can run the same workflow with the skill `/add-data-source <name>`.

## 1. Check first: licence and format

- **Licence and terms of use.** The repository is public (MIT). A source may only be used if its terms allow automated fetching. If it has no licence, the data is for local use only, like simonw/llm-prices. Outputs derived from it must then not be committed, and they are added to `.gitignore`.
- **Format and fields:** model name, benchmark, score and its scale, release date, and for prices the input and output price and the date from which they apply.
- **Test data:** save a small test dataset in `tests/fixtures/<source>/`. It must be **made up** or allowed by the licence, not a copy of an unlicensed source.

## 2. Adapter `src/forecaster/ingest/<source>.py`

The adapter has two functions:

```python
SOURCE = "<source>"

def fetch(paths: Paths, as_of: date, client=None) -> Path:
    """Download the raw file to data/raw/<source>/ (not again on the same day)."""

def ingest(repo: Repository, paths: Paths, payload: Path, ...) -> IngestReport:
    def work(run_id: str, report: IngestReport) -> None:
        ...  # parsing, validation (Pydantic row models in db/models.py), upserts
    return run_ingest(repo, SOURCE, work)
```

`run_ingest` (`ingest/base.py`) handles the `ingest_runs` log, storing unknown names and recording errors. The adapter is also responsible for the following:

- **Model names → `model_id`:** names are mapped to canonical identifiers through the alias table. Unknown names are added to `report.unknown_aliases` *as plain identifiers*, and any suggestion goes into `report.alias_suggestions`. Names are never dropped silently.
- **Incomplete rows:** rows missing required information go to quarantine (`repo.quarantine`).
- **SQL:** all SQL goes in `db/repository.py`.
- **API keys:** keys are read from environment variables and never written into code.

## 3. Configuration

- **New benchmarks:** add them to `config/benchmarks.yaml` (domain, raw_min/raw_max, guessing_floor on the normalised scale, contamination_risk and weight). Justify the guessing floor.
- **Manual name mapping:** add mappings to `config/model_aliases.yaml`. For price sources (OpenRouter, llm-prices), the command `forecaster aliases` suggests candidates, which are checked by hand. For a new price source, it has to be extended to read the source's raw file (`ingest/alias_candidates.py`).

## 4. Wiring

- **CLI:** add the source to `forecaster ingest <source>` and, if needed, to the `all` group (`cli.py`).
- **Weekly update:** add a `Source` to `DEFAULT_SOURCES` in `src/forecaster/update.py`. Order matters: a price source that builds a backfill on top of the others comes last.
- **README:** add a row to the licence table in both `README.md` (section "Data ja lisenssit") and `README.en.md` (section "Data and licences").

## 5. Tests (no network)

- Parsing the test data gives the expected number of rows and values.
- Running the import twice creates no duplicates.
- An unknown name and a missing date end up in the report or in quarantine.

Finally, run `ruff check .`, `ruff format --check .` and `pytest`.
