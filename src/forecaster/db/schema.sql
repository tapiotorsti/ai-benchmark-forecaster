-- AI Benchmark Aggregator & Forecaster – DuckDB-skeema.
-- Pohja: määrittely 3.2 + TOTEUTUSSUUNNITELMA.md aukot A1, A2, A8, A9.
-- Kaikki lauseet ovat idempotentteja (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 1. Mallit. Yksikkö on "malliryhmä" (A9), esim. 'Claude 3.5 Sonnet (October 2024)'.
--    Hinnat EIVÄT ole täällä vaan model_prices-historiassa (A1).
CREATE TABLE IF NOT EXISTS models (
    model_id VARCHAR PRIMARY KEY,
    display_name VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    release_date DATE NOT NULL,
    is_open_weights BOOLEAN DEFAULT FALSE,
    estimated_compute_flops DOUBLE,
    is_anchor_model BOOLEAN DEFAULT FALSE
);

-- 2. Lähdekohtaiset nimet → kanoninen model_id (A2).
CREATE TABLE IF NOT EXISTS model_aliases (
    source_name VARCHAR NOT NULL,
    alias VARCHAR NOT NULL,
    model_id VARCHAR NOT NULL REFERENCES models(model_id),
    PRIMARY KEY (source_name, alias)
);

-- 3. Benchmarkit (synkronoidaan config/benchmarks.yaml:sta).
CREATE TABLE IF NOT EXISTS benchmarks (
    benchmark_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    domain VARCHAR NOT NULL,
    metric_type VARCHAR NOT NULL,
    raw_min DOUBLE NOT NULL DEFAULT 0.0,
    raw_max DOUBLE NOT NULL DEFAULT 100.0,
    guessing_floor DOUBLE NOT NULL DEFAULT 0.0,
    contamination_risk VARCHAR,
    weight DOUBLE NOT NULL DEFAULT 1.0,
    release_date DATE,
    CHECK (raw_max > raw_min),
    CHECK (guessing_floor >= 0 AND guessing_floor < 1),
    CHECK (weight > 0)
);

-- 4. Yksittäiset tulokset. model_version säilyttää versiotason (A9);
--    '' = lähde ei erottele versioita.
CREATE TABLE IF NOT EXISTS evaluations (
    eval_id VARCHAR PRIMARY KEY,
    model_id VARCHAR NOT NULL REFERENCES models(model_id),
    benchmark_id VARCHAR NOT NULL REFERENCES benchmarks(benchmark_id),
    model_version VARCHAR NOT NULL DEFAULT '',
    raw_score DOUBLE NOT NULL,
    normalized_score DOUBLE NOT NULL,
    eval_date DATE,
    source_name VARCHAR NOT NULL,
    source_url VARCHAR,
    is_outlier BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (model_id, benchmark_id, source_name, model_version),
    CHECK (normalized_score > 0 AND normalized_score < 1)
);

-- 5. Hintahistoria (A1). blended = (3·input + output) / 4.
CREATE TABLE IF NOT EXISTS model_prices (
    model_id VARCHAR NOT NULL REFERENCES models(model_id),
    valid_from DATE NOT NULL,
    source_name VARCHAR NOT NULL,
    input_cost_per_1m DOUBLE NOT NULL,
    output_cost_per_1m DOUBLE NOT NULL,
    blended_cost_per_1m DOUBLE NOT NULL,
    PRIMARY KEY (model_id, valid_from, source_name),
    CHECK (input_cost_per_1m >= 0 AND output_cost_per_1m >= 0)
);

-- 6. IRT-ajojen metatiedot.
CREATE TABLE IF NOT EXISTS irt_runs (
    run_id VARCHAR PRIMARY KEY,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    domain VARCHAR NOT NULL,
    anchor_mode VARCHAR,
    n_models INTEGER,
    n_benchmarks INTEGER,
    n_observations INTEGER,
    final_loss DOUBLE,
    converged BOOLEAN,
    params_json VARCHAR
);

-- 7. Kyvykkyysindeksit.
CREATE TABLE IF NOT EXISTS capability_scores (
    run_id VARCHAR NOT NULL REFERENCES irt_runs(run_id),
    model_id VARCHAR NOT NULL REFERENCES models(model_id),
    domain VARCHAR NOT NULL,
    theta DOUBLE NOT NULL,
    theta_se DOUBLE,
    calculated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, model_id, domain)
);

-- 8. Benchmarkien IRT-parametrit.
CREATE TABLE IF NOT EXISTS benchmark_irt_params (
    run_id VARCHAR NOT NULL REFERENCES irt_runs(run_id),
    benchmark_id VARCHAR NOT NULL REFERENCES benchmarks(benchmark_id),
    difficulty_b DOUBLE NOT NULL,
    discrimination_a DOUBLE NOT NULL,
    PRIMARY KEY (run_id, benchmark_id)
);

-- 9. Ingest-ajojen loki ja karanteeni (A3).
CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id VARCHAR PRIMARY KEY,
    source_name VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status VARCHAR NOT NULL,          -- running | ok | failed
    rows_fetched INTEGER,
    rows_upserted INTEGER,
    rows_quarantined INTEGER,
    message VARCHAR
);

CREATE TABLE IF NOT EXISTS ingest_quarantine (
    run_id VARCHAR NOT NULL REFERENCES ingest_runs(run_id),
    source_name VARCHAR NOT NULL,
    record_json VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 10. Ennusteajot (sisältö tarkennetaan K5:ssä).
CREATE TABLE IF NOT EXISTS forecast_runs (
    run_id VARCHAR PRIMARY KEY,
    irt_run_id VARCHAR REFERENCES irt_runs(run_id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    kind VARCHAR NOT NULL,            -- capability | cost
    params_json VARCHAR,
    result_json VARCHAR
);
