"""6.4 (A37): θ-ennusteiden tulkinta.

θ on suhteellinen asteikko ankkurien (GPT-4 kesäkuu 2023 = 0, GPT-5 = 3.5) suhteen: testin
logit-tulos on a·(θ − b), joten "θ/vuosi" ei sellaisenaan kerro, mitä malli osaa. Tässä
ennuste käännetään odotetuksi tulokseksi kiinteillä vertailutesteillä (IRT-kaava
c + (1 − c)·σ(a(θ − b)), osuus testin katosta) ja lasketaan mitattavuusraja: θ, jossa se hyvin
mitattu testi, jolla raja on korkein, on kyllääntymässä (odotettu tulos c + (1 − c)·σ(k),
k = 2 → noin 88 % arvausrajan ja katon välisestä matkasta). Rajan yläpuolella testit
erottelevat enää heikosti, joten ennusteen tarkistaminen nykyisillä testeillä on epävarmaa.
Raja on sopimus, ei havaittu taitekohta, ja sen oma epävarmuus (a:n ja b:n keskivirhe) ei ole
mukana.

Käännös on mallin odotusarvo mallille, jonka θ on annettu. Väli kuvaa θ-ennusteen epävarmuutta,
ei testikohtaista hajontaa käyrän ympärillä eikä testiparametrien epävarmuutta. Käännös perii
IRT-mallin oletukset (yksiulotteisuus, testin parametrit nykyisestä ajosta).
"""

from __future__ import annotations

import math

import polars as pl


def expected_score(theta: float, a: float, b: float, c: float) -> float:
    """Odotettu normalisoitu tulos (osuus testin katosta)."""
    return c + (1 - c) / (1 + math.exp(-a * (theta - b)))


def _params(params: pl.DataFrame, domain: str | None = None) -> pl.DataFrame:
    """Repository.get_benchmark_params → yhtenäiset sarakkeet. Mallimäärä ilman poikkeamia
    (n_models_fit), jos saatavilla. domain rajaa testit osa-alueen omiin."""
    if domain is not None and "domain" in params.columns:
        params = params.filter(pl.col("domain") == domain)
    n_col = "n_models_fit" if "n_models_fit" in params.columns else "n_models"
    best = pl.col("best_score") if "best_score" in params.columns else pl.lit(None)
    return params.select(
        "benchmark_id", "name",
        pl.col("discrimination_a").alias("a"), pl.col("difficulty_b").alias("b"),
        pl.col("guessing_floor").fill_null(0.0).alias("c"),
        pl.col(n_col).alias("n_models"), best.cast(pl.Float64).alias("best_score"),
    )  # fmt: skip


def measurable_ceiling(
    params: pl.DataFrame,
    k: float = 2.0,
    min_a: float = 1.0,
    min_models: int = 15,
    *,
    domain: str | None = None,
    n_candidates: int = 3,
) -> dict | None:
    """Mitattavuusraja: suurin b + k/a testeistä, joiden erottelukyky a ≥ min_a ja joilla on
    vähintään min_models mallia (yksittäinen harvoin ajettu tai heikosti erotteleva testi ei
    riitä). Rajalla testin odotettu tulos on c + (1 − c)·σ(k). Palauttaa myös seuraavat
    ehdokkaat, koska raja riippuu usein yhdestä testistä ja hyppää, jos se putoaa pois."""
    p = _params(params, domain).filter((pl.col("a") >= min_a) & (pl.col("n_models") >= min_models))
    if p.is_empty():
        return None
    ranked = p.with_columns(ceiling=pl.col("b") + k / pl.col("a")).sort(
        ["ceiling", "benchmark_id"], descending=[True, False]
    )
    top = ranked.row(0, named=True)
    return {
        "theta": float(top["ceiling"]),
        "benchmark_id": top["benchmark_id"],
        "name": top["name"],
        "a": float(top["a"]),
        "b": float(top["b"]),
        "c": float(top["c"]),
        "n_models": int(top["n_models"]),
        "k": k,
        "min_a": min_a,
        "min_models": min_models,
        "score_at_ceiling": expected_score(top["ceiling"], top["a"], top["b"], top["c"]),
        "candidates": [
            {"benchmark_id": r["benchmark_id"], "name": r["name"], "theta": float(r["ceiling"])}
            for r in ranked.head(n_candidates).iter_rows(named=True)
        ],
    }


def reference_label(r: dict) -> str:
    """Vertailutestin nimi merkintöineen (kyllääntynyt, heikosti erotteleva)."""
    notes = [
        n
        for n, on in (
            ("kyllääntynyt", r.get("saturated")),
            (f"heikosti erotteleva, a {r['a']:.2f}", r.get("weak")),
        )
        if on
    ]
    return r["name"] + (f" ({'; '.join(notes)})" if notes else "")  # fmt: skip


def interpret_forecast(
    horizons: list[dict],
    current_theta: float,
    params: pl.DataFrame,
    reference_ids: list[str],
    *,
    k: float = 2.0,
    min_a: float = 1.0,
    min_models: int = 15,
    domain: str | None = None,
) -> dict:
    """horizons: capability_forecast()["horizons"] (months, prediction_p10_p50_p90).

    Palauttaa mitattavuusrajan, horisontit joiden P50 (tai P90) ylittää sen, ja
    vertailutestien odotetut tulokset nykyrintamalle ja horisonttien P10/P50/P90:lle
    (käännös on monotoninen, joten θ-persentiili → tulospersentiili). domain: osa-alueen θ
    tulkitaan vain osa-alueen omilla testeillä (vertailutestit muista osa-alueista puuttuvat).
    Vertailutesti on kyllääntynyt, kun nykyrintama on jo sen oman rajan b + k/a yläpuolella."""
    ceiling = measurable_ceiling(params, k, min_a, min_models, domain=domain)
    p = _params(params, domain)
    known = set(p["benchmark_id"])
    refs = []
    for bid in reference_ids:
        if bid not in known:
            continue
        r = p.filter(pl.col("benchmark_id") == bid).row(0, named=True)

        def score(t, r=r):
            return None if t is None else expected_score(t, r["a"], r["b"], r["c"])

        refs.append({
            "benchmark_id": bid, "name": r["name"], "a": r["a"], "b": r["b"], "c": r["c"],
            "n_models": r["n_models"], "current": score(current_theta),
            "best_observed": r["best_score"],
            "saturated": current_theta > r["b"] + k / r["a"],
            "weak": r["a"] < min_a,
            "horizons": [{"months": h["months"],
                          "score_p10_p50_p90": [score(t) for t in h["prediction_p10_p50_p90"]]}
                         for h in horizons],
        })  # fmt: skip
    lim = ceiling["theta"] if ceiling else None

    def beyond(i: int) -> list[int]:
        if lim is None:
            return []
        return [h["months"] for h in horizons
                if h["prediction_p10_p50_p90"][i] is not None
                and h["prediction_p10_p50_p90"][i] > lim]  # fmt: skip

    return {
        "ceiling": ceiling,
        "current_theta": current_theta,
        "beyond_ceiling_p50": beyond(1),
        "beyond_ceiling_p90": beyond(2),
        "references": refs,
        "missing_references": [b for b in reference_ids if b not in known],
    }
