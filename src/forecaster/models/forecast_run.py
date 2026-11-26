"""K5: ennusteajon orkestrointi, tallennus (forecast_runs) ja raportti (docs/ennusteet.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import partial
from pathlib import Path

import polars as pl

from forecaster.config import ForecastFile, Paths
from forecaster.db.repository import Repository
from forecaster.models.forecaster import (
    METHOD_VERSION,
    ForecastConfig,
    add_months,
    backtest_capability,
    backtest_capability_rolling,
    backtest_cost,
    capability_forecast,
    cost_forecast,
    debias_capability,
    month_starts,
    prospective_calibration,
    selection_bias,
    top_k_capability_forecast,
)
from forecaster.models.interpret import interpret_forecast, reference_label


@dataclass
class ForecastResults:
    run_id: str
    irt_run_id: str
    as_of: date
    capability: dict
    costs: dict[str, dict] = field(default_factory=dict)
    backtest_capability: dict | None = None
    backtest_rolling: dict | None = None
    backtest_costs: dict[str, dict] = field(default_factory=dict)
    prospective: dict | None = None  # A30: tallennettujen ennusteiden toteutuminen
    warnings: list[str] = field(default_factory=list)


def forecast_config(ff: ForecastFile) -> ForecastConfig:
    """Ennusteasetukset tiedostosta config/forecast.yaml (myös ajankohtaan sidottu backtest)."""
    return ForecastConfig(
        fit_start=ff.fit_start,
        horizons_months=tuple(ff.horizons_months),
        n_boot=ff.n_boot,
        seed=ff.seed,
        piecewise_window_months=ff.piecewise_window_months,
        piecewise_alpha=ff.piecewise_alpha,
        level_floor=ff.level_floor,
        small_sample=ff.small_sample,
        small_sample_max_points=ff.small_sample_max_points,
        fit_windows_months=tuple(ff.fit_windows_months),
    )


def run_forecasts(repo: Repository, ff: ForecastFile, as_of: date | None = None) -> ForecastResults:
    as_of = as_of or date.today()
    irt_run = repo.latest_run_id("overall")
    if irt_run is None:
        raise ValueError("IRT-ajoa ei ole – aja ensin 'forecaster fit'")
    # Vain as_of-päivään mennessä julkaistut mallit (ei tulevan datan vuotoa, jos as_of < tänään)
    all_scores = repo.get_capability_with_dates(irt_run).filter(pl.col("release_date") <= as_of)
    unreliable = all_scores.filter(pl.col("theta_se") > ff.max_theta_se)
    scores = all_scores.filter(pl.col("theta_se") <= ff.max_theta_se)
    cfg = forecast_config(ff)
    run_id = f"fc_{as_of:%Y%m%d}_{irt_run[-8:]}"
    # A30: kuukausikaista as_of → as_of + suurin horisontti tallennetaan, jotta ennustetta
    # voidaan myöhemmin verrata toteutuneisiin rintamapisteisiin niiden julkaisupäivänä.
    monthly_dates = [add_months(as_of, k) for k in range(max(ff.horizons_months) + 1)]
    cap = capability_forecast(scores, cfg, as_of, eval_dates=monthly_dates)
    cap["monthly"] = cap.pop("eval")
    cap["method_version"] = METHOD_VERSION  # 6.1: toteumaseuranta versioittain
    # 6.4 (A37): tulkinta vertailutesteillä ja mitattavuusraja (ei muuta ennustetta)
    cap["interpretation"] = interpret_forecast(
        cap["horizons"], cap["current_frontier_theta"], repo.get_benchmark_params(irt_run),
        ff.reference_benchmarks, k=ff.measurability_k, min_a=ff.measurability_min_a,
        min_models=ff.measurability_min_models,
    )  # fmt: skip
    # A31: vaihtoehtoinen ennuste todelliselle kyvykkyydelle (rintamavalinnan harha pois)
    cap["selection_bias"] = selection_bias(scores, cfg, as_of)
    cap["true_capability_horizons"] = debias_capability(cap, cap["selection_bias"])
    res = ForecastResults(run_id, irt_run, as_of, cap)
    res.prospective = prospective_calibration(repo.get_capability_forecast_history(), scores, as_of)
    if unreliable.height:
        res.warnings.append(
            f"{unreliable.height} mallia jätetty pois, θ-SE > {ff.max_theta_se} "
            "(vähän tuloksia tai heikosti erottelevia testejä, θ heikosti määrätty): "
            + ", ".join(
                f"{m} (θ {t:.1f} ± {s:.1f})"
                for m, t, s in unreliable.select("model_id", "theta", "theta_se").iter_rows()
            )  # fmt: skip
        )
    res.backtest_capability = backtest_capability(scores, cfg, as_of, ff.backtest_holdout_months)
    res.backtest_rolling = backtest_capability_rolling(
        scores, cfg, as_of, ff.backtest_origins_months, ff.backtest_holdout_months
    )
    if ff.comparison_top_k:
        # 2.6: vertailuennuste (vauhti k parhaasta per neljännes) ja sen liukuva backtest
        top_k_fn = partial(top_k_capability_forecast, k=ff.comparison_top_k)
        try:
            cmp_bt = backtest_capability_rolling(
                scores, cfg, as_of, ff.backtest_origins_months, ff.backtest_holdout_months,
                forecast_fn=top_k_fn,
            )  # fmt: skip
            cap["comparison_top_k"] = top_k_fn(scores, cfg, as_of) | {"backtest_rolling": cmp_bt}
        except ValueError as exc:
            res.warnings.append(f"Vertailuennuste (top-{ff.comparison_top_k}) ohitettu: {exc}")

    prices = repo.get_price_at_dates(month_starts(ff.fit_start, as_of))
    theta = dict(scores.select("model_id", "theta").iter_rows())
    for t in ff.cost_targets:
        if t.model_id not in theta:
            res.warnings.append(f"Kustannustason viitemalli {t.model_id} puuttuu IRT-ajosta")
            continue
        level = theta[t.model_id]
        fc = cost_forecast(scores, prices, level, cfg, as_of, res.capability["slope_per_year"])
        res.costs[t.label] = fc | {"reference_model": t.model_id, "method_version": METHOD_VERSION}
        res.backtest_costs[t.label] = backtest_cost(
            scores, prices, level, cfg, as_of, ff.backtest_holdout_months
        )

    params = ff.model_dump()
    with repo.transaction():
        repo._run("DELETE FROM forecast_runs WHERE run_id LIKE ?", [run_id + "%"])
        repo.save_forecast(run_id + ":capability", irt_run, "capability", params, res.capability)
        repo.save_forecast(run_id + ":backtest_capability", irt_run, "backtest",
                           params, res.backtest_capability)  # fmt: skip
        repo.save_forecast(run_id + ":backtest_rolling", irt_run, "backtest",
                           params, res.backtest_rolling)  # fmt: skip
        repo.save_forecast(run_id + ":prospective", irt_run, "calibration",
                           params, res.prospective)  # fmt: skip
        for label, fc in res.costs.items():
            repo.save_forecast(f"{run_id}:cost:{label}", irt_run, "cost", params, fc)
            repo.save_forecast(f"{run_id}:backtest_cost:{label}", irt_run, "backtest",
                               params, res.backtest_costs[label])  # fmt: skip
    return res


# --- Raportti -------------------------------------------------------------------------


def _f(v, nd=2) -> str:
    return "–" if v is None else f"{v:.{nd}f}"


def _floor_label(fc: dict) -> str:
    """Tila at_floor lasketaan aina koko markkinan lattiasta (A26)."""
    return "markkinan lattia"


def _calibration_notes(shares: list[float], se: float | None) -> str:
    """Datasta riippuvat tulkintalauseet: tulostetaan vain, kun ne pätevät tähän ajoon."""
    notes = ""
    if se is not None and se >= 0.1:
        notes += (
            " Lähtöpäivien keskiarvon keskivirhe (SE) on suuri ja todennäköisesti aliarvio, "
            "joten aineisto ei kerro luotettavasti, ovatko välit hyvin kalibroituja, liian "
            "leveitä vai liian kapeita."
        )
    if len(shares) > 1 and all(s in (0.0, 1.0) for s in shares):
        notes += (
            " Tässä ajossa jokainen lähtöpäivä osui joko kokonaan välille tai kokonaan sen "
            "ulkopuolelle: välit ovat leveitä pisteiden välisen hajonnan suhteen, mutta "
            "kapeita lähtöpäivän yhteisen trendivirheen suhteen."
        )
    return notes


def _horizon_floor(x: dict) -> str:
    """A26: horisontin alaraja ja sen lähde (lähitaso taso − δ_h ≤ θ < taso / koko markkina)."""
    if x.get("floor") is None:
        return "–"
    if x.get("floor_scope") == "level":
        return f"{x['floor']:.3g} (lähitaso, δ {x['floor_delta_theta']:.1f} θ)"
    return f"{x['floor']:.3g} (markkina)"


def _hl(v) -> str:
    """Puoliintumisajan persentiili: None = ääretön (hinta ei laske tässä drawien osassa)."""
    return "∞ (ei laske)" if v is None else f"{v:.1f}"


def _band(b, sig: bool = False) -> str:
    if not b or b[0] is None:
        return "–"
    fmt = (lambda v: f"{v:.3g}") if sig else (lambda v: f"{v:.2f}")
    return f"{fmt(b[1])} [{fmt(b[0])} – {fmt(b[2])}]"


def _pct(v) -> str:
    return "–" if v is None else f"{100 * v:.0f} %"


def _interpretation_section(it: dict | None) -> list[str]:
    """6.4 (A37): mitattavuusraja ja odotettu tulos vertailutesteillä."""
    if not it:
        return []
    lines = ["", "### Tulkinta: mitattavuusraja ja vertailutestit (A37)"]
    ce = it.get("ceiling")
    if ce:
        beyond = it["beyond_ceiling_p50"]
        beyond90 = [m for m in it["beyond_ceiling_p90"] if m not in beyond]
        others = ", ".join(f"{c['name']} θ {_f(c['theta'])}" for c in ce.get("candidates", [])[1:])
        lines += [
            "θ on suhteellinen asteikko ankkurien (GPT-4 kesäkuu 2023 = 0, GPT-5 = 3.5) "
            "suhteen; testin logit-tulos on a·(θ − b). "
            f"**Mitattavuusraja θ {_f(ce['theta'])}**: testi, jolla raja on korkein "
            f"({ce['name']}, a {_f(ce['a'])}, b {_f(ce['b'])}, {ce['n_models']} mallia), "
            f"saavuttaa siinä odotetusti noin {_pct(ce['score_at_ceiling'])} katostaan (raja = "
            f"suurin b + {ce['k']:g}/a testeistä, joilla a ≥ {ce['min_a']:g} ja vähintään "
            f"{ce['min_models']} mallia ilman poikkeamia). Rajan yläpuolella hyvin mitatut "
            "testit erottelevat enää heikosti, joten θ-ennusteen tarkistaminen nykyisillä "
            "testeillä on epävarmaa: toteutuminen riippuu myös siitä, ilmestyykö vaikeampia "
            "testejä ja kytkeytyvätkö ne asteikkoon. Raja on sopimus, ei havaittu taitekohta. "
            "Se on itsekin logistisen käyrän ekstrapolaatio, sen omaa epävarmuutta (a:n ja b:n "
            "keskivirhe) ei ole mukana, ja se riippuu yhdestä testistä"
            + (f" (seuraavat ehdokkaat: {others})" if others else "")
            + ". Lähellä rajaa oleva P50 voi vaihtaa puolta ajosta toiseen jo siksi, että "
            "horisontti lasketaan ajopäivästä; raja itse muuttuu, kun IRT-ajo vaihtuu.",
            "",
            "- " + (
                f"**P50 ylittää rajan horisonteilla {', '.join(f'{m} kk' for m in beyond)}.**"
                if beyond else "P50 ei ylitä rajaa millään horisontilla."
            ) + (
                f" P90 ylittää sen myös horisonteilla {', '.join(f'{m} kk' for m in beyond90)}."
                if beyond90 else ""
            ),
        ]  # fmt: skip
    refs = it.get("references") or []
    if refs:
        months = [h["months"] for h in refs[0]["horizons"]]
        lines += [
            "",
            "Odotettu tulos vertailutesteillä (osuus testin katosta, IRT-kaava "
            "c + (1 − c)·σ(a(θ − b)) rintamamallille, jonka θ on ennusteen P50 [P10 – P90]). "
            "Luvut ovat mallin odotusarvoja, eivät havaittuja tuloksia (vertaa sarakkeeseen "
            '"paras havaittu"; se voi olla rintamaa heikomman mallin tulos). Väli kuvaa '
            "θ-ennusteen epävarmuutta, ei testikohtaista hajontaa käyrän ympärillä eikä testin "
            "parametrien epävarmuutta. Käännös perii IRT-mallin oletukset; rajan yläpuolella se "
            "on ekstrapolaatio. Kyllääntynyt testi on jo nykyrintamalla oman rajansa "
            "yläpuolella, joten se ei kerro ennusteesta juuri mitään. Heikosti erottelevalla "
            "testillä (a alle hyvin mitatun testin rajan) käännös on epävarmempi.",
            "",
            "| testi | paras havaittu | nykyrintama | "
            + " | ".join(f"+{m} kk" for m in months)
            + " |",
            "|---|---|---|" + "---|" * len(months),
            *[
                f"| {reference_label(r)} | "
                f"{_pct(r.get('best_observed'))} | {_pct(r['current'])} | "
                + " | ".join(
                    f"{_pct(h['score_p10_p50_p90'][1])} [{_pct(h['score_p10_p50_p90'][0])} – "
                    f"{_pct(h['score_p10_p50_p90'][2])}]"
                    for h in r["horizons"]
                )
                + " |"
                for r in refs
            ],
        ]
    if it.get("missing_references"):
        lines.append(
            f"\nVertailutestejä ei löytynyt IRT-ajosta: {', '.join(it['missing_references'])}."
        )
    return lines


def _version_lines(pc: dict) -> list[str]:
    """6.1: toteumaseuranta menetelmäversioittain, kun ennusteita on useammalla versiolla."""
    by_v = [v for v in pc.get("by_method_version", []) if v["n_pairs"]]
    if len(by_v) < 2:
        return []
    return [
        "- Yllä olevat luvut yhdistävät kaikki menetelmäversiot. Menetelmäversioittain (eri "
        "versioiden tuloksia ei pidä tulkita yhdessä; versio 0 = ennusteet ennen versiointia, "
        "joiden menetelmä muuttui niiden välillä):",
        *[
            f"  - versio {v['method_version']}: ennusteita {v['n_forecasts']} "
            f"({v['first_forecast']} alkaen), pareja {v['n_pairs']}, uniikkeja pisteitä "
            f"{v['n_unique_points']}, välin sisällä pistekohtaisesti "
            f"{_f(v['point_balanced_share_inside_p10_p90'])}, keskivirhe {_f(v['mean_error'])} θ"
            for v in by_v
        ],
    ]


def _origin_metrics(rb: dict, cutoffs: set) -> dict:
    """Liukuvan backtestin mittarit annetuilta lähtöpäiviltä (vertailu yhteisiltä päiviltä)."""
    ok = [o for o in rb["origins"] if o["status"] == "ok" and o["cutoff"] in cutoffs]
    pts = [p for o in ok for p in o["points"]]
    if not pts:
        return {"n_origins_ok": 0, "n_points": 0}
    band = [p["prediction_p10_p50_p90"] for p in pts]
    err = [p["theta"] - b[1] for p, b in zip(pts, band, strict=True)]
    return {
        "n_origins_ok": len(ok),
        "n_points": len(pts),
        "mean_origin_share": sum(o["share_inside_p10_p90"] for o in ok) / len(ok),
        "mean_error": sum(err) / len(err),
        "mean_abs_error": sum(abs(e) for e in err) / len(err),
        "mean_width_p10_p90": sum(b[2] - b[0] for b in band) / len(band),
    }


def _comparison_section(c: dict, cmp_: dict, rb: dict | None) -> list[str]:
    """2.6: pääennuste vs. vertailuennuste (vauhti k parhaasta mallista per neljännes)."""
    k = cmp_["k"]
    cb_full = cmp_.get("backtest_rolling")
    if rb and cb_full:
        # Mittarit vain lähtöpäiviltä, jotka onnistuivat molemmilla (muuten eri pistejoukot)
        common = {o["cutoff"] for o in rb["origins"] if o["status"] == "ok"} & {
            o["cutoff"] for o in cb_full["origins"] if o["status"] == "ok"
        }
        widened = any(
            o["status"] == "ok" and o["cutoff"] in common and o.get("small_sample_applied")
            for o in rb["origins"]
        )
        rb, cb = _origin_metrics(rb, common), _origin_metrics(cb_full, common)
    else:
        cb, widened = {}, False
    lines = [
        "",
        f"### Vertailu: kehitysvauhti {k} parhaasta mallista per neljännes (2.6)",
        "Pääennusteen kulmakerroin lasketaan pelkistä rintamapisteistä, joita on vähän. "
        f"Vertailuennusteessa kulmakerroin sovitetaan {k} parhaaseen malliin jokaiselta "
        f"vuosineljännekseltä ({cmp_['n_slope_points']} pistettä), ja taso valitaan "
        "rintamapisteistä. Ennustettava suure on sama (rintamapiste). Oletus: ylälaita ja "
        "rintama kasvavat samaa vauhtia. Vertailu on lineaarinen malli samoilla "
        "sovitusikkunoilla kuin pääennuste (A28), mutta ilman saranaa ja pienen otoksen "
        "korjausta, eikä sitä käytetä ennusteissa. Siksi sen kehitysvauhdin väli voi olla "
        "rakenteellisesti kapeampi. Jos "
        "menetelmät alkavat erota, tarkista sekä pääennuste että vertailun oletus.",
        "",
        "| | pääennuste | vertailu |",
        "|---|---|---|",
        f"| kehitysvauhti θ/v, P50 [P10 – P90] | {_band(c['slope_per_year_p10_p50_p90'])} | "
        f"{_band(cmp_['slope_per_year_p10_p50_p90'])} |",
        *[
            f"| {h['months']} kk, prediction | {_band(h['prediction_p10_p50_p90'])} | "
            f"{_band(hc['prediction_p10_p50_p90'])} |"
            for h, hc in zip(c["horizons"], cmp_["horizons"], strict=True)
        ],
    ]
    if rb and cb and cb["n_points"]:
        lines += [
            f"| liukuva backtest: yhteisiä lähtöpäiviä / pisteitä | {rb['n_origins_ok']} / "
            f"{rb['n_points']} | {cb['n_origins_ok']} / {cb['n_points']} |",
            f"| liukuva backtest: välin sisällä, lähtöpäivien ka. (odotus ≈ 0.80) | "
            f"{_f(rb['mean_origin_share'])} | {_f(cb.get('mean_origin_share'))} |",
            f"| liukuva backtest: keskivirhe θ, kaikki pisteet | {_f(rb['mean_error'])} | "
            f"{_f(cb.get('mean_error'))} |",
            f"| liukuva backtest: keskim. itseisvirhe θ, kaikki pisteet | "
            f"{_f(rb.get('mean_abs_error'))} | {_f(cb.get('mean_abs_error'))} |",
            f"| liukuva backtest: välin keskileveys θ, kaikki pisteet | "
            f"{_f(rb.get('mean_width_p10_p90'))} | {_f(cb.get('mean_width_p10_p90'))} |",
            "",
            "Mittarit lasketaan vain lähtöpäiviltä, jotka onnistuivat molemmilla menetelmillä. "
            "Yllä olevan liukuvan backtestin tulkintarajoitukset pätevät: lähtöpäiviä on vähän ja "
            "ne ovat päällekkäisiä, joten eroja ei kannata tulkita tarkasti. Kattavuus selvästi "
            "yli 0.80 voi tarkoittaa liian leveää väliä, mutta muutamalla päällekkäisellä "
            "lähtöpäivällä se voi johtua myös siitä, että saman lähtöpäivän virheet korreloivat "
            "(A33)."
            + (
                " Leveydet eivät ole suoraan vertailukelpoisia: pääennusteen välejä levennettiin "
                "osalla lähtöpäivistä pienen rintaman vuoksi (A27), vertailun ei."
                if widened
                else ""
            ),
        ]
    return lines


def write_report(res: ForecastResults, paths: Paths, out: Path | None = None) -> Path:
    out = out or paths.root / "docs" / "ennusteet.md"
    c = res.capability
    lines = [
        "# Ennusteet",
        "",
        f"_Generoitu {res.as_of} komennolla `forecaster forecast`. IRT-ajo `{res.irt_run_id}`, "
        f"ennusteajo `{res.run_id}`, menetelmäversio "
        f"{c.get('method_version', METHOD_VERSION)} (ks. TOTEUTUSSUUNNITELMA.md, "
        "menetelmäversiot)._",
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
        f"- Kehitysvauhti: **{_f(c['slope_per_year'])} θ/vuosi** (bootstrap-jakauman mediaani "
        f"sovitusikkunoineen, A28–A29; P10–P90 {_f(c['slope_per_year_p10_p50_p90'][0])} – "
        f"{_f(c['slope_per_year_p10_p50_p90'][2])}; yhdessä synteettisessä kokeessa tasaisella "
        "vauhdilla hieman liian leveä, kattavuus noin 0.9; vauhdin muuttuessa kattavuutta ei ole "
        "mitattu). Sama "
        "vauhti on kustannusennusteen lähitason lattian taustalla (A26). Koko jakson sovituksen "
        f"({c['model']}) nykyinen kulmakerroin (diagnostiikka): "
        f"{_f(c.get('slope_per_year_full_period'))} θ/vuosi.",
        f"- Palaisuustesti (viim. 12 kk): p = {_f(c['piecewise_p_value'], 3)}, "
        f"viimeaikainen kulmakerroin {_f(c['recent_slope_per_year'])} θ/vuosi "
        f"(koko ikkunan lineaarinen {_f(c['linear_slope_per_year'])})",
        f"- Mallivalinta tehdään jokaisessa koko jakson bootstrap-drawissa uudelleen: palainen "
        f"malli valittiin {_f(c.get('share_piecewise_full_draws', c['share_piecewise_draws']))} "
        "niistä – välit sisältävät rakenne-epävarmuuden (lineaarinen vs. kiihtyvä). "
        + (
            "Koska koko jakson malli on palainen, saranatesti tehdään myös saranan yli "
            f"ulottuvissa ikkunoissa (palainen {_f(c.get('share_piecewise_window_draws'))} "
            "ikkunadrawista, A29)."
            if c["model"] == "piecewise" and c.get("fit_window_starts")
            else "Lyhyemmissä ikkunoissa saranatestiä ei tehdä."
        ),
        f"- Nykyinen rintama: θ = {_f(c['current_frontier_theta'])}",
        "- Pienen otoksen korjaus (A27): "
        + ("**käytössä**" if c.get("small_sample_applied") else "ei käytössä"),
        "- Sovitusikkunat (A28): "
        + (
            "koko jakso tai sovitus alkaen "
            + ", ".join(str(d) for d in c["fit_window_starts"])
            + f" (lyhyempi ikkuna {_f(c.get('share_windowed_draws'))} drawista; "
            "ikkunat, jotka alkaisivat ennen sovituksen alkua tai joissa on liian vähän "
            "rintamapisteitä, jätetään pois)"
            if c.get("fit_window_starts")
            else "vain koko jakso"
        ),
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
        *_interpretation_section(c.get("interpretation")),
    ]

    sb = c.get("selection_bias") or {}
    if sb.get("status") == "ok":
        lines += [
            "",
            "### Todellisen kyvykkyyden ennuste (rintamavalinnan harha korjattu, A31)",
            "Yllä oleva ennuste koskee *havaittuja* ennätyksiä. Rintamaan päätyy malleja, joiden "
            "mittausvirhe sattui olemaan positiivinen, joten havaitut ennätykset ovat todellista "
            "kyvykkyyttä korkeampia. Harha arvioidaan parametrisella bootstrapilla samalle "
            f"estimaattorille kuin pääennuste ({sb['n_sim']} toistoa × {sb['inner_boot']} drawia, "
            "sovitusikkunoineen): θ̂ oletetaan todeksi, siihen lisätään mittausvirhe, ja ennusteen "
            "mediaania kohinaisella datalla verrataan kohinattomaan. Korjattu ennuste = havaitun "
            "ennuste − harha; välin leveys ei muutu.",
            f"- Nykyrintaman huipun harha {_f(sb['current_frontier_bias'])} θ, kehitysvauhdin "
            f"harha {_f(sb['slope_bias_per_year'])} θ/vuosi",
            "- Synteettisessä kokeessa (scripts/validate_selection_bias.py, 100 toistoa) korjaus "
            "pienensi todellisten ennätysten keskivirheen −0.27:stä −0.03 θ:aan (SE 0.3) ja "
            "−0.12:sta −0.06 θ:aan (SE 0.15; nykyrintaman θ-SE:n mediaani on noin 0.17). Jäävä "
            "virhe on samaa suuruusluokkaa kuin pääennusteen oma virhe havaittuihin ennätyksiin "
            "(−0.05…−0.06). Siirretty P10–P90 kattoi todelliset ennätykset 0.85 (SE 0.15) ja 0.94 "
            "(SE 0.3), eli väli on todelliselle kyvykkyydelle hieman leveä. Arvio on likimääräinen: "
            "θ̂ sisältää jo kohinaa, joten harha jää yleensä hieman alakanttiin.",
            "",
            "| horisontti | harha | todellinen kyvykkyys, prediction |",
            "|---|---|---|",
            *[
                f"| {h['months']} kk | {_f(h['bias'])} | {_band(h['prediction_p10_p50_p90'])} |"
                for h in c.get("true_capability_horizons", [])
            ],
        ]

    bt = res.backtest_capability
    if bt:
        lines += [
            "",
            "### Backtest (kyvykkyys)",
            f"Sovitus vain {bt['cutoff']} asti julkaistuilla malleilla (malli {bt['trained_model']}, "
            f"kehitysvauhdin mediaani {_f(bt['trained_slope_per_year'])} θ/v), ennuste jokaiselle sen jälkeen "
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

    rb = res.backtest_rolling
    if rb:
        mm = rb["origin_share_min_max"] or [None, None]
        lines += [
            "",
            "### Liukuva backtest (kalibraatio, A24)",
            "Sama backtest usealta lähtöpäivältä. Yhden lähtöpäivän tulos on käytännössä yksi "
            "havainto, koska saman lähtöpäivän virheet ovat samanmerkkisiä.",
            f"- Lähtöpäiviä {rb['n_origins_ok']}, arvioituja pisteitä {rb['n_points']}, joista uniikkeja "
            f"{rb['n_unique_points']} (tuoreet pisteet toistuvat usealla lähtöpäivällä ja painavat enemmän)",
            f"- Ennustevälin P10–P90 sisällä: kaikista pisteistä **{_f(rb['pooled_share_inside_p10_p90'])}**, "
            f"lähtöpäivien keskiarvo **{_f(rb['mean_origin_share'])}** ± {_f(rb['mean_origin_share_se'])} (SE) "
            f"(vaihteluväli {_f(mm[0])} – {_f(mm[1])}; odotus ≈ 0.80). Keskivirhe {_f(rb['mean_error'])} θ",
            "- **Tulkinta:** saman lähtöpäivän virheet ovat samanmerkkisiä, joten tehokas "
            "otoskoko on korkeintaan lähtöpäivien määrä eikä pisteiden määrä. Siksi kalibraatiota "
            "arvioidaan lähtöpäivien keskiarvolla, ei kaikkien pisteiden osuudella. Lähtöpäivät "
            "jakavat lisäksi arviointipisteitä ja koulutusdataa, joten SE on todennäköisesti "
            "aliarvio."
            + _calibration_notes(
                [o["share_inside_p10_p90"] for o in rb["origins"] if o["status"] == "ok"],
                rb["mean_origin_share_se"],
            ),
            "",
            "`rintama` = koulutusrintaman pisteet; `korjaus` = pienen otoksen korjaus (A27).",
            "",
            "| lähtöpäivä | arvioitu asti | malli (koko jakso) | θ/v (mediaani) | rintama | korjaus | pisteitä | "
            "välin sisällä | keskivirhe |",
            "|---|---|---|---|---|---|---|---|---|",
            *[
                f"| {o['cutoff']} | {o['end']} | {o.get('trained_model', '–')} | "
                f"{_f(o.get('trained_slope_per_year'))} | {o.get('trained_n_frontier', '–')} | "
                f"{'kyllä' if o.get('small_sample_applied') else 'ei'} | {o.get('n_points', 0)} | "
                f"{_f(o.get('share_inside_p10_p90'))} | {_f(o.get('mean_error'))} |"
                if o["status"] == "ok"
                else f"| {o['cutoff']} | {o['end']} | {o['status']} | | | | | | |"
                for o in rb["origins"]
            ],
        ]

    cmp_ = c.get("comparison_top_k")
    if cmp_:
        lines += _comparison_section(c, cmp_, rb)

    pc = res.prospective
    if pc is not None:
        lines += [
            "",
            "### Aiempien ennusteiden toteutuminen (A30)",
            "Tallennetut ennusteet (kunkin ISO-viikon uusin) verrattuna niiden jälkeen julkaistuihin "
            "rintamapisteisiin julkaisupäivänä. Toisin kuin backtest, tämä ei sovita mallia "
            "uudelleen: ennuste on tehty oikeasti etukäteen. θ-arvot ovat nykyisestä IRT-ajosta.",
        ]
        if not pc["n_forecasts"]:
            lines.append(
                "- Aiempia ennusteita kuukausikaistoineen ei vielä ole (tallennetaan "
                "2026-11-19 alkaen; tämän ajon ennuste on mukana seuraavissa raporteissa)."
            )
        elif not pc["n_pairs"]:
            lines.append(
                f"- Ennusteita {pc['n_forecasts']} ({pc['first_forecast']} alkaen), mutta niiden "
                "jälkeen ei ole vielä julkaistu uusia rintamamalleja. Vertailu alkaa kertyä, kun "
                "uusia ennätysmalleja julkaistaan."
            )
        else:
            lines += [
                f"- Ennusteita {pc['n_forecasts']} ({pc['first_forecast']} alkaen), joista "
                f"{pc['n_forecasts_with_points']}:llä toteutuneita pisteitä. Vertailupareja "
                f"{pc['n_pairs']}, uniikkeja rintamapisteitä {pc['n_unique_points']}.",
                f"- Ennustevälin P10–P90 sisällä: kaikista pareista "
                f"**{_f(pc['pooled_share_inside_p10_p90'])}**, pistekohtaisesti tasapainotettuna "
                f"**{_f(pc['point_balanced_share_inside_p10_p90'])}** (odotus ≈ 0.80). "
                "Peräkkäiset ennusteet arvioivat samoja pisteitä, joten parit eivät ole "
                f"riippumattomia: tehokas otoskoko on enintään {pc['n_unique_points']}, ja luku "
                "on pitkään hyvin epävarma.",
                f"- Keskivirhe (toteuma − P50) {_f(pc['mean_error'])} θ; θ-asteikon siirtymällä "
                f"korjattuna {_f(pc['mean_error_shift_adjusted'])} θ. Siirtymä = ennustehetken "
                "rintaman parhaan mallin θ nykyisessä IRT-ajossa miinus sen tallennettu θ "
                "(asteikon muutos ajojen välillä näkyisi muuten ennustevirheenä).",
                *(
                    [
                        f"- Ohitettu: {pc['n_retro_skipped']} takautuvaa ennustetta (tallennettu "
                        f"jälkikäteen), {pc['n_points_skipped']} pistettä ilman kaistaa."
                    ]
                    if pc["n_retro_skipped"] or pc["n_points_skipped"]
                    else []
                ),  # fmt: skip
                *_version_lines(pc),
                "",
                "| ennuste | versio | pisteitä | välin sisällä | keskivirhe | asteikon siirtymä |",
                "|---|---|---|---|---|---|",
                *[
                    f"| {f['as_of']} | {f.get('method_version', '–')} | {f['n_points']} | "
                    f"{_f(f['share_inside_p10_p90'])} | {_f(f['mean_error'])} | "
                    f"{_f(f['scale_shift'])} |"
                    for f in pc["forecasts"]
                    if f["n_points"]
                ],
            ]

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
            "at_floor": f"- **Taso on hintalattialla** (halvin {_f(cur_cost, 4)} $ vs. "
            f"{_floor_label(fc)} {_f(fc['market_floor'], 4)} $): hinta ei voi enää juuri laskea – kehitys "
            "näkyy kyvykkyyden nousuna samalla hinnalla. Historiallinen puoliintumisaika kuvaa "
            "lattiaan johtanutta laskua.",
        }[fc["status"]]
        if fc.get("months_to_floor") is not None:
            status_line += (f"\n- Nykyvauhdilla lattia ({_f(fc['market_floor'], 4)} $) "
                            f"saavutetaan noin **{_f(fc['months_to_floor'], 1)} kk** kuluttua")  # fmt: skip
        history_months = len(fc.get("monthly_cheapest") or [])
        if history_months < 12:
            status_line += (
                f"\n- **Lyhyt hintahistoria:** tasolla on hintoja vain {history_months} kk ja "
                f"{len(fc['record_lows'])} hintaennätystä, joten puoliintumisaika ja ennuste ovat "
                "hyvin epävarmoja: lyhyt jakso ei kerro, jatkuuko lasku samaa vauhtia. "
                "Bootstrap-väli ja P10–P90 eivät sisällä tätä epävarmuutta, joten ne ovat "
                "todellista kapeampia."
            )
        lines += [
            status_line,
            f"- Malli: {fc['model']} (lattia-/palaisuustesti p = {_f(fc['piecewise_p_value'], 3)}); "
            f"historiallinen puoliintumisaika {_f(fc['historical_halving_months'], 1)} kk, "
            "viimeiset 12 kk "
            + (f"(koko {history_months} kk:n historia) " if history_months < 12 else "")
            + (
                f"{_f(fc['recent_halving_months'], 1)} kk"
                if fc["recent_halving_months"]
                else "ei laskua"
                if fc["piecewise_p_value"] is not None
                else "ei erillistä arviota (liian vähän pisteitä ennen saranaa)"
            ),  # fmt: skip
            f"- Halvin nyt: {cur.get('model_id', '–')} ({_f(cur.get('cost'))} $/1M)",
            f"- Hintaennätyksiä {len(fc['record_lows'])}: "
            + ", ".join(
                f"{r['month']:%Y-%m} {r['model_id']} {r['cost']:.3g}" for r in fc["record_lows"]
            ),  # fmt: skip
            "",
            "| horisontti | hinta $/1M, P50 [P10 – P90] | alaraja (A26) |",
            "|---|---|---|",
            *[
                f"| {x['months']} kk | {_band(x['cost_p10_p50_p90'], sig=True)} | "
                f"{_horizon_floor(x)} |"
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
            if b.get("realized_below_floor"):
                lines.append(
                    f"\n> **Toteutunut hinta alitti koulutetun alarajan** ({_f(b['trained_floor'], 4)} $): "
                    "raja oli liian tiukka (A26)."
                )
            if b.get("p50_at_floor"):
                lines.append(
                    f"\n> P50 osui alarajaan ({_f(b['trained_floor'], 4)} $, "
                    f"{'lähitaso' if b['trained_floor_scope'] == 'level' else 'markkina'}): "
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
