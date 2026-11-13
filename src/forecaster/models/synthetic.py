"""Synteettinen havaintomatriisi tunnetuilla IRT-parametreilla (K2:n validointi).

Rakenne jäljittelee Epoch AI -datakokeilun havaintoja:
- mallit ja benchmarkit julkaistaan ajan myötä; uudemmat ovat kyvykkäämpiä / vaikeampia
- malli voidaan testata vain benchmarkilla, joka on jo julkaistu
- uusia malleja testataan harvoin vanhoilla testeillä (siltamalleja on, mutta vähän)
- osa vanhoista testeistä on saturoitunut (hyvin matala b)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl

EPS = 1e-3


@dataclass(frozen=True)
class SyntheticSpec:
    n_models: int = 60
    n_benchmarks: int = 18
    missing_rate: float = 0.6
    n_saturated: int = 2
    noise_sd: float = 0.3  # kohina logit-avaruudessa
    age_decay: float = 0.35  # kuinka nopeasti vanhoja testejä lakataan ajamasta
    heterogeneous_counts: bool = False  # True: havaintoja/malli vaihtelee 2 … 2·k (kuten Epoch)
    seed: int = 0


@dataclass
class SyntheticData:
    obs: pl.DataFrame  # model_id, benchmark_id, y, weight, guessing_floor
    theta: dict[str, float]
    a: dict[str, float]
    b: dict[str, float]
    c: dict[str, float]
    release_index: dict[str, float]  # mallin julkaisuaika [0, 1]
    saturated: list[str] = field(default_factory=list)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def generate(spec: SyntheticSpec = SyntheticSpec()) -> SyntheticData:
    rng = np.random.default_rng(spec.seed)
    n, m = spec.n_models, spec.n_benchmarks

    t = np.sort(rng.uniform(0, 1, n))  # mallien julkaisuajat
    s = np.sort(rng.uniform(-0.2, 0.9, m))  # benchmarkien julkaisuajat
    theta = 3.0 * t - 1.5 + rng.normal(0, 0.6, n)
    b = 3.0 * s - 1.0 + rng.normal(0, 0.5, m)
    a = np.exp(rng.normal(0.2, 0.3, m))
    c = rng.choice([0.0, 0.25], size=m, p=[0.6, 0.4])

    saturated_idx = list(range(min(spec.n_saturated, m)))  # vanhimmat testit
    b[saturated_idx] = theta.min() - 3.5

    k_target = max(3, round((1 - spec.missing_rate) * m))
    rows = []
    for i in range(n):
        available = s <= t[i] + 0.05
        if available.sum() < 3:
            available[np.argsort(s)[:3]] = True
        age = np.clip(t[i] - s, 0, None)
        w = np.where(available, np.exp(-age / spec.age_decay), 0.0)
        k_i = int(rng.integers(2, 2 * k_target - 1)) if spec.heterogeneous_counts else k_target
        k = min(k_i, int(available.sum()))
        chosen = rng.choice(m, size=k, replace=False, p=w / w.sum())
        for j in chosen:
            z = a[j] * (theta[i] - b[j]) + rng.normal(0, spec.noise_sd)
            y = c[j] + (1 - c[j]) * _sigmoid(z)
            rows.append((i, int(j), float(np.clip(y, EPS, 1 - EPS))))

    mid = [f"model_{i:03d}" for i in range(n)]
    bid = [f"bench_{j:02d}" for j in range(m)]
    obs = pl.DataFrame(
        {
            "model_id": [mid[i] for i, _, _ in rows],
            "benchmark_id": [bid[j] for _, j, _ in rows],
            "y": [y for _, _, y in rows],
            "weight": [1.0] * len(rows),
            "guessing_floor": [float(c[j]) for _, j, _ in rows],
        }
    ).sort("model_id", "benchmark_id")

    return SyntheticData(
        obs=obs,
        theta=dict(zip(mid, theta.tolist(), strict=True)),
        a=dict(zip(bid, a.tolist(), strict=True)),
        b=dict(zip(bid, b.tolist(), strict=True)),
        c=dict(zip(bid, c.tolist(), strict=True)),
        release_index=dict(zip(mid, t.tolist(), strict=True)),
        saturated=[bid[j] for j in saturated_idx],
    )
