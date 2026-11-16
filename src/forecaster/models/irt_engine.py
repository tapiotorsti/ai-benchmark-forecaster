"""Jatkuva 2PL-IRT (määrittely 4.2) PyTorchilla.

Malli:      ŷ_ij = c_j + (1 - c_j) · σ(a_j (θ_i - b_j)),   a_j = softplus(ã_j)
Häviö:      -Σ w_j [y ln ŷ + (1-y) ln(1-ŷ)]
            + λ (mean θ² + mean b²)                      (määrittely)
            + λ_a · mean (ln a)²                          (A12)
            b-prior estää saturoituneen testin b:n karkaamisen; λ_a pitää skaalan
            määrättynä ilman ankkureita (ilman sitä asteikko romahtaa ja a → ∞).
Keskivirhe: HC3-sandwich (A13): Cov = H⁻¹ (Σ g_k g_kᵀ / (1-h_k)²) H⁻¹, missä
            H = H_data + H_prior, g_k on havainnon k häviön gradientti ja h_k sen
            vipuarvo (leverage). Pelkkä H⁻¹ olettaa Bernoulli-varianssin ja antaa
            moninkertaisen SE:n; quasi-φ ja korjaamaton sandwich aliarvioivat SE:n, koska
            mallikohtaisia havaintoja on vähän (keskim. vipuarvo ~0.2). Monte Carlo
            -validointi synteettisellä datalla: 80 %:n välin kattavuus ≈ 0.82.

Ankkurointi:
- fixed:           ankkurien θ ei ole optimoitava parametri
- post_hoc_linear: sovitus ilman ankkureita, sitten θ' = αθ + β, b' = αb + β, a' = a/α
                   (ennusteet muuttumattomia). >2 ankkurilla α, β pienimmän neliösumman mukaan.
"""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

import numpy as np
import polars as pl
import torch
from torch.nn.functional import logsigmoid, softplus

from forecaster.db.repository import EPSILON

REQUIRED_COLUMNS = {"model_id", "benchmark_id", "y", "weight", "guessing_floor"}
FULL_HESSIAN_MAX_PARAMS = 4000


@dataclass(frozen=True)
class IRTConfig:
    anchors: dict[str, float] = field(default_factory=dict)
    anchor_mode: Literal["fixed", "post_hoc_linear"] = "fixed"
    lambda_reg: float = 0.01
    lambda_a: float = 0.01
    # True: ankkurit ovat linkitysjoukko (esim. domain-ajo kokonaisajon θ:hin), PNS-sovitus
    # on tarkoituksellinen eikä tarkkaa osumaa odoteta → ei varoitusta >2 ankkurista.
    anchor_linking: bool = False
    # benchmark_id → (b, a): kiinteät osioparametrit (esim. overall-ajosta). Kiinteät osiot
    # määräävät asteikon, joten ankkureita ei tarvita (domain-ajot, A12).
    fixed_items: dict[str, tuple[float, float]] = field(default_factory=dict)
    max_iter: int = 5000
    grad_tol: float = 1e-5
    compute_se: bool = True
    seed: int = 0


@dataclass
class IRTResult:
    model_ids: list[str]
    benchmark_ids: list[str]
    theta: np.ndarray
    theta_se: np.ndarray | None
    a: np.ndarray
    b: np.ndarray
    c: np.ndarray
    loss: float
    grad_norm: float
    converged: bool
    n_iter: int
    n_observations: int
    dispersion: float
    config: IRTConfig
    missing_anchors: list[str] = field(default_factory=list)
    anchor_transform: tuple[float, float] | None = None  # (α, β) post_hoc_linear

    def theta_by_model(self) -> dict[str, float]:
        return dict(zip(self.model_ids, self.theta.tolist(), strict=True))

    def theta_se_by_model(self) -> dict[str, float]:
        if self.theta_se is None:
            raise ValueError("Keskivirheitä ei laskettu (compute_se=False)")
        return dict(zip(self.model_ids, self.theta_se.tolist(), strict=True))

    def a_by_benchmark(self) -> dict[str, float]:
        return dict(zip(self.benchmark_ids, self.a.tolist(), strict=True))

    def b_by_benchmark(self) -> dict[str, float]:
        return dict(zip(self.benchmark_ids, self.b.tolist(), strict=True))

    def predict(self, obs: pl.DataFrame) -> np.ndarray:
        """ŷ jokaiselle obs-riville (model_id, benchmark_id)."""
        mi = {m: k for k, m in enumerate(self.model_ids)}
        bi = {b: k for k, b in enumerate(self.benchmark_ids)}
        i = np.array([mi[m] for m in obs["model_id"]])
        j = np.array([bi[b] for b in obs["benchmark_id"]])
        z = self.a[j] * (self.theta[i] - self.b[j])
        return self.c[j] + (1 - self.c[j]) / (1 + np.exp(-z))


