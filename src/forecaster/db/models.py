"""Pydantic-rivimallit tietokantaan kirjoitettavalle datalle.

Ingest-adapterit tuottavat näitä; repository muuntaa ne tauluiksi. Benchmarkit
kirjoitetaan suoraan `forecaster.config.BenchmarkConfig`-malleista.
"""

from __future__ import annotations

import hashlib
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelRow(_Row):
    model_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    release_date: date
    is_open_weights: bool = False
    estimated_compute_flops: float | None = Field(default=None, gt=0)
    is_anchor_model: bool = False


class AliasRow(_Row):
    source_name: str = Field(min_length=1)
    alias: str = Field(min_length=1)
    model_id: str = Field(min_length=1)


class EvaluationRow(_Row):
    """Yksi tulos. `normalized_score` lasketaan repositoryssä benchmarkin rajoista."""

    model_id: str = Field(min_length=1)
    benchmark_id: str = Field(min_length=1)
    raw_score: float
    source_name: str = Field(min_length=1)
    model_version: str = ""
    eval_date: date | None = None
    source_url: str | None = None

    @property
    def eval_id(self) -> str:
        key = f"{self.model_id}|{self.benchmark_id}|{self.source_name}|{self.model_version}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


class PriceRow(_Row):
    model_id: str = Field(min_length=1)
    valid_from: date
    source_name: str = Field(min_length=1)
    input_cost_per_1m: float = Field(ge=0)
    output_cost_per_1m: float = Field(ge=0)
    blended_cost_per_1m: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _blend(self) -> PriceRow:
        if self.blended_cost_per_1m is None:
            # Määrittelyn 3:1 input/output -painotus.
            blended = (3 * self.input_cost_per_1m + self.output_cost_per_1m) / 4
            object.__setattr__(self, "blended_cost_per_1m", blended)
        return self
