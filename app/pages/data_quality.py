"""Sivu 6: ingest-ajojen loki, karanteeni ja tuntemattomat nimet."""

import json

import polars as pl
import streamlit as st

from forecaster.dashboard import ui

st.title("Datan laatu")
token = ui.require_db()
runs, quarantine, unknown = ui.ingest_log(token)

failed = runs.filter(pl.col("status") == "failed").height
grid = ui.metrics_grid()
grid.metric("Ingest-ajoja (viim. 50)", runs.height)
grid.metric("Epäonnistuneita", failed)
grid.metric("Karanteenissa", quarantine.height)

st.subheader("Päivitysajot")
pruns = ui.pipeline_runs(token)
if pruns.is_empty():
    st.write("Ei vielä päivitysajoja. Aja `forecaster update` tai ota ajastus käyttöön.")
else:
    ui.table(
        pruns.with_columns(
            hälytyksiä=pl.Series(
                [len(json.loads(a or "[]")) for a in pruns["alerts_json"]], dtype=pl.Int64
            )
        ).select("started_at", "status", "hälytyksiä", "finished_at", "run_id"),
        ["started_at", "status", "hälytyksiä"],
        "pipeline_runs",
        alt="Automaattisten päivitysajojen loki",
        column_config={
            "started_at": "Aloitettu",
            "status": "Tila",
            "finished_at": "Valmis",
            "run_id": "Ajo",
        },
    )
    with st.expander("Uusimman ajon vaiheet"):
        latest = pruns.row(0, named=True)
        rows = json.loads(latest["steps_json"] or "[]")
        if rows:
            steps = pl.DataFrame(rows)
            st.dataframe(steps, hide_index=True, alt="Uusimman päivitysajon vaiheet")

st.subheader("Ingest-ajot")
ui.table(
    runs.select(
        "source_name",
        "started_at",
        "status",
        "rows_fetched",
        "rows_upserted",
        "rows_quarantined",
        "message",
    ),
    ["source_name", "started_at", "status"],
    "ingest_runs",
    alt="Datan tuontiajojen loki",
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