# --- Häviö ----------------------------------------------------------------


def _log_lik_terms(z, c_log, c1_log, y):
    """Numeerisesti vakaat ln ŷ ja ln(1-ŷ), kun ŷ = c + (1-c)σ(z)."""
    log_yhat = torch.logaddexp(c_log, c1_log + logsigmoid(z))
    log_1m_yhat = c1_log + logsigmoid(-z)
    return y * log_yhat + (1 - y) * log_1m_yhat


class _Problem:
    """Parametrivektori p = [θ_vapaat, b, ã]; ankkuroidut θ:t ovat vakioita."""

    def __init__(self, obs: pl.DataFrame, anchors: dict[str, float], cfg: IRTConfig):
        self.cfg = cfg
        self.model_ids = sorted(obs["model_id"].unique().to_list())
        self.benchmark_ids = sorted(obs["benchmark_id"].unique().to_list())
        mi = {m: k for k, m in enumerate(self.model_ids)}
        bi = {b: k for k, b in enumerate(self.benchmark_ids)}
        self.N, self.M = len(self.model_ids), len(self.benchmark_ids)

        dt = torch.float64
        self.i = torch.tensor([mi[m] for m in obs["model_id"]], dtype=torch.long)
        self.j = torch.tensor([bi[b] for b in obs["benchmark_id"]], dtype=torch.long)
        y = obs["y"].to_numpy().astype(np.float64)
        self.y = torch.tensor(np.clip(y, EPSILON, 1 - EPSILON), dtype=dt)
        self.w = torch.tensor(obs["weight"].to_numpy(), dtype=dt)

        floors = obs.group_by("benchmark_id").agg(pl.col("guessing_floor").first())
        c = dict(floors.iter_rows())
        self.c = torch.tensor([c[b] for b in self.benchmark_ids], dtype=dt)
        with np.errstate(divide="ignore"):
            self.c_log = torch.log(self.c)  # -inf kun c = 0 → logaddexp hoitaa
        self.c1_log = torch.log1p(-self.c)

        self.anchor_mask = torch.zeros(self.N, dtype=torch.bool)
        self.anchor_vals = torch.zeros(self.N, dtype=dt)
        for m, v in anchors.items():
            self.anchor_mask[mi[m]] = True
            self.anchor_vals[mi[m]] = v
        self.free_idx = torch.nonzero(~self.anchor_mask).squeeze(1)
        self.nf = int(self.free_idx.numel())

        # Kiinteät osioparametrit (domain-ajot, A12): b ja a vakioita, vain θ estimoidaan
        fixed = cfg.fixed_items
        self.item_fixed = torch.tensor([b in fixed for b in self.benchmark_ids], dtype=torch.bool)
        self.b_const = torch.tensor([fixed.get(b, (0.0, 1.0))[0] for b in self.benchmark_ids],
                                    dtype=dt)  # fmt: skip
        self.a_const = torch.tensor([fixed.get(b, (0.0, 1.0))[1] for b in self.benchmark_ids],
                                    dtype=dt)  # fmt: skip
        self.free_items = torch.nonzero(~self.item_fixed).squeeze(1)
        self.nbf = int(self.free_items.numel())

    def initial(self) -> torch.Tensor:
        """Alkuarvot keskiarvojen logiteista (nopeampi ja vakaampi kuin satunnainen)."""
        y_adj = ((self.y - self.c[self.j]) / (1 - self.c[self.j])).clamp(0.02, 0.98)
        logit = torch.log(y_adj / (1 - y_adj))
        theta0 = torch.zeros(self.N, dtype=torch.float64).index_add_(0, self.i, logit)
        theta0 /= torch.bincount(self.i, minlength=self.N).clamp(min=1)
        b0 = torch.zeros(self.M, dtype=torch.float64).index_add_(0, self.j, -logit)
        b0 /= torch.bincount(self.j, minlength=self.M).clamp(min=1)
        theta0 = (theta0 - theta0.mean()) / (theta0.std() + 1e-9)
        b0 = (b0 - b0.mean()) / (b0.std() + 1e-9)
        if bool(self.item_fixed.any()):
            # Kiinteät osiot määräävät asteikon → θ:n alkuarvo samalle asteikolle
            fixed_b = self.b_const[self.item_fixed]
            theta0 = theta0 * (fixed_b.std() if fixed_b.numel() > 1 else 1.0) + fixed_b.mean()
        a_raw0 = torch.full((self.M,), math.log(math.e - 1), dtype=torch.float64)  # a = 1
        return torch.cat([theta0[self.free_idx], b0[self.free_items], a_raw0[self.free_items]])

    def unpack(self, p: torch.Tensor):
        theta = self.anchor_vals.clone()
        theta = theta.index_put((self.free_idx,), p[: self.nf])
        k = self.nf + self.nbf
        b = self.b_const.index_put((self.free_items,), p[self.nf : k])
        a = self.a_const.index_put((self.free_items,), softplus(p[k:]))
        return theta, b, a

    def data_loss(self, p: torch.Tensor) -> torch.Tensor:
        theta, b, a = self.unpack(p)
        z = a[self.j] * (theta[self.i] - b[self.j])
        ll = _log_lik_terms(z, self.c_log[self.j], self.c1_log[self.j], self.y)
        return -(self.w * ll).sum()

    def prior_loss(self, p: torch.Tensor) -> torch.Tensor:
        theta, b, a = self.unpack(p)
        lam, lam_a = self.cfg.lambda_reg, self.cfg.lambda_a
        return lam * ((theta**2).mean() + (b**2).mean()) + lam_a * (torch.log(a) ** 2).mean()

    def loss(self, p: torch.Tensor) -> torch.Tensor:
        return self.data_loss(p) + self.prior_loss(p)

    def mean(self, p: torch.Tensor) -> torch.Tensor:
        theta, b, a = self.unpack(p)
        c = self.c[self.j]
        return c + (1 - c) * torch.sigmoid(a[self.j] * (theta[self.i] - b[self.j]))

    def per_obs_loss(self, p: torch.Tensor) -> torch.Tensor:
        theta, b, a = self.unpack(p)
        z = a[self.j] * (theta[self.i] - b[self.j])
        return -self.w * _log_lik_terms(z, self.c_log[self.j], self.c1_log[self.j], self.y)

    def dispersion(self, p: torch.Tensor) -> float:
        """Pearson-χ² / vapausasteet (diagnostiikka; isojen ongelmien SE-approksimaatio)."""
        with torch.no_grad():
            yhat = self.mean(p)
            pearson = self.w * (self.y - yhat) ** 2 / (yhat * (1 - yhat))
            dof = max(len(self.y) - p.numel(), 1)
            return float(pearson.sum() / dof)


