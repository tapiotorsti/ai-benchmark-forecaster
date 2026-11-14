"""Sivu 6: ingest-ajojen loki, karanteeni ja tuntemattomat nimet."""

import polars as pl
import streamlit as st

from forecaster.dashboard import ui

st.title("Datan laatu")
token = ui.require_db()
runs, quarantine, unknown = ui.ingest_log(token)

failed = runs.filter(pl.col("status") == "failed").height
c1, c2, c3 = st.columns(3)
c1.metric("Ingest-ajoja (viim. 50)", runs.height)
c2.metric("Epäonnistuneita", failed)
c3.metric("Karanteenissa", quarantine.height)

st.subheader("Ingest-ajot")
st.dataframe(
    runs.select(
        "source_name",
        "started_at",
        "status",
        "rows_fetched",
        "rows_upserted",
        "rows_quarantined",
        "message",
    ),
    hide_index=True,
    width="stretch",
    column_config={
        "source_name": "Lähde",
        "started_at": "Aloitettu",
        "status": "Tila",
        "rows_fetched": "Haettu",
        "rows_upserted": "Tallennettu",
        "rows_quarantined": "Karanteeniin",
        "message": "Viesti",
    },
)

st.subheader("Tuntemattomat nimet (uusin ajo per lähde)")
if unknown.is_empty():
    st.write(
        "Ei tuntemattomia nimiä – tai ajot on tehty ennen skeemaversiota 2. "
        "Aja `forecaster ingest all`, niin nimet tallentuvat."
    )
else:
    src = st.selectbox("Lähde", sorted(unknown["source_name"].unique().to_list()))
    u = unknown.filter(pl.col("source_name") == src)
    st.dataframe(
        u.select("kind", "name"),
        hide_index=True,
        width="stretch",
        column_config={"kind": "Tyyppi", "name": "Nimi"},
    )
    st.caption(
        "Mallit: lisää kohdistus tiedostoon config/model_aliases.yaml. "
        "Benchmarkit: config/epoch_benchmarks.yaml."
    )

st.subheader("Karanteeni")
if quarantine.is_empty():
    st.write("Karanteeni on tyhjä.")
else:
    st.dataframe(quarantine, hide_index=True, width="stretch")
