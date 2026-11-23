"""Sivu 1: θ ajan funktiona, Pareto-rintama ja ennustekaista. Domain-valinta."""

from datetime import date

import polars as pl
import streamlit as st

from forecaster.dashboard import charts, data, ui
from forecaster.dashboard.guide import term
from forecaster.models.forecaster import add_months

st.title("Yleiskuva")
ui.page_guide("overview")
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
    grid.metric(
        "Rintama nyt",
        f"θ {fc['current_frontier_theta']:.2f}",
        help=f"Paras tähän mennessä julkaistu malli. {term('theta')}",
    )
    grid.metric(
        "Kehitysvauhti",
        f"{fc['slope_per_year']:.2f} θ/v",
        help=f"{term('slope')} P10–P90 "
        f"{fc['slope_per_year_p10_p50_p90'][0]:.2f}–{fc['slope_per_year_p10_p50_p90'][2]:.2f} "
        "(synteettisesti tasaisessa kehityksessä hieman liian leveä, noin 0.9 nimellisen 0.8:n "
        "sijaan; johtuu sovitusikkunoista A28, ja vauhdin muuttuessa kattavuutta ei ole "
        "mitattu). "
        f"Koko jakson sovituksen ({fc['model']}) nykyinen kulmakerroin: "
        f"{fc['slope_per_year_full_period']:.2f} θ/v.",
    )
    grid.metric(
        f"+{h12['months']} kk (P50)",
        f"θ {p[1]:.2f}",
        help=f"{term('p50_interval')} Tällä horisontilla P10–P90 {p[0]:.2f}–{p[2]:.2f}.",
    )
    grid.metric("Rintamapisteitä", fc["n_frontier"], help=term("n_frontier"))
    sb = fc.get("selection_bias") or {}
    if sb.get("status") == "ok":
        b12 = sb["horizon_bias"].get(h12["months"])
        st.caption(
            f"Todellisen kyvykkyyden ennuste (+{h12['months']} kk, rintamavalinnan harha "
            f"korjattu, A31): P50 θ {p[1] - b12:.2f} (harha {b12:+.2f} θ). Nykyrintaman "
            f"huipun harha {sb['current_frontier_bias']:+.2f} θ.",
            help=term("selection_bias"),
        )
else:
    st.info("Tälle osa-alueelle ei voi laskea ennustetta: rintamassa on liian vähän pisteitä.")

first = scores["release_date"].min()
last = band["date"].max() if band is not None and band.height else as_of
default_start = max(first, add_months(ui.forecast_file().fit_start, -6))
x_start, x_end = st.slider(
    "Aikaväli",
    min_value=first,
    max_value=last,
    value=(default_start, last),
    format="YYYY-MM",
    key="overview_range",
    help="Rajaa kaavion julkaisupäivien mukaan. Kapea väli erottaa tiheän kärjen pisteet.",
)
ui.chart(
    charts.overview(scores, band, as_of, x_start=x_start, x_end=x_end),
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
            "theta": st.column_config.NumberColumn("θ", format="%.2f", help=term("theta")),
            "theta_se": st.column_config.NumberColumn("SE", format="%.2f", help=term("theta_se")),
        },
    )

st.subheader("Aiempien ennusteiden toteutuminen", help=term("prospective"))
pc = ui.latest_forecasts(token).get("prospective") if domain == "overall" else None
if domain != "overall":
    st.caption("Seuranta tehdään vain kokonaiskyvykkyyden ennusteelle.")
elif not pc or not pc.get("n_pairs"):
    n = pc.get("n_forecasts", 0) if pc else 0
    st.caption(
        f"Tallennettuja viikkoennusteita {n}. Vertailu alkaa kertyä, kun niiden jälkeen "
        "julkaistaan uusia rintamamalleja (seuranta alkoi 2026-11-19)."
    )
else:
    g2 = ui.metrics_grid()
    g2.metric("Ennusteita", pc["n_forecasts"], help="Kunkin ISO-viikon uusin tallennettu ennuste.")
    g2.metric(
        "Rintamapisteitä",
        pc["n_unique_points"],
        help="Uniikit toteutuneet rintamamallit. Tehokas otoskoko on enintään tämä.",
    )
    g2.metric(
        "Välin sisällä",
        f"{pc['point_balanced_share_inside_p10_p90']:.2f}",
        help="Osuus P10–P90-välille osuneista, pistekohtaisesti tasapainotettuna. Odotus ≈ 0.80. "
        f"Kaikista vertailupareista: {pc['pooled_share_inside_p10_p90']:.2f}.",
    )
    g2.metric(
        "Keskivirhe",
        f"{pc['mean_error_shift_adjusted']:+.2f} θ",
        help="Toteuma − P50, θ-asteikon siirtymällä korjattuna. Positiivinen = kehitys oli "
        f"ennustettua nopeampaa. Korjaamaton: {pc['mean_error']:+.2f} θ.",
    )
    st.caption(
        "Peräkkäiset ennusteet arvioivat samoja pisteitä, joten luvut ovat pitkään hyvin "
        "epävarmoja."
    )
ui.license_note()
