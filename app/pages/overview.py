"""Sivu 1: θ ajan funktiona, Pareto-rintama ja ennustekaista. Domain-valinta."""

from datetime import date

import polars as pl
import streamlit as st

from forecaster.dashboard import charts, data, ui
from forecaster.models.forecaster import add_months

st.title("Yleiskuva")
token = ui.require_db()
all_runs = ui.runs(token)
if "overall" not in all_runs:
    ui.overall_run(token)

domains = ["overall", *sorted(d for d in all_runs if d != "overall")]
c1, c2 = st.columns([1, 3])
domain = c1.selectbox(
    "Osa-alue", domains, format_func=lambda d: "kokonaiskyvykkyys" if d == "overall" else d
)
run_id = all_runs[domain]
scores = data.with_frontier(ui.models(token, run_id, domain))
as_of = date.today()
result = ui.forecast_band(token, run_id, domain, as_of)
band, fc = result if result else (None, None)

if fc:
    h12 = next((h for h in fc["horizons"] if h["months"] == 12), fc["horizons"][0])
    p = h12["prediction_p10_p50_p90"]
    grid = ui.metrics_grid()
    grid.metric("Rintama nyt", f"θ {fc['current_frontier_theta']:.2f}")
    grid.metric(
        "Kehitysvauhti",
        f"{fc['slope_per_year']:.2f} θ/v",
        help=f"Malli: {fc['model']}. Bootstrap P10–P90 "
        f"{fc['slope_per_year_p10_p50_p90'][0]:.2f}–{fc['slope_per_year_p10_p50_p90'][2]:.2f}",
    )
    grid.metric(
        f"+{h12['months']} kk (P50)", f"θ {p[1]:.2f}", help=f"P10–P90 {p[0]:.2f}–{p[2]:.2f}"
    )
    grid.metric("Rintamapisteitä", fc["n_frontier"])
else:
    st.info("Tälle osa-alueelle ei voi laskea ennustetta: rintamassa on liian vähän pisteitä.")

full_history = st.toggle("Näytä koko historia", key="overview_full_history")
x_start = None if full_history else add_months(ui.forecast_file().fit_start, -6)
ui.chart(
    charts.overview(scores, band, as_of, x_start=x_start),
    alt="Mallien kyvykkyys julkaisupäivän mukaan, rintama ja ennustekaista",
)
st.caption(
    "Siniset pisteet = Pareto-rintama (malli ylitti kaikki aiemmin julkaistut, A11), virhepalkki "
    "± SE. Tummempi kaista = trendin epävarmuus, vaaleampi katkoviivareunainen = väli "
    "yksittäiselle tulevalle rintamamallille. Napauta pistettä nähdäksesi mallin. "
    "Ennusteesta on jätetty pois mallit, joiden θ-SE > "
    f"{ui.forecast_file().max_theta_se} (A18)."
)
with st.expander("Rintamamallit taulukkona"):
    ui.table(
        scores.filter(pl.col("is_frontier")).select(
            "display_name", "provider", "release_date", "theta", "theta_se"
        ),
        ["display_name", "release_date", "theta"],
        "frontier",
        alt="Pareto-rintaman mallit",
        column_config={
            "display_name": "Malli",
            "provider": "Tarjoaja",
            "release_date": "Julkaistu",
            "theta": st.column_config.NumberColumn("θ", format="%.2f"),
            "theta_se": st.column_config.NumberColumn("SE", format="%.2f"),
        },
    )
ui.license_note()
