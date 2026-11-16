"""Sivu 3: benchmarkien a, b, saturaatio, kattavuus ja liputetut poikkeamat."""

import polars as pl
import streamlit as st

from forecaster.dashboard import charts, data, ui

st.title("Benchmarkit")
token = ui.require_db()
run_id = ui.overall_run(token)
models = ui.models(token, run_id, "overall")
theta_max = float(models["theta"].max())
params = data.with_saturation(ui.benchmark_params(token, run_id), theta_max)

domains = sorted(params["domain"].unique().to_list())
sel = st.multiselect("Osa-alueet", domains, default=domains)
p = params.filter(pl.col("domain").is_in(sel))

grid = ui.metrics_grid()
grid.metric("Benchmarkeja", p.height)
grid.metric(
    "Saturoituneita (≥ 0.9)",
    int((p["saturation"] >= 0.9).sum()),
    help="Saturaatio = σ(a(θ_max − b)): parhaan mallin odotettu osuus arvausrajan "
    "yläpuolisesta vaihteluvälistä.",
)
grid.metric("Poikkeamia poistettu", int(p["n_outliers"].sum()))

ui.chart(charts.benchmarks(p), alt="Benchmarkien vaikeus ja erottelukyky")
ui.table(
    p.select(
        "name",
        "domain",
        "difficulty_b",
        "discrimination_a",
        "guessing_floor",
        "saturation",
        "n_models",
        "n_outliers",
        "contamination_risk",
        "weight",
    ),
    ["name", "difficulty_b", "saturation"],
    "benchmarks",
    alt="Benchmarkien IRT-parametrit ja saturaatio",
    column_config={
        "name": "Benchmark",
        "domain": "Osa-alue",
        "difficulty_b": st.column_config.NumberColumn("b", format="%.2f"),
        "discrimination_a": st.column_config.NumberColumn("a", format="%.2f"),
        "guessing_floor": st.column_config.NumberColumn("c (arvausraja)", format="%.2f"),
        "saturation": st.column_config.ProgressColumn(
            "Saturaatio", min_value=0, max_value=1, format="%.2f"
        ),
        "n_models": "Malleja",
        "n_outliers": "Poikkeamia",
        "contamination_risk": "Kontaminaatioriski",
        "weight": "Paino",
    },
)

st.subheader("Liputetut poikkeamat")
out = ui.outliers(token)
if out.is_empty():
    st.write("Ei poistettuja poikkeamia.")
else:
    names = dict(zip(models["model_id"], models["display_name"], strict=True))
    bnames = dict(zip(params["benchmark_id"], params["name"], strict=True))
    st.dataframe(
        out.with_columns(
            malli=pl.col("model_id").replace_strict(names, default=pl.col("model_id")),
            benchmark=pl.col("benchmark_id").replace_strict(bnames, default=pl.col("benchmark_id")),
        ).select("malli", "benchmark", "y"),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Epäilyttävän hyvät tulokset (robusti z > 3, A4), poistettu sovituksesta – "
        "mahdollinen kontaminaatio."
    )
ui.license_note()