# --- Sovitus ----------------------------------------------------------------


def _validate(obs: pl.DataFrame, cfg: IRTConfig) -> tuple[dict[str, float], list[str]]:
    missing_cols = REQUIRED_COLUMNS - set(obs.columns)
    if missing_cols:
        raise ValueError(f"Havaintomatriisista puuttuu sarakkeita: {sorted(missing_cols)}")
    if obs.is_empty():
        raise ValueError("Havaintomatriisi on tyhjä")
    if obs.select("model_id", "benchmark_id").is_duplicated().any():
        raise ValueError("Havaintomatriisissa on toistuvia (model_id, benchmark_id) -pareja")
    present = set(obs["model_id"].unique())
    anchors = {m: v for m, v in cfg.anchors.items() if m in present}
    missing = sorted(set(cfg.anchors) - present)
    if cfg.anchors and len(anchors) < 2:
        raise ValueError(
            f"Datassa on vain {len(anchors)} ankkurimallia (puuttuvat: {missing}); "
            "asteikon kiinnitys vaatii vähintään 2"
        )
    if missing:
        warnings.warn(f"Ankkurimalleja ei löydy datasta: {missing}", stacklevel=3)
    return anchors, missing


def _optimise(prob: _Problem, p0: torch.Tensor, cfg: IRTConfig):
    p = p0.clone().requires_grad_(True)
    opt = torch.optim.LBFGS(
        [p],
        lr=1.0,
        max_iter=cfg.max_iter,
        tolerance_grad=cfg.grad_tol,
        tolerance_change=1e-12,
        history_size=50,
        line_search_fn="strong_wolfe",
    )

    def closure():
        opt.zero_grad()
        loss = prob.loss(p)
        loss.backward()
        return loss

    opt.step(closure)
    loss_t = prob.loss(p)
    (grad,) = torch.autograd.grad(loss_t, p)
    loss = loss_t.detach()
    n_iter = opt.state[opt.param_groups[0]["params"][0]].get("n_iter", 0)
    grad_norm = float(grad.abs().max())
    # Toleranssi suhteessa havaintojen määrään: summahäviön gradientti kasvaa datan mukana.
    converged = grad_norm < cfg.grad_tol * max(1.0, len(prob.y) / 100)
    return p.detach(), float(loss), grad_norm, converged, int(n_iter)


