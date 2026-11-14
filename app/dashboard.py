"""Streamlit-dashboard (K6). Käynnistys: `forecaster dashboard`.

Sivut ovat kansiossa app/pages/. Laskenta ja kuvaajat ovat paketissa forecaster.dashboard.
"""

import os

# Polars 2.1:n säiepooli + Torchin/BLASin OpenMP Streamlitin skriptisäikeissä kaatoi prosessin
# Windowsissa (access violation, toistuva sivujärjestyksellä yleiskuva → mallit → benchmarkit →
# kustannukset). Polarsin data on dashboardissa pientä, joten yksi säie ei hidasta.
# Asetettava ennen kuin polars tuodaan ensimmäisen kerran.
os.environ.setdefault("POLARS_MAX_THREADS", "1")

from pathlib import Path  # noqa: E402

import streamlit as st  # noqa: E402

st.set_page_config(page_title="AI Benchmark Forecaster", layout="wide")

PAGES = Path(__file__).parent / "pages"
nav = st.navigation(
    [
        st.Page(
            PAGES / "overview.py", title="Yleiskuva", icon=":material/show_chart:", default=True
        ),
        st.Page(PAGES / "models.py", title="Mallit", icon=":material/smart_toy:"),
        st.Page(PAGES / "benchmarks.py", title="Benchmarkit", icon=":material/quiz:"),
        st.Page(PAGES / "costs.py", title="Kustannukset", icon=":material/payments:"),
        st.Page(PAGES / "simulator.py", title="Simulaattori", icon=":material/tune:"),
        st.Page(PAGES / "data_quality.py", title="Datan laatu", icon=":material/fact_check:"),
    ]
)
nav.run()
