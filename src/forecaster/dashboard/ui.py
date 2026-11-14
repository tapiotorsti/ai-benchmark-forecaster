"""Streamlit-välimuisti ja yhteiset apufunktiot sivuille.

Välimuistiavaimena on tietokantatiedoston (ja WAL:n) muokkausaika: uusi `forecaster fit` tai
`ingest` mitätöi välimuistin automaattisesti. Yhteys avataan aina vain lukutilassa.

Välimuisti on `st.cache_resource` eikä `st.cache_data`: tulosta ei sarjallisteta picklellä
joka latauksella (nopeampi). Polars-taulut ovat muuttumattomia, joten saman olion jakaminen
on turvallista. Sivut eivät saa muokata palautettuja dict-olioita paikallaan.
Huom: POLARS_MAX_THREADS=1 asetetaan app/dashboard.py:ssä (säiekaatuminen, ks. siellä).
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import polars as pl
import streamlit as st

from forecaster.config import ForecastFile, Paths, get_paths, load_anchors, load_forecast
from forecaster.dashboard import data
from forecaster.db.repository import Repository

ROOT_ENV = "FORECASTER_ROOT"


def paths() -> Paths:
    root = os.environ.get(ROOT_ENV)
    return get_paths(Path(root) if root else None)


def db_token() -> tuple[str, float]:
    """(polku, muokkausaika) – muuttuu, kun kantaan kirjoitetaan."""
    p = paths().db_path
    mtimes = [f.stat().st_mtime for f in (p, p.with_name(p.name + ".wal")) if f.exists()]
    return str(p), max(mtimes, default=0.0)


def _repo(token: tuple[str, float]) -> Repository:
    return Repository.connect(token[0], read_only=True)


def require_db() -> tuple[str, float]:
    token = db_token()
    if token[1] == 0.0:
        st.error("Tietokantaa ei löydy. Aja ensin `forecaster init-db`, `forecaster ingest all` "
                 "ja `forecaster fit`.")  # fmt: skip
        st.stop()
    return token


@st.cache_resource(show_spinner=False)
def runs(token) -> dict[str, str]:
    with _repo(token) as r:
        return r.latest_runs_by_domain()


@st.cache_resource(show_spinner=False)
def run_info(token, run_id: str) -> dict | None:
    with _repo(token) as r:
        return r.get_run_info(run_id)


@st.cache_resource(show_spinner=False)
def models(token, run_id: str, domain: str) -> pl.DataFrame:
    with _repo(token) as r:
        return r.get_models_overview(run_id, domain)


@st.cache_resource(show_spinner=False)
def benchmark_params(token, run_id: str) -> pl.DataFrame:
    with _repo(token) as r:
        return r.get_benchmark_params(run_id)


@st.cache_resource(show_spinner=False)
def observations(token, exclude_outliers: bool) -> pl.DataFrame:
    with _repo(token) as r:
        return r.get_observation_matrix("overall", exclude_outliers=exclude_outliers)


@st.cache_resource(show_spinner=False)
def outliers(token) -> pl.DataFrame:
    with _repo(token) as r:
        return r.get_outliers()


@st.cache_resource(show_spinner=False)
def prices(token, fit_start: date, as_of: date) -> pl.DataFrame:
    ff = forecast_file()
    ff = ff.model_copy(update={"fit_start": fit_start})
    with _repo(token) as r:
        return data.price_grid(r.get_price_at_dates, ff, as_of)


@st.cache_resource(show_spinner=False)
def latest_forecasts(token) -> dict[str, dict]:
    with _repo(token) as r:
        return r.get_latest_forecasts()


@st.cache_resource(show_spinner=False)
def ingest_log(token) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    with _repo(token) as r:
        return r.get_ingest_runs(), r.get_quarantine(), r.get_unknown_names()


@st.cache_resource(show_spinner="Lasketaan ennustekaistaa…")
def forecast_band(token, run_id: str, domain: str, as_of: date):
    return data.forecast_band(models(token, run_id, domain), forecast_file(), as_of)


@st.cache_resource(show_spinner="Lasketaan kustannusennustetta…")
def cost_view(token, run_id: str, target: float, as_of: date) -> dict:
    ff = forecast_file()
    return data.cost_view(models(token, run_id, "overall"), prices(token, ff.fit_start, as_of),
                          target, ff, as_of)  # fmt: skip


@st.cache_resource(show_spinner=False, max_entries=32)
def simulate(token, settings_key: str, _settings: data.SimSettings):
    """Välimuisti painojen hashilla (settings_key); _settings ei ole osa avainta."""
    obs = observations(token, _settings.exclude_outliers)
    with _repo(token) as r:
        bench = r.get_benchmark_params(r.latest_run_id("overall"))
    anchors = {a.model_id: a.theta for a in load_anchors(paths()).anchors}
    res, used = data.simulate(obs, bench, anchors, _settings)
    return res, used.height


@st.cache_resource(show_spinner=False)
def _forecast_file_cached(mtime: float) -> ForecastFile:
    return load_forecast(paths())


def forecast_file() -> ForecastFile:
    f = paths().config_dir / "forecast.yaml"
    return _forecast_file_cached(f.stat().st_mtime if f.exists() else 0.0)


def overall_run(token) -> str:
    r = runs(token).get("overall")
    if r is None:
        st.error("IRT-ajoa ei ole. Aja ensin `forecaster fit`.")
        st.stop()
    return r


def license_note() -> None:
    st.caption(
        "Data: Epoch AI (CC BY 4.0), OpenRouter, simonw/llm-prices (ei lisenssiä – vain "
        "paikalliseen käyttöön). θ-asteikko: GPT-4 (kesäkuu 2023) = 0, GPT-5 = 3.5."
    )