def _check_hessian(h: torch.Tensor) -> None:
    eig = torch.linalg.eigvalsh(h)
    lo, hi = float(eig.min()), float(eig.max())
    if lo <= 0:
        warnings.warn(
            f"Hessian ei ole positiividefiniitti (pienin ominaisarvo {lo:.2e}) – "
            "optimi ei ole aito minimi, SE:t epäluotettavia",
            stacklevel=4,
        )
    elif hi / lo > 1e10:
        warnings.warn(
            f"Hessian huonosti ehdollistettu (kunto {hi / lo:.1e}) – asteikko heikosti "
            "identifioitu (puuttuvat ankkurit?), SE:t epäluotettavia",
            stacklevel=4,
        )


def _theta_covariance(prob: _Problem, p: torch.Tensor, phi: float) -> np.ndarray:
    """Vapaiden θ:iden kovarianssimatriisi (nf × nf).

    Täysi HC3-sandwich (A13). Yli FULL_HESSIAN_MAX_PARAMS parametrin ongelmassa
    diagonaalinen quasi-approksimaatio, joka aliarvioi SE:n noin 40 % (math-reviewer,
    Monte Carlo). Nykydata jää selvästi kynnyksen alle.
    """
    n = p.numel()
    if n <= FULL_HESSIAN_MAX_PARAMS:
        h = torch.autograd.functional.hessian(prob.loss, p)
        h = 0.5 * (h + h.T)
        _check_hessian(h)
        try:
            h_inv = torch.linalg.inv(h)
        except RuntimeError:
            warnings.warn("Hessian on singulaarinen – käytetään pseudoinverssiä", stacklevel=3)
            h_inv = torch.linalg.pinv(h)
        g = torch.autograd.functional.jacobian(prob.per_obs_loss, p, vectorize=True)
        d_mu = torch.autograd.functional.jacobian(prob.mean, p, vectorize=True)
        mu = prob.mean(p).detach()
        # Vipuarvo Gauss–Newton-informaation kautta: h_k = w_k μ'_kᵀ H⁻¹ μ'_k / (μ_k (1-μ_k))
        lev = prob.w * ((d_mu @ h_inv) * d_mu).sum(1) / (mu * (1 - mu))
        g_adj = g / (1 - lev.clamp(max=0.99))[:, None]
        cov = h_inv @ (g_adj.T @ g_adj) @ h_inv
        return cov[: prob.nf, : prob.nf].numpy()
    warnings.warn(
        f"{n} parametria > {FULL_HESSIAN_MAX_PARAMS}: SE diagonaaliapproksimaatiolla – "
        "korrelaatiot jätetään huomiotta ja SE aliarvioituu noin 40 %",
        stacklevel=3,
    )
    pp = p.clone().requires_grad_(True)
    (g,) = torch.autograd.grad(
        prob.data_loss(pp) / phi + prob.prior_loss(pp), pp, create_graph=True
    )
    diag = torch.zeros(n, dtype=torch.float64)
    for k in range(prob.nf):
        (row,) = torch.autograd.grad(g[k], pp, retain_graph=True)
        diag[k] = row[k]
    return np.diag(1.0 / np.clip(diag[: prob.nf].numpy(), 1e-12, None))


