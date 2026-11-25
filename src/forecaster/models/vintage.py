"""6.2 (A35): ajankohtaan sidottu backtest.

Nykyinen backtest (A24) käyttää nykyisen IRT-ajon θ:ita, jotka on sovitettu myös
lähtöpäivän jälkeen julkaistulla datalla: tulevaisuuteen katsominen tekee siitä
todennäköisesti optimistisen. Tässä IRT sovitetaan jokaiselle lähtöpäivälle uudelleen vain
niillä tuloksilla, jotka olivat julkaistuja lähtöpäivänä (Epochin arkistoitu tilannekuva),
ja ennustetta verrataan samoihin toteutuneisiin rintamapisteisiin kuin nykyisessä
backtestissä. Molemmat lasketaan rinnakkain, jolloin ero kertoo tulevaisuuteen katsomisen
vaikutuksen.

Toteuma arvioidaan nykyisen IRT-ajon θ:lla (paras tieto toteutuneesta tasosta). Asteikko on
ankkuroitu, mutta sen taso voi siirtyä ajojen välillä; herkkyytenä virhe lasketaan myös
robustilla tasosiirtymällä (vakiintuneiden mallien θ-muutoksen mediaani, `level_shift`).
Kypsät toteumat (malli julkaistu viimeistään as_of − 3 kk) raportoidaan erikseen, koska
tuoreimpien mallien θ perustuu vielä harvoihin tuloksiin.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from forecaster.db.repository import Repository
from forecaster.ingest.epoch_ai import SOURCE as ARCHIVED_SOURCE
from forecaster.ingest.epoch_ai import FileSpec
from forecaster.ingest.epoch_archive import Snapshot, presence, snapshot_for
from forecaster.models.forecaster import ForecastConfig, add_months, capability_forecast
from forecaster.models.forecaster import frontier_mask as _frontier_mask
from forecaster.models.irt_engine import IRTConfig
from forecaster.models.pipeline import PipelineConfig, fit_overall


def vintage_eval_ids(keys: pl.DataFrame, present: set[tuple[str, str]], cutoff: date) -> list[str]:
    """Lähtöpäivänä julkaistut tulokset.

    keys: Repository.get_evaluation_keys(). Arkistoidun lähteen (Epoch) tulos on mukana, jos
    (benchmark_id, malliversio) oli tilannekuvassa. Muiden lähteiden tulos vain, jos sen
    arviointipäivä on tiedossa ja viimeistään lähtöpäivänä. Mallin on oltava julkaistu.
    """
    present_df = pl.DataFrame(
        list(present), schema={"benchmark_id": pl.String, "model_version": pl.String},
        orient="row",
    ).with_columns(_present=pl.lit(True))  # fmt: skip
    k = keys.filter(pl.col("release_date") <= cutoff).join(
        present_df, on=["benchmark_id", "model_version"], how="left"
    )
    ok = (
        pl.when(pl.col("source_name") == ARCHIVED_SOURCE)
        .then(pl.col("_present").fill_null(False))
        .otherwise(pl.col("eval_date").is_not_null() & (pl.col("eval_date") <= cutoff))
    )
    return k.filter(ok)["eval_id"].sort().to_list()


def vintage_scores(
    repo: Repository, eval_ids: list[str], irt_cfg: IRTConfig, pcfg: PipelineConfig
) -> tuple[pl.DataFrame, dict]:
    """IRT-sovitus (sama putki kuin tuotannossa, ei tallennusta) annetuilla tuloksilla."""
    raw = repo.get_observation_matrix("overall", exclude_outliers=False, eval_ids=eval_ids)
    fit = fit_overall(raw, irt_cfg, pcfg)
    res = fit.result
    theta, se = res.theta_by_model(), res.theta_se_by_model()
    rel = dict(repo.get_evaluation_keys().select("model_id", "release_date").unique().iter_rows())
    scores = pl.DataFrame(
        [(m, rel[m], float(t), float(se.get(m, np.nan))) for m, t in theta.items()],
        schema=["model_id", "release_date", "theta", "theta_se"], orient="row",
    ).sort("release_date", "model_id")  # fmt: skip
    info = {
        "n_models": int(fit.obs["model_id"].n_unique()),
        "n_benchmarks": int(fit.obs["benchmark_id"].n_unique()),
        "n_observations": fit.obs.height,
        "n_excluded_outliers": len(fit.excluded),
        "missing_anchors": list(res.missing_anchors),
    }
    return scores, info


METHODS = ("vintage", "vintage_level_adjusted", "lookahead")


def _metrics(y: np.ndarray, bands: np.ndarray) -> dict:
    err = y - bands[:, 1]
    return {
        "share_inside_p10_p90": float(np.mean((bands[:, 0] <= y) & (y <= bands[:, 2]))),
        "mean_error": float(np.mean(err)),
        "mean_abs_error": float(np.mean(np.abs(err))),
        "mean_width_p10_p90": float(np.mean(bands[:, 2] - bands[:, 0])),
    }


def level_shift(
    vintage: pl.DataFrame,
    current: pl.DataFrame,
    cutoff: date,
    *,
    max_se: float = 0.3,
    min_age_months: int = 12,
    min_models: int = 10,
) -> float | None:
    """Asteikon robusti tasosiirtymä: mediaani (nykyinen θ − lähtöpäivän θ) vakiintuneista
    malleista (pieni SE, julkaistu yli vuosi ennen lähtöpäivää).

    Ei rintaman parhaan mallin muutosta (katselmointi A35): se mittaisi tuoreimman mallin
    arvion korjautumista (valintaharha), jonka backtestin pitää nimenomaan mitata."""
    old = vintage.filter(
        (pl.col("theta_se") < max_se)
        & (pl.col("release_date") <= add_months(cutoff, -min_age_months))
    ).join(current.select("model_id", pl.col("theta").alias("now")), on="model_id")
    if old.height < min_models:
        return None
    return float((old["now"] - old["theta"]).median())


def evaluate_origin(
    vintage: pl.DataFrame,
    current: pl.DataFrame,
    cfg: ForecastConfig,
    cutoff: date,
    end: date,
    max_theta_se: float,
    mature_before: date | None = None,
) -> dict:
    """Ajankohtaan sidottu ja nykyisillä θ:illa laskettu backtest samoille toteumille.

    vintage: lähtöpäivän IRT-ajon θ (model_id, release_date, theta, theta_se).
    current: nykyisen IRT-ajon θ kaikille malleille (toteumat ja vertailu).
    mature_before: toteuma on "kypsä", jos malli on julkaistu viimeistään tänä päivänä
    (tuoreen mallin θ perustuu vielä harvoihin tuloksiin ja muuttuu tyypillisesti)."""
    current = current.filter(pl.col("theta_se") <= max_theta_se)
    rel = current["release_date"].to_numpy().astype("datetime64[D]")
    fm = _frontier_mask(rel, current["theta"].to_numpy().astype(float))
    after = fm & (rel > np.datetime64(cutoff, "D")) & (rel <= np.datetime64(end, "D"))
    realized = current.filter(pl.Series(after)).select("model_id", "release_date", "theta")
    out: dict = {"cutoff": cutoff, "end": end, "n_points": realized.height}
    if not realized.height:
        return out | {"status": "no_points"}
    dates, y = realized["release_date"].to_list(), realized["theta"].to_numpy()
    bt_cfg = replace(cfg, cost_targets={})

    def run(scores: pl.DataFrame) -> dict:
        sc = scores.filter(
            (pl.col("release_date") <= cutoff) & (pl.col("theta_se") <= max_theta_se)
        )
        fc = capability_forecast(sc, bt_cfg, cutoff, eval_dates=dates)
        bands = np.array([e["prediction_p10_p50_p90"] for e in fc["eval"]], dtype=float)
        return {"fc": fc, "bands": bands}

    try:
        v = run(vintage)
        c = run(current)
    except ValueError as exc:
        return out | {"status": "insufficient", "note": str(exc)}
    shift = level_shift(vintage, current, cutoff)
    # Herkkyys: toteumat lähtöpäivän asteikolle (nykyinen = lähtöpäivän + shift)
    y_adj = y - (shift or 0.0)
    targets = {"vintage": (y, v["bands"]), "vintage_level_adjusted": (y_adj, v["bands"]),
               "lookahead": (y, c["bands"])}  # fmt: skip
    points = []
    for i, (m, d, t) in enumerate(realized.iter_rows()):
        p = {"model_id": m, "date": d, "theta": float(t),
             "mature": mature_before is None or d <= mature_before}  # fmt: skip
        for key, (yy, b) in targets.items():
            p[key] = {"band": b[i].tolist(), "error": float(yy[i] - b[i, 1]),
                      "inside": bool(b[i, 0] <= yy[i] <= b[i, 2]),
                      "width": float(b[i, 2] - b[i, 0])}  # fmt: skip
        points.append(p)
    return out | {
        "status": "ok",
        "vintage_n_frontier": v["fc"]["n_frontier"],
        "vintage_slope_per_year": v["fc"]["slope_per_year"],
        "vintage_frontier_theta": v["fc"]["current_frontier_theta"],
        "lookahead_slope_per_year": c["fc"]["slope_per_year"],
        "lookahead_n_frontier": c["fc"]["n_frontier"],
        "level_shift": shift,
        **{key: _metrics(yy, b) for key, (yy, b) in targets.items()},
        "points": points,
    }


def _f(v, nd: int = 2, sign: bool = False) -> str:
    if v is None:
        return "–"
    return f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"


def _summary_rows(res: dict, suffix: str) -> list[str]:
    names = (("ajankohtaan sidottu", "vintage"),
             ("– tasosiirtymällä korjattu (herkkyys)", "vintage_level_adjusted"),
             ("nykyisillä θ:illa (tavallinen)", "lookahead"))  # fmt: skip
    rows = []
    for name, key in names:
        a = res.get(key + suffix) or {}
        mm = a.get("origin_share_min_max") or [None, None]
        rows.append(
            f"| {name} | {_f(a.get('mean_origin_share'))} ± {_f(a.get('mean_origin_share_se'))} "
            f"({_f(mm[0])}–{_f(mm[1])}) | {_f(a.get('pooled_share'))} | "
            f"{a.get('n_points', 0)} / {a.get('n_unique_models', 0)} | "
            f"{_f(a.get('mean_error'), sign=True)} | {_f(a.get('mean_abs_error'))} | "
            f"{_f(a.get('mean_width_p10_p90'))} |"
        )
    return rows


def write_report(res: dict, out: Path, *, irt_run: str, method_version: str) -> Path:
    """docs/backtest_ajankohta.md. Sisältää vain Epochin datasta (CC BY 4.0) johdettuja
    θ-lukuja, ei hintoja."""
    ok = [o for o in res["origins"] if o.get("status") == "ok"]
    lags = [(o["cutoff"] - o["snapshot"]).days for o in ok]
    if not lags:
        lag_text = "tilannekuvia ei ollut"
    elif max(lags) == 0:
        lag_text = "lähtöpäivät ovat tilannekuvien päiviä (data ei ole vanhentunutta)"
    else:
        lag_text = (f"tilannekuva on {min(lags)}–{max(lags)} pv lähtöpäivää vanhempi, joten "
                    "sovituksesta voi puuttua jo julkaistuja tuloksia")  # fmt: skip
    head = ("| | välin sisällä, lähtöpäivien ka. ± SE (min–max) | välin sisällä, pisteistä | "
            "pisteitä / eri malleja | virhe θ | itseisvirhe θ | välin leveys θ |")  # fmt: skip
    sep = "|---|---|---|---|---|---|---|"
    lines = [
        "# Ajankohtaan sidottu backtest (A35)",
        "",
        f"_Generoitu {res['as_of']} komennolla `forecaster backtest-vintage`. Toteumien θ: "
        f"IRT-ajo `{irt_run}`. Menetelmäversio {method_version}._",
        "",
        "> **Lähteet.** Benchmark-data: [Epoch AI](https://epoch.ai/data), lisenssi "
        "[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); vanhat datapaketit "
        "[Internet Archivesta](https://web.archive.org/). Raportti on johdettu aineistosta: "
        "tulokset on normalisoitu, aggregoitu ja niihin on sovitettu IRT-malli.",
        "",
        "## Mitä tämä mittaa",
        "Tavallinen backtest käyttää nykyisen IRT-ajon θ-arvoja, jotka on sovitettu myös "
        "lähtöpäivän jälkeen julkaistulla datalla. Tässä IRT sovitetaan jokaiselle lähtöpäivälle "
        "uudelleen vain niillä tuloksilla, jotka olivat Epochin datapaketissa (arkistoitu "
        "tilannekuva viimeistään lähtöpäivänä; lähtöpäivät ovat tilannekuvien päiviä), ja "
        "ennustetta verrataan samoihin toteutuneisiin rintamapisteisiin enintään "
        f"{res['holdout_months']} kk eteenpäin. **Ajankohtaan sidottu** = likimääräinen "
        "etukäteistilanne (päätulos); **nykyisillä θ:illa** = tavallinen backtest samoilla "
        "lähtöpäivillä. Toteumat arvioidaan nykyisen IRT-ajon θ:lla. **Virhe** = toteuma − P50: "
        "positiivinen = ennuste oli toteumaa matalampi. **Tasosiirtymällä korjattu** on "
        "herkkyystarkastelu: toteumat siirretään lähtöpäivän asteikolle vakiintuneiden mallien "
        "(SE < 0.3, julkaistu yli vuosi ennen lähtöpäivää) θ-muutoksen mediaanilla.",
        "",
        "**Kypsät toteumat** = mallit, jotka on julkaistu viimeistään "
        f"{res['mature_before']}. Tuoreimpien mallien θ perustuu vielä harvoihin tuloksiin, joten "
        "niiden toteumat ovat epävarmoja; hypoteesina rintamamallin θ voi olla valintaharhan "
        "vuoksi yläkanttiin (vrt. A31). Kypsät toteumat ovat myös lyhyemmän horisontin ja "
        "aiemman jakson pisteitä, joten ero kypsien ja kaikkien välillä ei erota näitä syitä.",
        "",
        f"Rajoitukset: {lag_text}. Tilannekuvasta käytetään vain tietoa siitä, mitkä tulokset "
        "olivat julkaistuja; pistemäärät, mallien julkaisupäivät, versioiden ryhmittely ja "
        "aliakset, testien valinta ja arvausrajat sekä ankkurit ovat nykyisestä datasta. "
        "Menetelmän asetukset (A27, A28, A33) on valittu tämän jakson backtestien perusteella, "
        "joten asetelma ei ole menetelmän valinnan kannalta otoksen ulkopuolinen. Lähtöpäivät "
        "arvioivat osin samoja malleja, joten tehokas otoskoko on enintään eri mallien määrä, ja "
        "lähtöpäivien SE on alaraja.",
        "",
        "## Yhteenveto",
        f"Lähtöpäiviä {res['n_origins_ok']}. Odotus välin sisällä ≈ 0.80.",
        "",
        "Kaikki toteumat:",
        "",
        head,
        sep,
        *_summary_rows(res, ""),
        "",
        f"Kypsät toteumat (julkaistu viimeistään {res['mature_before']}):",
        "",
        head,
        sep,
        *_summary_rows(res, "_mature"),
        "",
        "## Lähtöpäivittäin",
        "",
        "| lähtöpäivä | tilannekuva | tuloksia | malleja | rintama | θ/v | θ/v nyk. | "
        "tasosiirtymä | pisteitä | sisällä | sisällä nyk. | virhe | virhe nyk. |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for o in res["origins"]:
        if o.get("status") != "ok":
            lines.append(f"| {o['cutoff']} | {o.get('snapshot', '–')} | "
                         f"{o.get('n_vintage_results', '–')} | {o['status']} "
                         + "| " * 9)  # fmt: skip
            continue
        v, c = o["vintage"], o["lookahead"]
        lines.append(
            f"| {o['cutoff']} | {o['snapshot']} | {o['n_vintage_results']} | "
            f"{o['fit']['n_models']} | {o['vintage_n_frontier']} | "
            f"{_f(o['vintage_slope_per_year'])} | {_f(o['lookahead_slope_per_year'])} | "
            f"{_f(o['level_shift'], sign=True)} | {o['n_points']} | "
            f"{_f(v['share_inside_p10_p90'])} | {_f(c['share_inside_p10_p90'])} | "
            f"{_f(v['mean_error'], sign=True)} | {_f(c['mean_error'], sign=True)} |"
        )
    lines += [
        "",
        "`rintama` ja `θ/v` = lähtöpäivän IRT-ajon rintamapisteet ja kehitysvauhti; `nyk.` = "
        "sama nykyisillä θ:illa; `tasosiirtymä` = vakiintuneiden mallien θ-muutoksen mediaani "
        "(nykyinen − lähtöpäivän).",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _aggregate(origins: list[dict], key: str, mature_only: bool = False) -> dict:
    """Samat tunnusluvut kuin liukuvassa backtestissä (A24): lähtöpäivien keskiarvo ± SE
    (alaraja, lähtöpäivät korreloivat) ja vaihteluväli, pisteistä laskettu osuus sekä
    eri toteumamallien määrä (lähtöpäivät arvioivat osin samoja malleja, joten tehokas
    otoskoko on enintään n_unique_models). Virheet ja leveys pisteistä."""
    per_origin = []
    for o in origins:
        if o.get("status") != "ok":
            continue
        pts = [p for p in o["points"] if p["mature"] or not mature_only]
        if pts:
            per_origin.append(pts)
    pts = [p for g in per_origin for p in g]
    if not pts:
        return {}
    shares = [float(np.mean([p[key]["inside"] for p in g])) for g in per_origin]
    return {
        "n_origins": len(shares),
        "mean_origin_share": float(np.mean(shares)),
        "mean_origin_share_se": (
            float(np.std(shares, ddof=1) / np.sqrt(len(shares))) if len(shares) > 1 else None
        ),
        "origin_share_min_max": [float(min(shares)), float(max(shares))],
        "pooled_share": float(np.mean([p[key]["inside"] for p in pts])),
        "n_points": len(pts),
        "n_unique_models": len({p["model_id"] for p in pts}),
        "mean_error": float(np.mean([p[key]["error"] for p in pts])),
        "mean_abs_error": float(np.mean([abs(p[key]["error"]) for p in pts])),
        "mean_width_p10_p90": float(np.mean([p[key]["width"] for p in pts])),
    }


def vintage_backtest(
    repo: Repository,
    snapshots: list[Snapshot],
    specs: list[FileSpec],
    irt_cfg: IRTConfig,
    fcfg: ForecastConfig,
    as_of: date,
    current: pl.DataFrame,
    *,
    cutoffs: list[date],
    holdout_months: int = 12,
    max_theta_se: float = 1.0,
    mature_months: int = 3,
    pcfg: PipelineConfig | None = None,
) -> dict:
    """Ajankohtaan sidottu liukuva backtest. Lähtöpäiviksi kannattaa antaa tilannekuvien
    päivät (`snapshot_cutoffs`): muuten tilannekuva voi olla viikkoja vanha ja sama data
    toistua usealla lähtöpäivällä (katselmointi A35). Toteumat lähtöpäivästä enintään
    holdout_months eteenpäin (≤ as_of). Kypsät toteumat: julkaistu viimeistään
    as_of − mature_months (Epochin arviointiviiveen mediaani ~2 kk)."""
    pcfg = pcfg or PipelineConfig()
    keys = repo.get_evaluation_keys()
    mature_before = add_months(as_of, -mature_months)
    cache: dict[str, set] = {}
    origins = []
    for cutoff in sorted(set(cutoffs)):
        end = min(as_of, add_months(cutoff, holdout_months))
        cfg = replace(fcfg, horizons_months=(holdout_months,))
        entry: dict = {"cutoff": cutoff, "end": end}
        snap = snapshot_for(snapshots, cutoff)
        if snap is None:
            origins.append(entry | {"status": "no_snapshot"})
            continue
        if snap.timestamp not in cache:
            cache[snap.timestamp] = presence(snap.path, specs)
        ids = vintage_eval_ids(keys, cache[snap.timestamp], cutoff)
        entry |= {"snapshot": snap.day, "n_vintage_results": len(ids)}
        try:
            vintage, info = vintage_scores(repo, ids, irt_cfg, pcfg)
        except ValueError as exc:
            origins.append(entry | {"status": "fit_failed", "note": str(exc)})
            continue
        entry["fit"] = info
        origins.append(entry | evaluate_origin(vintage, current, cfg, cutoff, end,
                                               max_theta_se, mature_before))  # fmt: skip
    return {
        "as_of": as_of,
        "holdout_months": holdout_months,
        "mature_before": mature_before,
        "n_origins_ok": sum(o.get("status") == "ok" for o in origins),
        **{key: _aggregate(origins, key) for key in METHODS},
        **{f"{key}_mature": _aggregate(origins, key, mature_only=True) for key in METHODS},
        "origins": origins,
    }


def snapshot_cutoffs(
    snapshots: list[Snapshot], as_of: date, min_lead_months: int = 1
) -> list[date]:
    """Lähtöpäiviksi tilannekuvien päivät, joista on vähintään min_lead_months as_ofiin."""
    last = add_months(as_of, -min_lead_months)
    return sorted({s.day for s in snapshots if s.day <= last})
