"""Sivu 4: iso-kyvykkyyskäyrä, θ_target-liukusäädin ja puoliintumisaika väleineen."""

from datetime import date

import polars as pl
import streamlit as st

from forecaster.dashboard import charts, ui
from forecaster.dashboard.guide import term

st.title("Kustannukset")
ui.page_guide("costs")
token = ui.require_db()
run_id = ui.overall_run(token)
models = ui.models(token, run_id, "overall")
ff = ui.forecast_file()
theta_of = dict(zip(models["model_id"], models["theta"], strict=True))

presets = {
    f"{t.label} (θ {theta_of[t.model_id]:.2f})": theta_of[t.model_id]
    for t in ff.cost_targets
    if t.model_id in theta_of
}
c1, c2 = st.columns([1, 2])
preset = c1.selectbox("Viitetaso", ["(oma arvo)", *presets])
lo, hi = float(models["theta"].quantile(0.05)), float(models["theta"].max())
default = presets.get(preset, 0.0)
target = c2.slider(
    "θ_target: halvin malli, jonka θ ≥ tämä",
    min_value=round(lo, 1),
    max_value=round(hi, 1),
    value=float(round(default, 2)),
    step=0.05,
)

fc = ui.cost_view(token, run_id, round(target, 2), date.today())
if fc.get("status") == "insufficient":
    st.warning(f"Ei laskettavissa: {fc.get('note')}")
    st.stop()

status_badge = {
    "ok": ("Hinta laskee", "green", ":material/trending_down:"),
    "plateau": ("Lasku pysähtynyt", "orange", ":material/trending_flat:"),
    "not_declining": ("Hinta ei laske", "gray", ":material/trending_flat:"),
    "at_floor": ("Markkinan lattialla", "blue", ":material/vertical_align_bottom:"),
}
h = fc["halving_months_p10_p50_p90"]


def _hl(v):
    return "∞" if v is None else f"{v:.1f}"


# Tila merkkinä: pitkä teksti katkesi mittarin isolla fontilla ("Lasku p…", 2026-11-23)
label, color, icon = status_badge.get(fc["status"], (fc["status"], "gray", None))
st.badge(label, color=color, icon=icon, help=term("cost_status"))
grid = ui.metrics_grid()
grid.metric(
    "Puoliintumisaika nyt",
    f"{fc['halving_months']:.1f} kk" if fc["halving_months"] else "–",
    help=f"{term('halving')} Bootstrap P10–P90: {_hl(h[0])} – {_hl(h[2])} kk.",
)
grid.metric(
    "Historiallinen T½",
    f"{fc['historical_halving_months']:.1f} kk" if fc["historical_halving_months"] else "–",
    help=term("historical_halving"),
)
cur = fc.get("current_cheapest") or {}
grid.metric(
    "Halvin nyt ($/1M)",
    f"{cur.get('cost', float('nan')):.3g}",
    help=f"{cur.get('model_id', '–')}. {term('cheapest')}",
)
history_months = len(fc.get("monthly_cheapest") or [])
if history_months < 12:
    st.warning(
        f"Tasolla on hintoja vain {history_months} kk, joten puoliintumisaika ja ennuste ovat "
        "hyvin epävarmoja: lyhyt jakso ei kerro, jatkuuko lasku samaa vauhtia. Välit eivät "
        "sisällä tätä epävarmuutta.",
        title="Lyhyt hintahistoria",
    )

ui.chart(
    charts.cost(
        fc["monthly_df"],
        fc["horizons_df"],
        fc.get("market_floor"),
        f"Iso-kyvykkyyskäyrä, θ ≥ {target:.2f}",
    ),
    alt="Halvimman riittävän mallin hinta ajan funktiona ja ennuste",
)
notes = []
if fc["status"] == "at_floor":
    notes.append(
        "Taso on markkinan hintalattialla: hinta ei voi enää juuri laskea, joten "
        "kehitys näkyy kyvykkyyden nousuna samalla hinnalla."
    )
if fc.get("months_to_floor"):
    notes.append(f"Nykyvauhdilla lattia saavutetaan noin {fc['months_to_floor']:.0f} kk kuluttua.")
notes.append(
    f"Malli: {fc['model']} (palaisuus-/lattiatesti p = {fc['piecewise_p_value']:.3f})."
    if fc.get("piecewise_p_value") is not None
    else f"Malli: {fc['model']}."
)
st.caption(
    " ".join(notes) + " Hinta = (3 × input + output) / 4. Ennuste on rajattu väliin "
    "[markkinan lattia, nykyinen halvin] (A17)."
)

with st.expander("Hintaennätykset"):
    st.dataframe(pl.DataFrame(fc["record_lows"]), hide_index=True, width="stretch")
with st.expander("Ennuste horisonteittain"):
    st.dataframe(fc["horizons_df"], hide_index=True, width="stretch")
ui.license_note()