def _linear_anchor_map(x: torch.Tensor, anchor_idx: list[int], targets: torch.Tensor):
    """θ' = αθ + β, missä α, β PNS-sovitus ankkureista (2 ankkurilla tarkka osuma)."""
    xa = x[anchor_idx]
    xm, tm = xa.mean(), targets.mean()
    alpha = ((xa - xm) * (targets - tm)).sum() / ((xa - xm) ** 2).sum()
    beta = tm - alpha * xm
    return alpha, beta


def _post_hoc_transform(
    theta: np.ndarray,
    cov: np.ndarray | None,
    ids: list[str],
    anchors: dict[str, float],
    linking: bool = False,
):
    """Lineaarinen ankkurointi + delta-menetelmän SE (ankkurien epävarmuus mukana)."""
    idx = {m: k for k, m in enumerate(ids)}
    anchor_idx = [idx[m] for m in anchors]
    targets = torch.tensor(list(anchors.values()), dtype=torch.float64)
    x = torch.tensor(theta, dtype=torch.float64)
    alpha, beta = (float(v) for v in _linear_anchor_map(x, anchor_idx, targets))
    if alpha <= 0:
        raise ValueError(
            "Ankkurien järjestys on ristiriidassa sovituksen kanssa (α ≤ 0); tarkista anchors.yaml"
        )
    if len(anchors) > 2 and not linking:
        warnings.warn(
            "post_hoc_linear yli kahdella ankkurilla: ankkurit eivät osu tarkasti (PNS). "
            "Käytä fixed-tilaa, jos tarkka osuma vaaditaan.",
            stacklevel=3,
        )
    se = None
    if cov is not None:

        def mapped(v: torch.Tensor) -> torch.Tensor:
            a_, b_ = _linear_anchor_map(v, anchor_idx, targets)
            return a_ * v + b_

        jac = torch.autograd.functional.jacobian(mapped, x).numpy()
        se = np.sqrt(np.clip(np.diag(jac @ cov @ jac.T), 0, None))
        if len(anchors) == 2:
            se[anchor_idx] = 0.0  # numeerinen nolla → tarkka
    return alpha, beta, se


def fit_irt(obs: pl.DataFrame, cfg: IRTConfig | None = None) -> IRTResult:
    """Sovita 2PL-IRT havaintomatriisiin (sarakkeet: REQUIRED_COLUMNS)."""
    cfg = cfg or IRTConfig()
    anchors, missing = _validate(obs, cfg)
    torch.manual_seed(cfg.seed)

    n_fixed = len(set(cfg.fixed_items) & set(obs["benchmark_id"].unique()))
    if cfg.fixed_items and n_fixed < 2:
        raise ValueError("Kiinteitä osioparametreja pitää olla datassa vähintään 2")
    if not anchors and not n_fixed:
        warnings.warn(
            "Ajo ilman ankkureita: θ-asteikon sijainti ja skaala määräytyvät vain priorista "
            "(λ, λ_a, N, M). Tulokset eivät ole vertailukelpoisia muihin ajoihin.",
            stacklevel=2,
        )

    post_hoc = cfg.anchor_mode == "post_hoc_linear"
    prob = _Problem(obs, {} if post_hoc else anchors, cfg)
    p, loss, grad_norm, converged, n_iter = _optimise(prob, prob.initial(), cfg)
    if not converged:
        warnings.warn(
            f"IRT ei konvergoinut (grad {grad_norm:.2e}, {n_iter} iteraatiota)", stacklevel=2
        )
    theta_t, b_t, a_t = prob.unpack(p)
    theta, b, a = theta_t.numpy().copy(), b_t.numpy().copy(), a_t.numpy().copy()

    phi = prob.dispersion(p)
    cov = _theta_covariance(prob, p, phi) if cfg.compute_se else None
    theta_se = None
    if cov is not None:
        theta_se = np.zeros(prob.N)
        theta_se[prob.free_idx.numpy()] = np.sqrt(np.clip(np.diag(cov), 0, None))

    transform = None
    if post_hoc and anchors:
        # post_hoc-tilassa kaikki θ:t ovat vapaita → cov on N × N mallijärjestyksessä
        alpha, beta, theta_se = _post_hoc_transform(
            theta, cov, prob.model_ids, anchors, linking=cfg.anchor_linking
        )
        theta, b, a = alpha * theta + beta, alpha * b + beta, a / alpha
        transform = (alpha, beta)

    return IRTResult(
        model_ids=prob.model_ids,
        benchmark_ids=prob.benchmark_ids,
        theta=theta,
        theta_se=theta_se,
        a=a,
        b=b,
        c=prob.c.numpy().copy(),
        loss=loss,
        grad_norm=grad_norm,
        converged=converged,
        n_iter=n_iter,
        n_observations=obs.height,
        dispersion=phi,
        config=cfg,
        missing_anchors=missing,
        anchor_transform=transform,
    )


