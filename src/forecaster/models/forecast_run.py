"""K5: ennusteajon orkestrointi, tallennus (forecast_runs) ja raportti (docs/ennusteet.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import polars as pl

from forecaster.config import ForecastFile, Paths
from forecaster.db.repository import Repository
from forecaster.models.forecaster import (
    ForecastConfig,
    backtest_capability,
    backtest_cost,
    capability_forecast,
    cost_forecast,
    month_starts,
)


@dataclass
class ForecastResults:
    run_id: str
    irt_run_id: str
    as_of: date
    capability: dict
    costs: dict[str, dict] = field(default_factory=dict)
    backtest_capability: dict | None = None
    backtest_costs: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def run_forecasts(repo: Repository, ff: ForecastFile, as_of: date | None = None) -> ForecastResults:
    as_of = as_of or date.today()
    irt_run = repo.latest_run_id("overall")
    if irt_run is None:
        raise ValueError("IRT-ajoa ei ole – aja ensin 'forecaster fit'")
    # Vain as_of-päivään mennessä julkaistut mallit (ei tulevan datan vuotoa, jos as_of < tänään)
    all_scores = repo.get_capability_with_dates(irt_run).filter(pl.col("release_date") <= as_of)
    unreliable = all_scores.filter(pl.col("theta_se") > ff.max_theta_se)
    scores = all_scores.filter(pl.col("theta_se") <= ff.max_theta_se)
    cfg = ForecastConfig(
        fit_start=ff.fit_start,
        horizons_months=tuple(ff.horizons_months),
        n_boot=ff.n_boot,
        seed=ff.seed,
        piecewise_window_months=ff.piecewise_window_months,
        piecewise_alpha=ff.piecewise_alpha,
    )
    run_id = f"fc_{as_of:%Y%m%d}_{irt_run[-8:]}"
    res = ForecastResults(run_id, irt_run, as_of, capability_forecast(scores, cfg, as_of))
    if unreliable.height:
        res.warnings.append(
            f"{unreliable.height} mallia jätetty pois, θ-SE > {ff.max_theta_se} "
            "(lattiatason tulokset, θ heikosti identifioitu): "
            + ", ".join(
                f"{m} (θ {t:.1f} ± {s:.1f})"
                for m, t, s in unreliable.select("model_id", "theta", "theta_se").iter_rows()
            )  # fmt: skip
        )
    res.backtest_capability = backtest_capability(scores, cfg, as_of, ff.backtest_holdout_months)

    prices = repo.get_price_at_dates(month_starts(ff.fit_start, as_of))
    theta = dict(scores.select("model_id", "theta").iter_rows())
    for t in ff.cost_targets:
        if t.model_id not in theta:
            res.warnings.append(f"Kustannustason viitemalli {t.model_id} puuttuu IRT-ajosta")
            continue
        level = theta[t.model_id]
        fc = cost_forecast(scores, prices, level, cfg, as_of)
        res.costs[t.label] = fc | {"reference_model": t.model_id}
        res.backtest_costs[t.label] = backtest_cost(
            scores, prices, level, cfg, as_of, ff.backtest_holdout_months
        )

    params = ff.model_dump()
    with repo.transaction():
        repo._run("DELETE FROM forecast_runs WHERE run_id LIKE ?", [run_id + "%"])
        repo.save_forecast(run_id + ":capability", irt_run, "capability", params, res.capability)
        repo.save_forecast(run_id + ":backtest_capability", irt_run, "backtest",
                           params, res.backtest_capability)  # fmt: skip
        for label, fc in res.costs.items():
            repo.save_forecast(f"{run_id}:cost:{label}", irt_run, "cost", params, fc)
            repo.save_forecast(f"{run_id}:backtest_cost:{label}", irt_run, "backtest",
                               params, res.backtest_costs[label])  # fmt: skip
    return res


# --- Raportti -------------------------------------------------------------------------


def _f(v, nd=2) -> str:
    return "–" if v is None else f"{v:.{nd}f}"


def _hl(v) -> str:
    """Puoliintumisajan persentiili: None = ääretön (hinta ei laske tässä drawien osassa)."""
    return "∞ (ei laske)" if v is None else f"{v:.1f}"


def _band(b, sig: bool = False) -> str:
    if not b or b[0] is None:
        return "–"
    fmt = (lambda v: f"{v:.3g}") if sig else (lambda v: f"{v:.2f}")
    return f"{fmt(b[1])} [{fmt(b[0])} – {fmt(b[2])}]"


def write_report(res: ForecastResults, paths: Paths, out: Path | None = None) -> Path:
    out = out or paths.root / "docs" / "ennusteet.md"
    c = res.capability
    lines = [
        "# Ennusteet",
        "",
        f"_Generoitu {res.as_of} komennolla `forecaster forecast`. IRT-ajo `{res.irt_run_id}`, "
        f"ennusteajo `{res.run_id}`._",
        "",
        "> **Lähteet ja rajoitukset.** Benchmark-data: Epoch AI (CC BY 4.0). Hinnat: "
        "simonw/llm-prices (ei lisenssiä – vain omaan käyttöön) ja OpenRouter; mallit ilman "
        "hintahistoriaa saavat OpenRouterin nykyhinnan julkaisupäivästä alkaen (oletus: hinta "
        "ei muuttunut → vanhat hinnat voivat olla aliarvioita). Rintamassa on vähän pisteitä, "
        "joten välit ovat leveitä – älä tulkitse P50:tä varmana.",
        "",
        "## 1. Kyvykkyysrintama (määrittely 5.1)",
        f"- Sovitus {c['fit_start']} alkaen, {c['n_frontier']} rintamapistettä, malli: "
        f"**{c['model']}**",
        f"- Kulmakerroin: **{_f(c['slope_per_year'])} θ/vuosi** "
        f"(bootstrap P10–P90 {_f(c['slope_per_year_p10_p50_p90'][0])} – "
        f"{_f(c['slope_per_year_p10_p50_p90'][2])})",
        f"- Palaisuustesti (viim. 12 kk): p = {_f(c['piecewise_p_value'], 3)}, "
        f"viimeaikainen kulmakerroin {_f(c['recent_slope_per_year'])} θ/vuosi "
        f"(koko ikkunan lineaarinen {_f(c['linear_slope_per_year'])})",
        f"- Mallivalinta tehdään jokaisessa bootstrap-drawissa uudelleen: palainen malli "
        f"valittiin {_f(c['share_piecewise_draws'])} drawista – välit sisältävät "
        "rakenne-epävarmuuden (lineaarinen vs. kiihtyvä)",
        f"- Nykyinen rintama: θ = {_f(c['current_frontier_theta'])}",
        "",
        "`trend` = trendisuoran epävarmuus; `prediction` = väli yksittäiselle tulevalle "
        "rintamapisteelle (sisältää jäännöskohinan). Muoto: P50 [P10 – P90].",
        "",
        "| horisontti | päivä | trend | prediction |",
        "|---|---|---|---|",
        *[
            f"| {h['months']} kk | {h['date']} | {_band(h['trend_p10_p50_p90'])} | "
            f"{_band(h['prediction_p10_p50_p90'])} |"
            for h in c["horizons"]
        ],
        "",
        f"Rintamamallit: {', '.join(c['frontier_models'])}",
    ]

    bt = res.backtest_capability
    if bt:
        lines += [
            "",
            "### Backtest (kyvykkyys)",
            f"Sovitus vain {bt['cutoff']} asti julkaistuilla malleilla (malli {bt['trained_model']}, "
            f"{_f(bt['trained_slope_per_year'])} θ/v), ennuste jokaiselle sen jälkeen "
            f"toteutuneelle rintamapisteelle sen julkaisupäivänä.",
            f"- Toteutuneita rintamapisteitä {bt['n_realized_frontier_points']}, ennustevälin "
            f"P10–P90 sisällä **{_f(bt['share_inside_p10_p90'])}** (odotus ≈ 0.80), "
            f"keskivirhe (toteuma − P50) **{_f(bt['mean_error'])}** θ",
            "",
            "| malli | päivä | toteutunut θ | ennuste |",
            "|---|---|---|---|",
            *[f"| {p['model_id']} | {p['date']} | {_f(p['theta'])} | "
              f"{_band(p['prediction_p10_p50_p90'])} |" for p in bt["points"]],
        ]  # fmt: skip

    lines += ["", "## 2. Kustannukset (määrittely 5.2, A16)",
              "Hinta = blended (3 × input + output) / 4, USD / 1M tokenia. Regressio "
              "hintaennätyksiin (kuukaudet, joina halvin tason ylittävä malli halpeni) sekä "
              "porrasfunktion arvoon saranakuukautena ja nyt (sensuroidut pisteet, A16). "
              "Palainen malli valitaan, jos laskuvauhti muuttui viimeisen 12 kk:n aikana. "
              "Ennusteet on rajattu väliin [markkinan halvin hinta millä tahansa tasolla, "
              "tason nykyinen halvin hinta] – halvin saatavilla oleva hinta ei voi nousta.",
              ""]  # fmt: skip
    for label, fc in res.costs.items():
        lines += [
            f"### Taso: {label} (θ ≥ {_f(fc['target_theta'])}, viite {fc['reference_model']})"
        ]
        if fc.get("status") == "insufficient" or "model" not in fc:
            lines += [f"Ei laskettu: {fc['note']}.", ""]
            continue
        h = fc["halving_months_p10_p50_p90"]
        cur = fc.get("current_cheapest") or {}
        cur_cost = cur.get("cost")
        status_line = {
            "ok": f"- Puoliintumisaika nyt: **{_f(fc['halving_months'], 1)} kk** "
            f"(bootstrap P10–P90 {_hl(h[0])} – {_hl(h[2])}; osuus draweista, joissa hinta "
            f"ei laske: {_f(fc['share_not_declining'])})",
            "plateau": "- **Lasku on pysähtynyt (lattia):** viimeisen 12 kk:n trendi ei laske → "
            "puoliintumisaika nyt määrittelemätön; ennuste seuraa lattiaa",
            "not_declining": "- Hinta ei laske tällä tasolla: puoliintumisaika **määrittelemätön**",
            "at_floor": f"- **Taso on markkinan hintalattialla** (halvin {_f(cur_cost, 4)} $ vs. "
            f"lattia {_f(fc['market_floor'], 4)} $): hinta ei voi enää juuri laskea – kehitys "
            "näkyy kyvykkyyden nousuna samalla hinnalla. Historiallinen puoliintumisaika kuvaa "
            "lattiaan johtanutta laskua.",
        }[fc["status"]]
        if fc.get("months_to_floor") is not None:
            status_line += (f"\n- Nykyvauhdilla lattia ({_f(fc['market_floor'], 4)} $) "
                            f"saavutetaan noin **{_f(fc['months_to_floor'], 1)} kk** kuluttua")  # fmt: skip
        lines += [
            status_line,
            f"- Malli: {fc['model']} (lattia-/palaisuustesti p = {_f(fc['piecewise_p_value'], 3)}); "
            f"historiallinen puoliintumisaika {_f(fc['historical_halving_months'], 1)} kk, "
            "viimeiset 12 kk "
            + (
                f"{_f(fc['recent_halving_months'], 1)} kk"
                if fc["recent_halving_months"]
                else "ei laskua"
            ),  # fmt: skip
            f"- Halvin nyt: {cur.get('model_id', '–')} ({_f(cur.get('cost'))} $/1M)",
            f"- Hintaennätyksiä {len(fc['record_lows'])}: "
            + ", ".join(
                f"{r['month']:%Y-%m} {r['model_id']} {r['cost']:.3g}" for r in fc["record_lows"]
            ),  # fmt: skip
            "",
            "| horisontti | hinta $/1M, P50 [P10 – P90] |",
            "|---|---|",
            *[
                f"| {x['months']} kk | {_band(x['cost_p10_p50_p90'], sig=True)} |"
                for x in fc["horizons"]
            ],  # fmt: skip
        ]
        b = res.backtest_costs.get(label, {})
        if b.get("status") == "ok":
            p = b["predicted_cost_at_as_of_p10_p50_p90"]
            lines += [
                "",
                f"Backtest: cutoff {b['cutoff']} (malli {b['trained_model']}), ennuste nyt "
                f"{_band(p, sig=True)} $/1M, toteutunut {_f(b['realized_cost_at_as_of'], 3)} → "
                f"log-virhe {_f(b['log_error'])} (ilman lattia-/kattorajausta "
                f"{_f(b['unclipped_log_error'])}), välin sisällä: {b['inside_p10_p90']}",
            ]
            if b.get("p50_at_floor"):
                lines.append(
                    f"\n> P50 osui markkinan lattiaan ({_f(b['trained_market_floor'], 4)} $): "
                    "rajattu log-virhe mittaa lattian osuvuutta, ei trendimallia. Regression oma "
                    "virhe on rajaamaton luku."
                )
        else:
            lines.append(f"\nBacktest: {b.get('status', 'ei saatavilla')}")
        lines.append("")
    if res.warnings:
        lines += ["## Varoitukset", *[f"- {w}" for w in res.warnings]]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
