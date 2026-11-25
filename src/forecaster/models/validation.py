"""6.3 (A36): θ-asteikon validointi ennustamalla tuloksia, joita sovitus ei nähnyt.

ECI-vertailu (A10) on toistettavuuden tarkistus, koska ECI on Epochin IRT-tyyppinen malli
pääosin samasta datasta. Tässä mitataan, ennustaako asteikko tuloksia:

1. Pois jätetyt tulokset (k-osainen ristiinvalidointi): osa havainnoista piilotetaan,
   IRT sovitetaan lopuilla ja piilotetut ennustetaan kaavalla c + (1 − c)·σ(a(θ − b)).
   Vertailukohdat sovitetaan samoihin opetushavaintoihin:
   - testin keskiarvo (ei tietoa mallista)
   - 1PL-tyyppinen additiivinen malli c + (1 − c)·σ(μ + m_i + d_j): sama jatkuva BCE ja
     leikkaus kuin IRT-moottorissa, mutta yhteinen erottelukyky (katselmointi A36: aiempi
     logit-asteikon pienimmän neliösumman versio oli heikompi kuin oikein sovitettu 1PL)
2. Uuden testin kylmäkäynnistys: testi jätetään kokonaan pois, IRT sovitetaan ilman sitä,
   ja testin parametrit sovitetaan muutamaan (n_cal) satunnaiseen malliin, joiden θ on
   tunnettu. Muiden mallien tulokset ennustetaan. Vertailukohta: kalibrointimallien
   keskiarvo.

Mittarit ovat normalisoidulla asteikolla (0–1). Kaikki satunnaisuus siemenellä.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, replace

import numpy as np
import polars as pl
from scipy.optimize import minimize

from forecaster.models.irt_engine import IRTConfig, fit_irt
from forecaster.models.quality import prune_sparse

Y_CLIP = 1e-3
METHODS = {"irt": "pred_irt", "additive": "pred_additive", "bench_mean": "pred_bench_mean"}


@dataclass(frozen=True)
class ValidationConfig:
    folds: int = 5
    seed: int = 0
    min_model_obs: int = 3
    min_benchmark_obs: int = 5
    n_cal: int = 5  # kylmäkäynnistyksen kalibrointimallit
    cal_draws: int = 20  # kalibrointijoukon arvontoja per testi
    min_eval: int = 5  # kylmäkäynnistys: vähintään näin monta arvioitavaa mallia


def _rmse(e: np.ndarray) -> float:
    return float(np.sqrt(np.mean(e**2)))


def additive_baseline(train: pl.DataFrame, test: pl.DataFrame, ridge: float = 1e-4) -> np.ndarray:
    """1PL-tyyppinen vertailukohta: ŷ = c + (1 − c)·σ(μ + m_i + d_j), sovitettu samalla
    jatkuvalla BCE:llä ja leikkauksella kuin IRT-moottori (yhteinen erottelukyky sulautuu
    asteikkoon). Kevyt ridge m:lle ja d:lle (tunnistettavuus). Tuntematon malli/testi → 0."""
    models = sorted(train["model_id"].unique().to_list())
    benches = sorted(train["benchmark_id"].unique().to_list())
    mi = {m: k for k, m in enumerate(models)}
    bj = {b: k for k, b in enumerate(benches)}
    i = np.array([mi[m] for m in train["model_id"]])
    j = np.array([bj[b] for b in train["benchmark_id"]])
    y = np.clip(train["y"].to_numpy(), Y_CLIP, 1 - Y_CLIP)
    c = train["guessing_floor"].to_numpy()
    n_m, n_b = len(models), len(benches)

    def f(p):
        m, d = p[1 : 1 + n_m], p[1 + n_m :]
        s = 1 / (1 + np.exp(-(p[0] + m[i] + d[j])))
        q = np.clip(c + (1 - c) * s, 1e-9, 1 - 1e-9)
        loss = -np.sum(y * np.log(q) + (1 - y) * np.log(1 - q)) + ridge * (m @ m + d @ d)
        g = (q - y) / (q * (1 - q)) * (1 - c) * s * (1 - s)
        grad = np.concatenate([[g.sum()], np.bincount(i, g, n_m) + 2 * ridge * m,
                               np.bincount(j, g, n_b) + 2 * ridge * d])  # fmt: skip
        return loss, grad

    r = minimize(f, np.zeros(1 + n_m + n_b), jac=True, method="L-BFGS-B",
                 options={"maxiter": 5000})  # fmt: skip
    mu, m, d = r.x[0], r.x[1 : 1 + n_m], r.x[1 + n_m :]
    zt = (
        mu
        + np.array([m[mi[a]] if a in mi else 0.0 for a in test["model_id"]])
        + np.array([d[bj[b]] if b in bj else 0.0 for b in test["benchmark_id"]])
    )
    ct = test["guessing_floor"].to_numpy()
    return ct + (1 - ct) / (1 + np.exp(-zt))


def assign_folds(obs: pl.DataFrame, cfg: ValidationConfig) -> pl.DataFrame:
    """Sarake `fold` (0…folds−1). Havainto arvioidaan vain, jos sen malli ja testi jäävät
    opetusdataan vähintään karsintarajan verran (muuten fold = −1, ei arvioida)."""
    rng = np.random.default_rng([cfg.seed, 6])
    o = obs.sort("model_id", "benchmark_id")
    fold = rng.integers(0, cfg.folds, o.height)
    o = o.with_columns(pl.Series("fold", fold))
    # Jos mallilla tai testillä jäisi foldin poiston jälkeen liian vähän, rivi ei ole testirivi
    nm = o.group_by("model_id", "fold").len().rename({"len": "_nmf"})
    nb = o.group_by("benchmark_id", "fold").len().rename({"len": "_nbf"})
    o = o.join(nm, on=["model_id", "fold"]).join(nb, on=["benchmark_id", "fold"])
    o = o.with_columns(_nm=pl.len().over("model_id"), _nb=pl.len().over("benchmark_id"))
    ok = ((pl.col("_nm") - pl.col("_nmf")) >= cfg.min_model_obs) & (
        (pl.col("_nb") - pl.col("_nbf")) >= cfg.min_benchmark_obs
    )
    return o.with_columns(fold=pl.when(ok).then(pl.col("fold")).otherwise(-1)).drop(
        "_nmf", "_nbf", "_nm", "_nb"
    )


def holdout_cv(obs: pl.DataFrame, irt_cfg: IRTConfig, cfg: ValidationConfig) -> pl.DataFrame:
    """k-osainen ristiinvalidointi. Palauttaa testirivit sarakkein y, pred_irt,
    pred_additive, pred_bench_mean, fold ja irt_converged (foldin sovitus konvergoi)."""
    o = assign_folds(obs, cfg)
    fcfg = replace(irt_cfg, compute_se=False)
    out = []
    for f in range(cfg.folds):
        test = o.filter(pl.col("fold") == f)
        if test.is_empty():
            continue
        train = prune_sparse(o.filter(pl.col("fold") != f).drop("fold"),
                             cfg.min_model_obs, cfg.min_benchmark_obs).obs  # fmt: skip
        models = train["model_id"].unique().to_list()
        benches = train["benchmark_id"].unique().to_list()
        test = test.filter(pl.col("model_id").is_in(models) & pl.col("benchmark_id").is_in(benches))
        if test.is_empty():
            continue
        res = fit_irt(train, fcfg)
        bmean = dict(train.group_by("benchmark_id").agg(pl.col("y").mean()).iter_rows())
        out.append(test.with_columns(
            pred_irt=pl.Series(res.predict(test)),
            pred_additive=pl.Series(additive_baseline(train, test)),
            pred_bench_mean=pl.col("benchmark_id").replace_strict(bmean),
            irt_converged=pl.lit(bool(res.converged)),
        ))  # fmt: skip
    return pl.concat(out) if out else pl.DataFrame()


def _fit_item(
    theta: np.ndarray, y: np.ndarray, c: float, fix_a: float | None
) -> tuple[float, float, bool]:
    """Testin (a, b) sovitus tunnetuilla θ:illa jatkuvalla BCE:llä (kuten IRT-moottorissa).
    Priorit 0.01·b² ja 0.01·(ln a)² per testi ovat vahvempia kuin moottorissa (siellä
    keskiarvo testien yli), koska pisteitä on vain muutama. fix_a: vain b sovitetaan.
    Palauttaa (a, b, optimointi onnistui)."""
    yc = np.clip(y, Y_CLIP, 1 - Y_CLIP)

    def loss(p):
        b = p[0]
        a = fix_a if fix_a is not None else math.exp(p[1])
        q = np.clip(c + (1 - c) / (1 + np.exp(-a * (theta - b))), 1e-9, 1 - 1e-9)
        bce = -np.sum(yc * np.log(q) + (1 - yc) * np.log(1 - q))
        return bce + 0.01 * b**2 + (0.0 if fix_a is not None else 0.01 * p[1] ** 2)

    x0 = [float(np.median(theta))] + ([] if fix_a is not None else [0.0])
    r = minimize(loss, x0, method="L-BFGS-B")
    a = fix_a if fix_a is not None else math.exp(float(r.x[1]))
    return a, float(r.x[0]), bool(r.success)


def cold_start(obs: pl.DataFrame, irt_cfg: IRTConfig, cfg: ValidationConfig,
               benchmarks: list[str] | None = None) -> pl.DataFrame:  # fmt: skip
    """Testi kerrallaan pois (leave-one-benchmark-out). Palauttaa rivin per (testi, arvonta):
    benchmark_id, draw, n_eval, rmse_2pl, rmse_b_only, rmse_cal_mean, irt_converged,
    item_fit_ok. Arvonnat testikohtaisesta virrasta (ei riipu ajettavien testien joukosta)."""
    fcfg = replace(irt_cfg, compute_se=False)
    rows = []
    todo = benchmarks or sorted(obs["benchmark_id"].unique().to_list())
    for bid in todo:
        target = obs.filter(pl.col("benchmark_id") == bid)
        rest = prune_sparse(obs.filter(pl.col("benchmark_id") != bid),
                            cfg.min_model_obs, cfg.min_benchmark_obs).obs  # fmt: skip
        known = target.filter(pl.col("model_id").is_in(rest["model_id"].unique().to_list()))
        if known.height < cfg.n_cal + cfg.min_eval:
            continue
        rng = np.random.default_rng([cfg.seed, 7, zlib.crc32(bid.encode())])
        res = fit_irt(rest, fcfg)
        a_med = float(np.median(res.a))
        th = res.theta_by_model()
        t = np.array([th[m] for m in known["model_id"]])
        y = known["y"].to_numpy()
        c = float(known["guessing_floor"][0])
        for draw in range(cfg.cal_draws):
            cal = rng.choice(known.height, cfg.n_cal, replace=False)
            ev = np.setdiff1d(np.arange(known.height), cal)
            a2, b2, ok2 = _fit_item(t[cal], y[cal], c, None)
            a1, b1, ok1 = _fit_item(t[cal], y[cal], c, a_med)

            def pred(a, b, idx=ev, tt=t, cc=c):
                return cc + (1 - cc) / (1 + np.exp(-a * (tt[idx] - b)))

            rows.append({
                "benchmark_id": bid, "draw": draw, "n_eval": int(ev.size),
                "rmse_2pl": _rmse(y[ev] - pred(a2, b2)),
                "rmse_b_only": _rmse(y[ev] - pred(a1, b1)),
                "rmse_cal_mean": _rmse(y[ev] - y[cal].mean()),
                "irt_converged": bool(res.converged), "item_fit_ok": ok1 and ok2,
            })  # fmt: skip
    return pl.DataFrame(rows)


def _calibration(p: np.ndarray, y: np.ndarray) -> list[dict]:
    edges = np.quantile(p, np.linspace(0, 1, 11))
    bins = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, 9)
    return [
        {"bin": int(k), "pred_mean": float(p[bins == k].mean()),
         "obs_mean": float(y[bins == k].mean()), "n": int((bins == k).sum())}
        for k in range(10) if (bins == k).any()
    ]  # fmt: skip


def summarize_holdout(pred: pl.DataFrame, domains: pl.DataFrame | None = None) -> dict:
    """RMSE ja MAE menetelmittäin (kaikista havainnoista ja testikohtaisten RMSE:iden
    keskiarvona), osa-alueittain, desiilikalibraatio IRT:lle ja testin keskiarvolle sekä
    konvergoitumattomien foldien määrä."""
    y = pred["y"].to_numpy()

    def scores(df: pl.DataFrame) -> dict:
        yy = df["y"].to_numpy()
        return {k: {"rmse": _rmse(yy - df[c].to_numpy()),
                    "mae": float(np.mean(np.abs(yy - df[c].to_numpy())))}
                for k, c in METHODS.items()}  # fmt: skip

    overall = scores(pred)
    for k, c in METHODS.items():
        per_b = pred.group_by("benchmark_id").agg(
            ((pl.col("y") - pl.col(c)) ** 2).mean().sqrt().alias("r")
        )
        overall[k]["rmse_macro"] = float(per_b["r"].mean())
    out = {
        "n": pred.height, "n_models": pred["model_id"].n_unique(),
        "n_benchmarks": pred["benchmark_id"].n_unique(), "overall": overall,
        "n_folds": int(pred["fold"].n_unique()),
        "n_folds_not_converged": int(
            pred.filter(~pl.col("irt_converged"))["fold"].n_unique()
        ),
    }  # fmt: skip
    if domains is not None:
        d = pred.join(domains.select("benchmark_id", "domain"), on="benchmark_id", how="left")
        out["by_domain"] = {
            dom: {"n": g.height} | scores(g)
            for (dom,), g in sorted(d.group_by("domain"), key=lambda x: str(x[0]))
        }
    # Myös testin keskiarvo on yhdistetyssä aineistossa lähes kalibroitu (lokerot ryhmittelevät
    # testejä), joten desiilikalibraatio ei yksin todista erottelukykyä (katselmointi A36)
    out["calibration"] = _calibration(pred["pred_irt"].to_numpy(), y)
    out["calibration_bench_mean"] = _calibration(pred["pred_bench_mean"].to_numpy(), y)
    return out


def summarize_cold_start(cs: pl.DataFrame, n_total: int | None = None) -> dict:
    """Testikohtaiset mediaanit arvontojen yli ja niiden mediaani testien yli; lisäksi
    keskiarvo ja P90 kaikista arvonnoista (2PL:n häntä: kaksi parametria muutamasta pisteestä),
    osuudet sekä konvergenssi. n_total: testien kokonaismäärä (ohitetut raporttiin)."""
    if cs.is_empty():
        return {}
    per = cs.group_by("benchmark_id").agg(
        pl.col("rmse_2pl").median(), pl.col("rmse_b_only").median(),
        pl.col("rmse_cal_mean").median(), pl.col("n_eval").first(),
    ).sort("benchmark_id")  # fmt: skip
    keys = ("2pl", "b_only", "cal_mean")
    return {
        "n_benchmarks": per.height,
        "n_skipped": (n_total - per.height) if n_total is not None else 0,
        "n_draws": cs.height,
        "median_rmse": {k: float(per[f"rmse_{k}"].median()) for k in keys},
        "mean_rmse_draws": {k: float(cs[f"rmse_{k}"].mean()) for k in keys},
        "p90_rmse_draws": {k: float(cs[f"rmse_{k}"].quantile(0.9)) for k in keys},
        "share_b_only_better_than_mean": float((per["rmse_b_only"] < per["rmse_cal_mean"]).mean()),
        "share_2pl_better_than_mean": float((per["rmse_2pl"] < per["rmse_cal_mean"]).mean()),
        "share_draws_2pl_worse_than_b_only": float((cs["rmse_2pl"] > cs["rmse_b_only"]).mean()),
        "n_irt_not_converged": int(cs.filter(~pl.col("irt_converged"))["benchmark_id"].n_unique()),
        "n_item_fit_failed": int((~cs["item_fit_ok"]).sum()),
        "per_benchmark": per.to_dicts(),
    }


def _f(v, nd: int = 3) -> str:
    return "–" if v is None else f"{v:.{nd}f}"


def write_report(hold: dict, cold: dict, cfg: ValidationConfig, out, *, as_of, data_note: str):
    """docs/validointi.md. Vain Epochin datasta (CC BY 4.0) johdettuja lukuja, ei hintoja."""
    from pathlib import Path

    names = (("IRT (2PL)", "irt"), ("1PL-tyyppinen additiivinen malli", "additive"),
             ("testin keskiarvo", "bench_mean"))  # fmt: skip
    o = hold["overall"]
    bm_cal = hold.get("calibration_bench_mean") or []
    bm_gap = max((abs(c["pred_mean"] - c["obs_mean"]) for c in bm_cal), default=None)
    best = min(o, key=lambda k: o[k]["rmse"])
    verdict = (
        f"IRT ennusti parhaiten (RMSE {_f(o['irt']['rmse'])} vs. 1PL-tyyppinen "
        f"{_f(o['additive']['rmse'])} ja testin keskiarvo {_f(o['bench_mean']['rmse'])})."
        if best == "irt"
        else f"IRT ei ennustanut parhaiten: paras oli {dict((k, n) for n, k in names)[best]}."
    )
    lines = [
        "# θ-asteikon validointi (A36)",
        "",
        f"_Generoitu {as_of} komennolla `forecaster validate`. {data_note}_",
        "",
        "> **Lähteet.** Benchmark-data: [Epoch AI](https://epoch.ai/data), lisenssi "
        "[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Raportti on johdettu "
        "aineistosta: tulokset on normalisoitu, aggregoitu ja niihin on sovitettu IRT-malli.",
        "",
        "## Mitä tämä mittaa",
        "ECI-vertailu (A10) on toistettavuuden tarkistus, koska ECI on Epochin IRT-tyyppinen "
        "malli pääosin samasta datasta. Tässä mitataan, ennustaako θ-asteikko tuloksia, joita "
        "sovitus ei nähnyt. Mittarit ovat normalisoidulla asteikolla (tulos suhteessa testin "
        f"kattoon, 0–1). {verdict} Validointi on aineiston sisäinen: se kertoo, kuinka hyvin "
        "yksi θ per malli ja kaksi parametria per testi selittävät havaitut tulokset, mutta ei "
        "sitä, että θ mittaisi yleistä kyvykkyyttä datan ulkopuolella.",
        "",
        "Rajoitukset: piilotetut tulokset ovat satunnaisotos havaituista, vaikka todellinen "
        "puuttuvuus ei ole satunnaista (todennäköisesti koska testejä ajetaan valikoiden). "
        "Poikkeamat, karsinta ja ankkurit on päätetty koko datalla, mikä voi tehdä tuloksesta "
        "hieman optimistisen. Kylmäkäynnistyksen kalibrointimallit arvotaan satunnaisesti koko "
        "θ-alueelta; uusia testejä ajetaan tyypillisesti ensin kärkimalleille, joten "
        "kylmäkäynnistyksen tulos on todennäköisesti optimistinen (hypoteesi).",
        "",
        f"## 1. Pois jätetyt tulokset ({cfg.folds}-osainen ristiinvalidointi)",
        f"Havainnot jaetaan satunnaisesti {cfg.folds} osaan; kukin osa ennustetaan muilla "
        "sovitetulla mallilla. Havaintoa ei arvioida, jos mallille tai testille jäisi alle "
        f"karsintarajan ({cfg.min_model_obs} / {cfg.min_benchmark_obs}) opetushavaintoja. "
        f"Arvioituja havaintoja {hold['n']} ({hold['n_models']} mallia, "
        f"{hold['n_benchmarks']} testiä). Vertailukohdat sovitetaan samoihin opetushavaintoihin: "
        "1PL-tyyppinen malli = c + (1 − c)·σ(μ + m_i + d_j) samalla BCE-häviöllä ja leikkauksella "
        "kuin IRT, mutta yhteisellä erottelukyvyllä (kevyt ridge); testin keskiarvo = ei tietoa "
        "mallista. IRT:n ja 1PL:n ero mittaa siis testikohtaisen erottelukyvyn hyötyä (IRT:n "
        "priorit ja optimointi eroavat hieman). RMSE (testit) = testikohtaisten RMSE:iden "
        f"keskiarvo. Konvergoitumattomia IRT-sovituksia {hold.get('n_folds_not_converged', 0)} / "
        f"{hold.get('n_folds', cfg.folds)}.",
        "",
        "| menetelmä | RMSE | RMSE (testit) | MAE |",
        "|---|---|---|---|",
        *[
            f"| {n} | {_f(o[k]['rmse'])} | {_f(o[k].get('rmse_macro'))} | {_f(o[k]['mae'])} |"
            for n, k in names
        ],
        "",
    ]
    if hold.get("by_domain"):
        lines += [
            "Osa-alueittain (RMSE):",
            "",
            "| osa-alue | n | IRT | 1PL-tyyppinen | testin keskiarvo |",
            "|---|---|---|---|---|",
            *[f"| {d} | {v['n']} | {_f(v['irt']['rmse'])} | {_f(v['additive']['rmse'])} | "
              f"{_f(v['bench_mean']['rmse'])} |" for d, v in hold["by_domain"].items()],
            "",
        ]  # fmt: skip
    lines += [
        "Pisteennusteen osuvuus desiileittäin: ennusteen desiilit, ennusteen ja havainnon "
        f"keskiarvo. Myös testin keskiarvon suurin desiiliero on vain {_f(bm_gap, 2)} (lokerot "
        "ryhmittelevät testejä), joten taulukko ei yksin todista erottelukykyä; "
        "ennustevälejä se ei mittaa.",
        "",
        "| desiili | IRT: ennuste | havaittu | testin keskiarvo: ennuste | havaittu |",
        "|---|---|---|---|---|",
        *[f"| {c['bin'] + 1} | {_f(c['pred_mean'], 2)} | {_f(c['obs_mean'], 2)} | "
          f"{_f(b['pred_mean'], 2)} | {_f(b['obs_mean'], 2)} |"
          for c, b in zip(hold["calibration"], hold.get("calibration_bench_mean",
                                                          hold["calibration"]), strict=False)],
        "",
        f"## 2. Uuden testin kylmäkäynnistys ({cfg.n_cal} kalibrointimallia)",
    ]  # fmt: skip
    if cold:
        m = cold["median_rmse"]
        lines += [
            "Testi kerrallaan pois: IRT sovitetaan ilman sitä, ja testin parametrit sovitetaan "
            f"{cfg.n_cal} satunnaisen mallin tuloksiin (θ tunnettu; {cfg.cal_draws} arvontaa). "
            "Muiden mallien tulokset ennustetaan. `vain b` = erottelukyky kiinnitetty "
            "muiden testien mediaaniin. Vertailukohta: kalibrointimallien keskiarvo. "
            "Testikohtaiset luvut ovat mediaaneja arvontojen yli. Testit, joilla on alle "
            f"{cfg.n_cal + cfg.min_eval} tunnetun θ:n mallia, ohitetaan "
            f"({cold.get('n_skipped', 0)} kpl); pienen `arvioitu`-määrän rivit ovat kohinaisia.",
            "",
            f"Testejä {cold['n_benchmarks']}, arvontoja {cold.get('n_draws', '–')}. "
            f"2PL parempi kuin keskiarvo {_f(cold['share_2pl_better_than_mean'], 2)} testeistä, "
            f"vain b {_f(cold['share_b_only_better_than_mean'], 2)}. 2PL huonompi kuin vain b "
            f"{_f(cold.get('share_draws_2pl_worse_than_b_only'), 2)} arvonnoista (todennäköisesti "
            f"koska kaksi parametria estimoidaan {cfg.n_cal} pisteestä; hypoteesi). "
            "Konvergoitumattomia IRT-sovituksia "
            f"{cold.get('n_irt_not_converged', 0)}, epäonnistuneita testin sovituksia "
            f"{cold.get('n_item_fit_failed', 0)}.",
            "",
            "| RMSE | 2PL | vain b | kalibrointimallien keskiarvo |",
            "|---|---|---|---|",
            f"| testikohtaisten mediaanien mediaani | {_f(m['2pl'])} | {_f(m['b_only'])} | "
            f"{_f(m['cal_mean'])} |",
            *[f"| {label} kaikista arvonnoista | {_f(cold[key]['2pl'])} | "
              f"{_f(cold[key]['b_only'])} | {_f(cold[key]['cal_mean'])} |"
              for label, key in (("keskiarvo", "mean_rmse_draws"), ("P90", "p90_rmse_draws"))
              if key in cold],
            "",
            "| testi | arvioitu | 2PL | vain b | keskiarvo |",
            "|---|---|---|---|---|",
            *[f"| {r['benchmark_id']} | {r['n_eval']} | {_f(r['rmse_2pl'])} | "
              f"{_f(r['rmse_b_only'])} | {_f(r['rmse_cal_mean'])} |"
              for r in cold["per_benchmark"]],
        ]  # fmt: skip
    else:
        lines.append("Ei ajettu (`--no-cold-start`).")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
