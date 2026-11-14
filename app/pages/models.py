"""Sivu 2: mallien θ ja SE; mallikohtainen havaittu vs. ennustettu benchmarkeittain."""

import polars as pl
import streamlit as st

from forecaster.dashboard import charts, data, ui

st.title("Mallit")
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
st.dataframe(
    shown.select(
        "display_name",
        "provider",
        "release_date",
        "theta",
        "theta_se",
        "n_benchmarks",
        "is_open_weights",
    ),
    hide_index=True,
    width="stretch",
    height=360,
    column_config={
        "display_name": "Malli",
        "provider": "Tarjoaja",
        "release_date": "Julkaistu",
        "theta": st.column_config.NumberColumn("θ", format="%.2f"),
        "theta_se": st.column_config.NumberColumn("SE", format="%.2f"),
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
c1, c2, c3 = st.columns(3)
c1.metric("θ", f"{row['theta']:.2f}", help=f"SE {row['theta_se']:.2f}" if row["theta_se"] else None)
c2.metric("Havaittuja benchmarkeja", int(detail["y_obs"].is_not_null().sum()))
rmse = detail.filter(pl.col("residual").is_not_null() & ~pl.col("is_outlier"))["residual"]
c3.metric("Jäännösten RMSE", f"{(rmse.pow(2).mean() ** 0.5):.3f}" if rmse.len() else "–")
st.plotly_chart(
    charts.model_detail(detail, f"{pick}: havaittu ja ennustettu tulos"), width="stretch"
)
st.caption(
    "Benchmarkit vaikeusjärjestyksessä (b). Ennuste = c + (1 − c)·σ(a(θ − b)). "
    "Ennuste näytetään myös benchmarkeille, joilla mallia ei ole testattu."
)
with st.expander("Taulukko"):
    st.dataframe(
        detail.select("name", "domain", "y_obs", "y_pred", "residual", "is_outlier"),
        hide_index=True,
        width="stretch",
    )
ui.license_note()
