"""Projektin polut ja YAML-konfiguraation lataus Pydantic-malleiksi.

Konfiguraatio validoidaan latauksen yhteydessä, joten virheellinen YAML kaatuu
heti selkeään virheilmoitukseen eikä vasta laskennan keskellä.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Domain = Literal["coding", "math", "reasoning", "agentic", "general"]
MetricType = Literal["accuracy", "pass_at_1", "elo"]
ContaminationRisk = Literal["low", "medium", "high"]
AnchorMode = Literal["fixed", "post_hoc_linear"]

ROOT_ENV_VAR = "FORECASTER_ROOT"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Polut ----------------------------------------------------------------


class Paths(_Strict):
    root: Path

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "benchmark.duckdb"


def find_project_root(start: Path | None = None) -> Path:
    """Projektin juuri: ympäristömuuttuja tai lähin `pyproject.toml` ylöspäin."""
    env = os.environ.get(ROOT_ENV_VAR)
    if env:
        return Path(env).resolve()
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "config").is_dir():
            return candidate
    raise FileNotFoundError(
        f"Projektin juurta ei löytynyt hakemistosta {current} ylöspäin. "
        f"Aja komento projektikansiossa tai aseta {ROOT_ENV_VAR}."
    )


def get_paths(root: Path | None = None) -> Paths:
    return Paths(root=root.resolve() if root else find_project_root())


# --- Benchmarkit -----------------------------------------------------------


class BenchmarkConfig(_Strict):
    benchmark_id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    domain: Domain
    metric_type: MetricType
    raw_min: float = 0.0
    raw_max: float = 100.0
    guessing_floor: float = Field(default=0.0, ge=0.0, lt=1.0)
    contamination_risk: ContaminationRisk | None = None
    weight: float = Field(default=1.0, gt=0.0)
    release_date: date | None = None

    @model_validator(mode="after")
    def _check_range(self) -> BenchmarkConfig:
        if self.raw_max <= self.raw_min:
            raise ValueError(
                f"{self.benchmark_id}: raw_max ({self.raw_max}) pitää olla suurempi "
                f"kuin raw_min ({self.raw_min})"
            )
        return self


class BenchmarksFile(_Strict):
    benchmarks: list[BenchmarkConfig]

    @model_validator(mode="after")
    def _unique_ids(self) -> BenchmarksFile:
        ids = [b.benchmark_id for b in self.benchmarks]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"Toistuvat benchmark_id:t: {dupes}")
        return self


# --- Ankkurit --------------------------------------------------------------


class Anchor(_Strict):
    model_id: str
    theta: float


class AnchorsFile(_Strict):
    mode: AnchorMode = "fixed"
    anchors: list[Anchor]

    @model_validator(mode="after")
    def _check_anchors(self) -> AnchorsFile:
        if len(self.anchors) < 2:
            raise ValueError("Asteikon kiinnitys vaatii vähintään 2 ankkurimallia")
        ids = [a.model_id for a in self.anchors]
        if len(set(ids)) != len(ids):
            raise ValueError("Sama ankkurimalli on listattu useammin kuin kerran")
        if len({a.theta for a in self.anchors}) < 2:
            raise ValueError("Ankkureilla pitää olla vähintään kaksi eri θ-arvoa")
        return self


# --- Epoch AI -benchmarkien kuvaus (K3) -----------------------------------------


class EpochBenchmarkMap(_Strict):
    benchmark_id: str = Field(pattern=r"^[a-z0-9_]+$")
    domain: Domain
    weight: float = Field(default=1.0, gt=0.0)
    contamination_risk: ContaminationRisk | None = None
    include: bool = True
    # Pakollisia vain, jos Epochin metatiedoista puuttuu tiedosto (ECI:n ulkopuoliset testit)
    source_file: str | None = None
    score_column: str | None = None
    scale: float | None = Field(default=None, gt=0.0)
    # Todellinen katto raaka-asteikolla, jos se on julkaistusti alle Epochin score_ceilingin
    # (esim. ihmisten tarkkuus, kun osa tehtävistä on epäselviä). Lähde on pakollinen.
    score_ceiling: float | None = Field(default=None, gt=0.0)
    ceiling_source: str | None = None

    @model_validator(mode="after")
    def _ceiling_needs_source(self) -> EpochBenchmarkMap:
        if self.score_ceiling is not None and not (self.ceiling_source or "").strip():
            raise ValueError(f"{self.benchmark_id}: score_ceiling vaatii ceiling_source-lähteen")
        return self


class EpochBenchmarksFile(_Strict):
    benchmarks: dict[str, EpochBenchmarkMap]

    @model_validator(mode="after")
    def _unique_ids(self) -> EpochBenchmarksFile:
        ids = [b.benchmark_id for b in self.benchmarks.values()]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"Toistuvat benchmark_id:t epoch_benchmarks.yaml:ssa: {dupes}")
        return self


# --- Mallinimien aliakset ---------------------------------------------------------


class AliasConfig(_Strict):
    source_name: str = Field(min_length=1)
    alias: str = Field(min_length=1)
    model_id: str = Field(min_length=1)


class ModelAliasesFile(_Strict):
    aliases: list[AliasConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> ModelAliasesFile:
        keys = [(a.source_name, a.alias) for a in self.aliases]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ValueError(f"Toistuvat aliakset: {dupes}")
        return self


# --- Ennusteet (K5) ----------------------------------------------------------------


class CostTarget(_Strict):
    label: str = Field(min_length=1)
    model_id: str = Field(min_length=1)


class ForecastFile(_Strict):
    fit_start: date
    horizons_months: list[int] = Field(min_length=1)
    n_boot: int = Field(default=1000, ge=50)
    seed: int = 0
    piecewise_window_months: int = Field(default=12, ge=3)
    piecewise_alpha: float = Field(default=0.05, gt=0, lt=1)
    backtest_holdout_months: int = Field(default=12, ge=1)
    # Liukuva backtest (A24): lähtöpäivät = as_of − k kk. Yksi lähtöpäivä on käytännössä yksi
    # havainto (virheet samanmerkkisiä), joten kalibraatio arvioidaan usealta lähtöpäivältä.
    backtest_origins_months: list[int] = Field(default_factory=lambda: [6, 12, 18, 24])
    max_theta_se: float = Field(default=1.0, gt=0)
    # Horisonttikohtainen hintalattia (A26): halvin malli, jolle taso − β·h/12 ≤ θ < taso
    # (β = rintaman kulmakerroin). False = koko markkinan halvin malli kaikilla horisonteilla.
    level_floor: bool = True
    # Pienen otoksen korjaus kyvykkyyden bootstrapiin (A27): none | inflate | auto
    # (auto = inflate, kun rintamassa on alle small_sample_max_points pistettä).
    small_sample: Literal["none", "inflate", "auto"] = "auto"
    small_sample_max_points: int = Field(default=12, ge=3)
    # Sovitusikkunan epävarmuus (A28): drawissa sovitus alkaa satunnaisesti fit_startista tai
    # as_of − w kk (w näistä). Tyhjä lista = pois.
    fit_windows_months: list[int] = Field(default_factory=list)
    cost_targets: list[CostTarget] = Field(default_factory=list)


class ManualPrice(_Strict):
    model_id: str = Field(min_length=1)
    input: float = Field(ge=0)
    output: float = Field(ge=0)
    valid_from: date | None = None
    note: str | None = None


class ManualPricesFile(_Strict):
    prices: list[ManualPrice] = Field(default_factory=list)


def load_manual_prices(paths: Paths | None = None) -> ManualPricesFile:
    paths = paths or get_paths()
    path = paths.config_dir / "manual_prices.yaml"
    if not path.is_file():
        return ManualPricesFile()
    return ManualPricesFile.model_validate(_read_yaml(path))


def load_forecast(paths: Paths | None = None) -> ForecastFile:
    paths = paths or get_paths()
    return ForecastFile.model_validate(_read_yaml(paths.config_dir / "forecast.yaml"))


# --- Lataus ----------------------------------------------------------------


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Konfiguraatiotiedostoa ei löydy: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: odotettiin YAML-objektia, saatiin {type(data).__name__}")
    return data


def load_benchmarks(paths: Paths | None = None) -> BenchmarksFile:
    paths = paths or get_paths()
    return BenchmarksFile.model_validate(_read_yaml(paths.config_dir / "benchmarks.yaml"))


def load_anchors(paths: Paths | None = None) -> AnchorsFile:
    paths = paths or get_paths()
    return AnchorsFile.model_validate(_read_yaml(paths.config_dir / "anchors.yaml"))


def load_epoch_benchmarks(paths: Paths | None = None) -> EpochBenchmarksFile:
    paths = paths or get_paths()
    return EpochBenchmarksFile.model_validate(
        _read_yaml(paths.config_dir / "epoch_benchmarks.yaml")
    )


def load_model_aliases(paths: Paths | None = None) -> ModelAliasesFile:
    paths = paths or get_paths()
    path = paths.config_dir / "model_aliases.yaml"
    if not path.is_file():
        return ModelAliasesFile()
    return ModelAliasesFile.model_validate(_read_yaml(path))


def check_benchmark_id_conflicts(manual: BenchmarksFile, epoch: EpochBenchmarksFile) -> None:
    """benchmarks.yaml (omat testit) ja epoch_benchmarks.yaml eivät saa käyttää samaa id:tä,
    muuten ne ylikirjoittaisivat toistensa rajat tietokannassa."""
    clash = sorted(
        {b.benchmark_id for b in manual.benchmarks}
        & {b.benchmark_id for b in epoch.benchmarks.values()}
    )
    if clash:
        raise ValueError(
            f"Sama benchmark_id sekä benchmarks.yaml:ssa että epoch_benchmarks.yaml:ssa: {clash}"
        )
