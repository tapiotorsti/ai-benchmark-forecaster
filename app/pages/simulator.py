"""Sivu 5: benchmarkien painot ja kontaminaatiosuodatus → IRT uudelleen (välimuisti painojen
hashilla). Tallennettuun ajoon ei kosketa: tulos on vain tässä näkymässä."""

import time

import polars as pl
import streamlit as st

from forecaster.dashboard import charts, data, ui

st.title("Simulaattori")
st.write(
    "Säädä benchmarkien painoja tai jätä riskialttiit testit pois ja sovita IRT uudelleen. "
    "Tulosta verrataan tallennettuun ajoon. Tietokantaan ei kirjoiteta."
)
token = ui.require_db()
run_id = ui.overall_run(token)
models = ui.models(token, run_id, "overall")
params = ui.benchmark_params(token, run_id)

c1, c2 = st.columns(2)
drop = c1.multiselect(
    "Jätä pois kontaminaatioriskiltään",
    ["high", "medium", "low"],
    format_func={"high": "korkea", "medium": "keskitaso", "low": "matala"}.get,
)
excl = c2.checkbox("Poista liputetut poikkeamat (kuten tuotantoajo)", value=True)

st.markdown("**Painot** (0 = benchmark pois; oletus konfiguraatiosta)")
edited = st.data_editor(
    params.select("benchmark_id", "name", "domain", "contamination_risk", "weight"),
    hide_index=True,
    width="stretch",
    height=300,
    disabled=["benchmark_id", "name", "domain", "contamination_risk"],
    column_config={
        "weight": st.column_config.NumberColumn("Paino", min_value=0.0, max_value=10.0, step=0.1)
    },
)
weights = {b: float(w) for b, w in zip(edited["benchmark_id"], edited["weight"], strict=True)}
defaults = dict(params.select("benchmark_id", "weight").iter_rows())
changed = {b: w for b, w in weights.items() if abs(w - defaults[b]) > 1e-9}
settings = data.SimSettings(
    weights=changed, drop_contamination=frozenset(drop), exclude_outliers=excl
)

if not st.button("Sovita uudelleen", type="primary"):
    st.caption(
        f"{len(changed)} painoa muutettu, {len(drop)} riskitasoa pois. "
        "Sovitus kestää noin 10–20 s; sama säätö haetaan välimuistista heti."
    )
    st.stop()

t0 = time.perf_counter()
with st.spinner("Sovitetaan IRT:tä…"):
    try:
        res, n_obs = ui.simulate(token, settings.key(), settings)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
elapsed = time.perf_counter() - t0

cmp = data.compare_runs(models.select("model_id", "display_name", "release_date", "theta"), res)
fit_start = ui.forecast_file().fit_start
base_slope = data.frontier_slope(models, fit_start)
sim_scores = cmp.filter(pl.col("theta_sim").is_not_null()).select(
    "model_id", "release_date", theta="theta_sim"
)
sim_slope = data.frontier_slope(sim_scores, fit_start)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Sovitusaika", f"{elapsed:.1f} s", help="Välimuistista < 1 s")
m2.metric("Havaintoja", n_obs)
m3.metric(
    "Rintaman kulmakerroin",
    f"{sim_slope:.2f} θ/v" if sim_slope else "–",
    delta=f"{sim_slope - base_slope:+.2f}" if sim_slope and base_slope else None,
    help="Lineaarinen piste-estimaatti, vertailukohtana tallennettu ajo",
)
m4.metric("Konvergoi", "kyllä" if res.converged else "EI")

st.plotly_chart(charts.sim_delta(cmp), width="stretch")
st.dataframe(
    cmp.select(
        "display_name", "theta", "theta_sim", "delta", "rank_base", "rank_sim", "rank_change"
    ),
    hide_index=True,
    width="stretch",
    column_config={
        "display_name": "Malli",
        "theta": st.column_config.NumberColumn("θ tallennettu", format="%.2f"),
        "theta_sim": st.column_config.NumberColumn("θ simuloitu", format="%.2f"),
        "delta": st.column_config.NumberColumn("Δθ", format="%+.2f"),
        "rank_base": "Sija ennen",
        "rank_sim": "Sija nyt",
        "rank_change": "Muutos",
    },
)
st.caption(
    "Ankkurit (GPT-4 kesäkuu 2023 = 0, GPT-5 = 3.5) pidetään kiinteinä, joten asteikot ovat "
    "vertailukelpoisia. Simulaatio ei laske keskivirheitä."
)