# --- Tallennus ----------------------------------------------------------------


def make_run_id(domain: str, obs: pl.DataFrame, cfg: IRTConfig) -> str:
    digest = hashlib.sha1()
    digest.update(obs.sort("model_id", "benchmark_id").write_csv().encode())
    digest.update(json.dumps(asdict(cfg), sort_keys=True).encode())
    # Mikrosekunnit: sama data samalla sekunnilla (nopeat peräkkäiset ajot, CI) ei saa törmätä
    # irt_runs-pääavaimeen. Tiivisteen 8 viimeistä merkkiä pysyvät lopussa (fc-ajon tunniste).
    return f"irt_{domain}_{datetime.now():%Y%m%dT%H%M%S%f}_{digest.hexdigest()[:8]}"


def fit_and_store(repo, cfg: IRTConfig, domain: str = "overall") -> str:
    """Lue havaintomatriisi, sovita ja tallenna (ilman K4:n karsintaa/poikkeamia).

    Tuotantoajo: forecaster.models.pipeline.run_fit.
    """
    obs = repo.get_observation_matrix(domain)
    if obs.is_empty():
        raise ValueError(f"Ei havaintoja domainille '{domain}' – aja ensin ingest")
    return store_result(repo, fit_irt(obs, cfg), obs, domain)


def store_result(
    repo, res: IRTResult, obs: pl.DataFrame, domain: str, extra: dict | None = None
) -> str:
    """Kirjoita irt_runs/capability_scores/benchmark_irt_params. Palauttaa run_id:n."""
    cfg = res.config
    run_id = make_run_id(domain, obs, cfg)
    params = asdict(cfg) | {
        "dispersion": res.dispersion,
        "grad_norm": res.grad_norm,
        "n_iter": res.n_iter,
        "missing_anchors": res.missing_anchors,
        "anchor_transform": res.anchor_transform,
    }
    if len(json.dumps(params["anchors"])) > 2000:  # linkitysajossa satoja ankkureita
        params["anchors"] = f"<{len(cfg.anchors)} linkitysankkuria>"
    if params["fixed_items"]:
        params["fixed_items"] = f"<{len(cfg.fixed_items)} kiinteää osiota>"
    params |= extra or {}
    repo._run(
        "INSERT INTO irt_runs (run_id, domain, anchor_mode, n_models, n_benchmarks, "
        "n_observations, final_loss, converged, params_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            run_id, domain, cfg.anchor_mode, len(res.model_ids), len(res.benchmark_ids),
            res.n_observations, res.loss, res.converged, json.dumps(params),
        ],
    )  # fmt: skip
    se = res.theta_se if res.theta_se is not None else [None] * len(res.model_ids)
    scores = pl.DataFrame(
        {
            "run_id": run_id,
            "model_id": res.model_ids,
            "domain": domain,
            "theta": res.theta,
            "theta_se": se,
        }
    )
    repo._upsert("capability_scores", scores, ["run_id", "model_id", "domain"])
    irt_params = pl.DataFrame(
        {
            "run_id": run_id,
            "benchmark_id": res.benchmark_ids,
            "difficulty_b": res.b,
            "discrimination_a": res.a,
        }
    )
    repo._upsert("benchmark_irt_params", irt_params, ["run_id", "benchmark_id"])
    return run_id
