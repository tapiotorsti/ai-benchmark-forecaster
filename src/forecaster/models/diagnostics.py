"""K4: IRT-ajon diagnostiikkaraportti (markdown, docs/irt_diagnostiikka.md)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from forecaster.config import Paths
from forecaster.db.repository import Repository
from forecaster.ingest.base import slugify
from forecaster.models.pipeline import FitSummary
from forecaster.models.quality import (
    bridge_summary,
    compare_external,
    connectivity,
    leave_one_benchmark_out,
)

# CC BY 4.0 vaatii lähteen, lisenssilinkin ja maininnan muutoksista (julkinen repo)
EPOCH_ATTRIBUTION = (
    "> Data: [Epoch AI](https://epoch.ai/data), lisenssi "
    "[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Tämä raportti on johdettu "
    "aineistosta: tulokset on normalisoitu, aggregoitu ja niihin on sovitettu IRT-malli."
)

# Siltabenchmarkit, joiden varassa vanhan ja uuden aikakauden yhteys on (datakokeilu)
KEY_BRIDGES = ["math_level_5", "gpqa_diamond"]


def _table(df: pl.DataFrame, floats: int = 3) -> str:
    if df.is_empty():
        return "_(ei rivejä)_"
    lines = ["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
    for row in df.iter_rows():
        cells = [
            f"{v:.{floats}f}" if isinstance(v, float) else ("" if v is None else str(v))
            for v in row
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def load_eci(paths: Paths) -> dict[str, float] | None:
    files = sorted(
        (paths.raw_dir / "epoch_ai").glob("*/extracted/epoch_capabilities_index/eci_scores.csv")
    )
    if not files:
        return None
    eci = pl.read_csv(files[-1])
    return {
        slugify(m): float(v) for m, v in eci.select("Model", "eci").iter_rows() if v is not None
    }


def write_report(
    repo: Repository,
    paths: Paths,
    s: FitSummary,
    *,
    warnings_seen: list[str] | None = None,
    stability: str = "key",  # none | key | all
    out: Path | None = None,
) -> Path:
    res = s.result
    out = out or paths.root / "docs" / "irt_diagnostiikka.md"
    lines: list[str] = [
        "# IRT-diagnostiikka",
        "",
        f"_Generoitu {date.today()} komennolla `forecaster fit --report`. Ajo `{s.run_id}`._",
        "",
        EPOCH_ATTRIBUTION,
        "",
        "## 1. Ajo",
        f"- Domain: **{s.domain}**"
        + (f" (osioparametrit kiinnitetty ajosta `{s.fixed_from}`)" if s.fixed_from else ""),
        f"- Mallit {len(res.model_ids)}, benchmarkit {len(res.benchmark_ids)}, "
        f"havainnot {res.n_observations}",
        f"- Konvergoi: **{res.converged}** "
        f"(gradientti {res.grad_norm:.1e}, {res.n_iter} iteraatiota)",
        f"- Ankkurit ({res.config.anchor_mode}): "
        + (
            ", ".join(f"{m} = {v:g}" for m, v in res.config.anchors.items())
            if len(res.config.anchors) <= 5
            else f"{len(res.config.anchors)} linkitysmallia"
        ),
        f"- Hajontaparametri φ (diagnostiikka): {res.dispersion:.4f}",
    ]
    if warnings_seen:
        lines += ["", "**Varoitukset sovituksessa:**", *[f"- {w}" for w in warnings_seen]]
    else:
        lines += ["- Ei varoituksia (Hessian positiividefiniitti, ankkurit löytyivät)"]

    lines += [
        "",
        "## 2. Karsinta",
        f"- Pudotettu {len(s.dropped_models)} mallia (alle rajan tuloksia) ja "
        f"{len(s.dropped_benchmarks)} benchmarkia: {', '.join(s.dropped_benchmarks) or '–'}",
    ]
    if s.dropped_models:
        shown = ", ".join(s.dropped_models[:40])
        more = f" … (+{len(s.dropped_models) - 40})" if len(s.dropped_models) > 40 else ""
        lines.append(f"- Pudotetut mallit: {shown}{more}")

    comps = connectivity(s.obs)
    bench_dates = dict(repo._run("SELECT benchmark_id, release_date FROM benchmarks"))
    bridges = bridge_summary(s.obs, bench_dates)
    lines += [
        "",
        "## 3. Kytkentä ja siltamallit",
        f"- Yhtenäisiä komponentteja: **{len(comps)}** "
        + ("(kaikki samalla asteikolla)" if len(comps) == 1 else f"– IRRALLISIA: {comps[1:]}"),
        f"- Siltamallit (≥1 vanha [<2023] ja ≥1 uusi [≥2025] testi): **{bridges['bridge_1_1']}**, "
        f"vahvat (≥2+2): {bridges['bridge_2_2']}",
        f"- Vain vanhoja: {bridges['only_old']}, vain uusia: {bridges['only_new']}",
    ]

    lines += ["", "## 4. Poikkeamat (määrittely 4.3, A4)"]
    if s.outliers.height:
        n_ex = int(s.outliers["excluded"].sum())
        n_high = s.outliers.filter(pl.col("direction") == "high").height
        lines += [
            f"{s.outliers.height} havaintoa liputettu (|robusti z| > 3 σ-ekvivalenttia, "
            f"z = (r − mediaani) / (1.4826·MAD) benchmarkin sisällä): {n_high} high, "
            f"{s.outliers.height - n_high} low. **Sovituksesta poistettu {n_ex}** (vain high = "
            "mahdollinen kontaminaatio, enintään 1 per malli, ei karsintarajan alle); "
            "sovitus ajettu uudelleen kerran.",
            "",
            "`low`-tulokset jätetään sovitukseen: ne voivat olla aitoa heikkoutta, jota "
            "yksiulotteinen malli ei selitä. Tarkista ne käsin ennen mahdollista poistoa.",
            "",
            _table(s.outliers.head(40)),
        ]
    else:
        lines.append("Ei liputettuja havaintoja (tai tunnistus pois päältä / domain-ajo).")

    counts = s.obs.group_by("benchmark_id").len().rename({"len": "n"})
    bparams = (
        pl.DataFrame({"benchmark_id": res.benchmark_ids, "b": res.b, "a": res.a, "c": res.c})
        .join(counts, on="benchmark_id")
        .join(
            pl.DataFrame(
                repo._run("SELECT benchmark_id, domain FROM benchmarks"),
                schema=["benchmark_id", "domain"],
                orient="row",
            ),
            on="benchmark_id",
        )  # fmt: skip
        .sort("b")
    )
    lo, hi = float(np.min(res.theta)), float(np.max(res.theta))
    extrap = bparams.filter((pl.col("b") < lo - 2) | (pl.col("b") > hi + 2))
    flat = bparams.filter(pl.col("a") < 0.35)
    lines += ["", "## 5. Benchmarkien parametrit (järjestetty vaikeuden b mukaan)", ""]
    notes = []
    if extrap.height:
        notes.append(
            f"{extrap.height} testin b on yli 2 yksikköä havaitun θ-alueen [{lo:.1f}, {hi:.1f}] "
            f"ulkopuolella ({', '.join(extrap['benchmark_id'])}) – b on ekstrapolaatio."
        )
    if flat.height:
        notes.append(
            f"{flat.height} testin erottelukyky a < 0.35 ({', '.join(flat['benchmark_id'])}): "
            "testi kertoo θ:sta vähän. Tyypillinen syy on alle 1:n todellinen katto "
            "(epäselvät tehtävät), jolloin 2PL loiventaa a:ta ja siirtää b:tä; älä tulkitse "
            "näiden b:tä vaikeutena. Korjaus: raw_max todelliseen kattoon (backlog)."
        )
    if notes:
        lines += [f"**Huom:** {n}" for n in notes] + [""]
    lines += [_table(bparams, 2)]

    extreme = pl.DataFrame({"model_id": res.model_ids, "theta": res.theta}).filter(
        (pl.col("theta") - pl.col("theta").median()).abs() > 6
    )
    if extreme.height:
        lines += ["", "**Äärimmäiset θ (> 6 mediaanista):** "
                  + ", ".join(f"{m} ({t:.1f})" for m, t in extreme.iter_rows())]  # fmt: skip

    scores = pl.DataFrame(
        {"model_id": res.model_ids, "theta": res.theta,
         "se": res.theta_se if res.theta_se is not None else np.nan}
    ).join(s.obs.group_by("model_id").len().rename({"len": "n_obs"}), on="model_id")  # fmt: skip
    lines += [
        "",
        "## 6. Kärki (θ ± SE)",
        "",
        _table(scores.sort("theta", descending=True).head(20), 2),
    ]

    lines += ["", "## 7. Järjestyksen uskottavuus: vertailu Epoch ECI:hin (A10)"]
    eci = load_eci(paths)
    if eci is None:
        lines.append("ECI-tiedostoa ei löydy (aja `forecaster ingest epoch_ai`).")
    else:
        cmp = compare_external(res.theta_by_model(), eci)
        ok = cmp["spearman"] is not None and cmp["spearman"] > 0.9
        lines.append(
            f"- Yhteisiä malleja {cmp['n']}, Spearman **{cmp['spearman']:.3f}** "
            f"({'OK, tavoite > 0.9' if ok else 'ALLE TAVOITTEEN 0.9'})"
        )
        lines.append(
            "- Huom: ECI on IRT-malli samasta Epoch-datasta, joten tämä on toistettavuuden "
            "tarkistus, ei riippumaton validointi."
        )
        common = scores.filter(pl.col("model_id").is_in(list(eci)))
        if common.height >= 3:
            ranked = (
                common.with_columns(
                    eci=pl.col("model_id").replace_strict(eci, return_dtype=pl.Float64)
                )
                .with_columns(
                    rank_theta=pl.col("theta").rank(descending=True),
                    rank_eci=pl.col("eci").rank(descending=True),
                )
                .with_columns(rank_diff=(pl.col("rank_theta") - pl.col("rank_eci")).abs())
            )
            lines += ["", "Suurimmat järjestyserot:", "",
                      _table(ranked.sort("rank_diff", descending=True).head(10)
                             .select("model_id", "theta", "se", "n_obs", "eci", "rank_theta",
                                     "rank_eci"), 2)]  # fmt: skip

    if stability != "none":
        benches = (
            res.benchmark_ids
            if stability == "all"
            else [b for b in KEY_BRIDGES if b in res.benchmark_ids]
        )
        lobo = leave_one_benchmark_out(
            s.obs, res.config, benches, full=res,
            min_model_obs=3, min_benchmark_obs=5,
        )  # fmt: skip
        ok_share = lobo["share_z_within_1_96"].drop_nulls()
        worst = lobo.drop_nulls("share_z_within_1_96").head(1)
        summary_line = (
            f"Heikoin: **{worst['benchmark_id'][0]}** ({worst['share_z_within_1_96'][0]:.2f}); "
            f"mediaani yli testien {ok_share.median():.2f}."
            if worst.height
            else ""
        )
        lines += [
            "",
            "## 8. Vakaus: yksi benchmark kerrallaan pois",
            "Tunnusluvut vain malleille, jotka on testattu poistetulla benchmarkilla (`n_tested`). "
            "z = Δθ / √|SE²_lobo − SE²_full| (Hausman); jos poisto ei muuta mallia "
            "systemaattisesti, z ~ N(0,1) ja `share_z_within_1_96` ≈ 0.95. "
            "`median_shift_se` = Δθ / SE_full (havainnollinen). "
            "`untested_median_shift_se` = muiden mallien muutos (asteikon siirtymä).",
            "",
            summary_line,
            "",
            _table(lobo, 2),
        ]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
