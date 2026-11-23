"""Sivu 2: mallien θ ja SE; mallikohtainen havaittu vs. ennustettu benchmarkeittain."""

import polars as pl
import streamlit as st

from forecaster.dashboard import charts, data, ui
from forecaster.dashboard.guide import term

st.title("Mallit")
ui.page_guide("models")
token = ui.require_db()
run_id = ui.overall_run(token)
models = ui.models(token, run_id, "overall")

q = st.text_input("Hae mallia", placeholder="esim. claude, gpt-5, llama")
shown = models
if q:
    shown = models.filter(
        pl.col("display_name").str.to_lowercase().str.contains(q.lower(), literal=True)
        | pl.col("provider").str.to_lowercase().str.contains(q.lower(), literal=True)
    )
ui.table(
    shown.select(
        "display_name",
        "provider",
        "release_date",
        "theta",
        "theta_se",
        "n_benchmarks",
        "is_open_weights",
    ),
    ["display_name", "theta", "theta_se"],
    "models",
    alt="Mallien kyvykkyys ja keskivirhe",
    height=360,
    column_config={
        "display_name": "Malli",
        "provider": "Tarjoaja",
        "release_date": "Julkaistu",
        "theta": st.column_config.NumberColumn("θ", format="%.2f", help=term("theta")),
        "theta_se": st.column_config.NumberColumn("SE", format="%.2f", help=term("theta_se")),
        "n_benchmarks": "Benchmarkeja",
        "is_open_weights": "Avoimet painot",
    },
)
st.caption(f"{shown.height} / {models.height} mallia, järjestetty θ:n mukaan.")

st.subheader("Mallikohtainen näkymä")
names = dict(zip(models["display_name"], models["model_id"], strict=True))
default = shown["display_name"][0] if shown.height else models["display_name"][0]
pick = st.selectbox("Malli", list(names), index=list(names).index(default))
mid = names[pick]
row = models.filter(pl.col("model_id") == mid).row(0, named=True)
detail = data.model_detail(
    mid,
    row["theta"],
    ui.benchmark_params(token, run_id),
    ui.observations(token, exclude_outliers=False),
    ui.outliers(token),
)
grid = ui.metrics_grid()
se_text = f"SE {row['theta_se']:.2f}. " if row["theta_se"] else ""
grid.metric("θ", f"{row['theta']:.2f}", help=se_text + term("theta"))
grid.metric("Havaittuja benchmarkeja", int(detail["y_obs"].is_not_null().sum()))
rmse = detail.filter(pl.col("residual").is_not_null() & ~pl.col("is_outlier"))["residual"]
grid.metric(
    "Jäännösten RMSE",
    f"{(rmse.pow(2).mean() ** 0.5):.3f}" if rmse.len() else "–",
    help=term("rmse"),
)
ui.chart(
    charts.model_detail(detail, f"{pick}: havaittu ja ennustettu tulos"),
    alt="Valitun mallin havaittu ja ennustettu tulos benchmarkeittain",
)
st.caption(
    "Benchmarkit vaikeusjärjestyksessä (b). Ennuste = c + (1 − c)·σ(a(θ − b)). "
    "Ennuste näytetään myös benchmarkeille, joilla mallia ei ole testattu."
)
with st.expander("Taulukko"):
    ui.table(
        detail.select("name", "domain", "y_obs", "y_pred", "residual", "is_outlier"),
        ["name", "y_obs", "y_pred"],
        "model_detail",
        alt="Valitun mallin havaitut ja ennustetut tulokset benchmarkeittain",
        column_config={
            "name": "Benchmark",
            "domain": "Osa-alue",
            "y_obs": st.column_config.NumberColumn("Havaittu", format="%.2f"),
            "y_pred": st.column_config.NumberColumn("Ennuste", format="%.2f"),
            "residual": st.column_config.NumberColumn(
                "Jäännös", format="%+.2f", help=term("residual")
            ),
            "is_outlier": st.column_config.CheckboxColumn("Poikkeama", help=term("outliers")),
        },
    )
ui.license_note()
