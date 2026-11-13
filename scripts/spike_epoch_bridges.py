"""Datakokeilu: riittääkö Epoch AI -data θ-asteikon yhdistämiseen?

Kertakäyttöinen analyysi ennen K1/K3:a. Lukee puretun Epoch AI -ZIPin ja kirjoittaa
markdown-raportin. Ei kirjoita tietokantaan.

Käyttö:
    .venv\\Scripts\\python.exe scripts\\spike_epoch_bridges.py data\\raw\\epoch_ai\\<pvm>\\extracted
        [--out docs\\datakokeilu_epoch_ai.md]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import polars as pl

OLD_CUTOFF = date(2023, 1, 1)  # "vanhat" testit: julkaistu ennen GPT-4-aikaa
NEW_CUTOFF = date(2025, 1, 1)  # "uudet" testit
SATURATION = 0.9

# Raakatiedostot, jotka eivät ole ECI-aineistossa mutta ovat määrittelyn kannalta keskeisiä.
EXTRA_RAW = {
    "SWE-Bench verified": ("swe_bench_verified.csv", "Best score (across scorers)"),
    "HLE": ("hle_external.csv", "Accuracy"),
}
FOCUS_OLD = ["MMLU", "GSM8K", "HellaSwag", "MATH level 5"]
FOCUS_NEW = [
    "GPQA diamond",
    "SWE-Bench verified",
    "HLE",
    "FrontierMath-Tiers-1-3-v2-Private",
    "FrontierMath-2025-02-28-Private",
]


def load(extracted: Path) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, list[str]]:
    notes: list[str] = []
    obs = pl.read_csv(
        extracted / "epoch_capabilities_index" / "processed_data_for_eci.csv", try_parse_dates=True
    ).select(
        pl.col("Model").alias("model"),
        pl.col("date").alias("model_date"),
        "benchmark",
        pl.col("benchmark_release_date").alias("bench_date"),
        "performance",
    )
    bmeta = pl.read_csv(extracted / "benchmark_metadata.csv", try_parse_dates=True)
    mmeta = pl.read_csv(extracted / "model_metadata.csv", infer_schema_length=0).filter(
        pl.col("model_version").is_not_null()
    )

    # ECI-datan Model-nimi vastaa model_metadata.csv:n model_group-kenttää.
    version_to_group = mmeta.select("model_version", "model_group", "date").unique("model_version")

    for bench, (fname, col) in EXTRA_RAW.items():
        raw = pl.read_csv(extracted / fname, infer_schema_length=10_000)
        if col is None:
            numeric = [
                c
                for c, t in raw.schema.items()
                if t.is_numeric() and c not in {"Training compute (FLOP)", "stderr", "Shots"}
            ]
            col = numeric[0]
        raw = raw.select(pl.col("Model version").alias("model_version"), pl.col(col).alias("score"))
        joined = raw.join(version_to_group, on="model_version", how="left")
        unmapped = joined.filter(pl.col("model_group").is_null())
        notes.append(
            f"{bench} ({fname}, sarake `{col}`): {raw.height} riviä, "
            f"{unmapped.height} ilman vastinetta model_metadata.csv:ssä"
            + (
                f" (esim. {', '.join(unmapped['model_version'].head(5).to_list())})"
                if unmapped.height
                else ""
            )
        )
        meta = bmeta.filter(pl.col("benchmark") == bench)
        bdate = meta["release_date"]
        # ECI-aineiston performance = (raaka - random_baseline) / (score_ceiling - random_baseline).
        # Sama muunnos raakatiedostoille, jotta luvut ovat vertailukelpoisia.
        base, ceil = float(meta["random_baseline"][0]), float(meta["score_ceiling"][0])
        raw = raw.with_columns(
            ((pl.col("score").cast(pl.Float64) - base) / (ceil - base)).clip(0.0, 1.0)
        )
        mapped = (
            joined.filter(pl.col("model_group").is_not_null())
            .group_by("model_group")
            .agg(pl.col("score").max(), pl.col("date").first())
            .select(
                pl.col("model_group").alias("model"),
                pl.col("date").str.to_date(strict=False).alias("model_date"),
                pl.lit(bench).alias("benchmark"),
                pl.lit(bdate[0] if len(bdate) else None).cast(pl.Date).alias("bench_date"),
                pl.col("score").cast(pl.Float64).alias("performance"),
            )
        )
        obs = pl.concat([obs, mapped]).unique(["model", "benchmark"], keep="first")
    return obs, bmeta, mmeta, notes


def connected_components(obs: pl.DataFrame) -> list[set[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for m, b in obs.select("model", "benchmark").iter_rows():
        ra, rb = find("M:" + m), find("B:" + b)
        if ra != rb:
            parent[ra] = rb
    comps: dict[str, set[str]] = {}
    for node in list(parent):
        comps.setdefault(find(node), set()).add(node)
    return sorted(comps.values(), key=len, reverse=True)


def md_table(df: pl.DataFrame) -> str:
    cols = df.columns
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.iter_rows():
        cells = [f"{v:.3g}" if isinstance(v, float) else ("" if v is None else str(v)) for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def analyse(obs: pl.DataFrame, bmeta: pl.DataFrame, mmeta: pl.DataFrame, notes: list[str]) -> str:
    out: list[str] = []
    n_models, n_bench = obs["model"].n_unique(), obs["benchmark"].n_unique()
    density = obs.height / (n_models * n_bench)

    out += [
        "# Datakokeilu: Epoch AI -benchmarkdata",
        "",
        "_Generoitu: `scripts/spike_epoch_bridges.py`. Johtopäätökset: TOTEUTUSSUUNNITELMA.md._",
        "",
        "Pistemäärät ovat Epochin ECI-muunnoksen mukaisia: "
        "(raaka − random_baseline) / (score_ceiling − random_baseline).",
        "",
        "## 1. Yleiskuva",
        f"- Malleja: **{n_models}**, benchmarkeja: **{n_bench}**, havaintoja: **{obs.height}**",
        f"- Matriisin täyttöaste: **{density:.1%}** (puuttuu {1 - density:.1%})",
        f"- Mallien julkaisupäivät: {obs['model_date'].min()} – {obs['model_date'].max()}, "
        f"puuttuvia päivämääriä: {obs.filter(pl.col('model_date').is_null())['model'].n_unique()}",
        f"- model_metadata.csv: {mmeta.height} versionimeä → "
        f"{mmeta['model_group'].n_unique()} mallia (valmis pohja aliastaululle, A2)",
        "",
        "Lisätyt raakatiedostot (eivät ole Epochin ECI-aineistossa):",
        *[f"- {n}" for n in notes],
        "",
    ]

    per_model = obs.group_by("model").len()
    q = per_model["len"]
    out += [
        "### Benchmarkien määrä per malli",
        f"- mediaani {q.median():.0f}, min {q.min()}, max {q.max()}",
        f"- malleja, joilla < 3 benchmarkia: **{per_model.filter(pl.col('len') < 3).height}** "
        "(θ-estimaatti hyvin epävarma)",
        "",
    ]

    # --- Saturaatio
    sat = (
        obs.group_by("benchmark", "bench_date")
        .agg(
            pl.len().alias("n_models"),
            pl.col("performance").median().alias("median"),
            pl.col("performance").max().alias("max"),
            (pl.col("performance") >= SATURATION).mean().alias("share_ge_0.9"),
        )
        .sort("bench_date")
    )
    out += [
        "## 2. Benchmarkit ja saturaatio",
        f"`share_ge_0.9` = osuus malleista, joiden tulos ≥ {SATURATION}.",
        "",
        md_table(sat),
        "",
    ]

    # --- Siltamallit
    tagged = obs.with_columns(
        pl.when(pl.col("bench_date") < OLD_CUTOFF)
        .then(pl.lit("old"))
        .when(pl.col("bench_date") >= NEW_CUTOFF)
        .then(pl.lit("new"))
        .otherwise(pl.lit("mid"))
        .alias("era")
    )
    era_counts = tagged.group_by("model").agg(
        (pl.col("era") == "old").sum().alias("n_old"),
        (pl.col("era") == "mid").sum().alias("n_mid"),
        (pl.col("era") == "new").sum().alias("n_new"),
        pl.col("model_date").first(),
    )
    bridge1 = era_counts.filter((pl.col("n_old") >= 1) & (pl.col("n_new") >= 1))
    bridge2 = era_counts.filter((pl.col("n_old") >= 2) & (pl.col("n_new") >= 2))
    out += [
        "## 3. Siltamallit vanhojen ja uusien testien välillä",
        f"Vanha = benchmark julkaistu ennen {OLD_CUTOFF}, uusi = {NEW_CUTOFF} tai myöhemmin.",
        "",
        f"- Malleja, joilla ≥ 1 vanha **ja** ≥ 1 uusi tulos: **{bridge1.height}**",
        f"- Malleja, joilla ≥ 2 vanhaa **ja** ≥ 2 uutta tulosta: **{bridge2.height}**",
        f"- Malleja, joilla vain vanhoja: "
        f"{era_counts.filter((pl.col('n_old') > 0) & (pl.col('n_new') == 0)).height}, "
        f"vain uusia: {era_counts.filter((pl.col('n_old') == 0) & (pl.col('n_new') > 0)).height}",
        "",
        "Vahvimmat siltamallit:",
        "",
        md_table(
            bridge1.with_columns((pl.col("n_old") + pl.col("n_new")).alias("_s"))
            .sort("_s", descending=True)
            .drop("_s")
            .head(15)
        ),
        "",
    ]

    # --- Kohdistetut parit
    present = set(obs["benchmark"].unique())
    rows = []
    sets = {b: set(obs.filter(pl.col("benchmark") == b)["model"]) for b in FOCUS_OLD + FOCUS_NEW}
    for o in FOCUS_OLD:
        row = {"vanha \\ uusi": o}
        for n in FOCUS_NEW:
            row[n] = len(sets[o] & sets[n]) if o in present and n in present else None
        rows.append(row)
    out += [
        "### Yhteiset mallit määrittelyn esimerkkitestien välillä",
        "Solu = mallien määrä, joilla on tulos molemmista. Tavoite suunnitelmassa ≥ 5–10.",
        "",
        md_table(pl.DataFrame(rows)),
        "",
    ]

    # --- Kytkentä
    comps = connected_components(obs)
    out += [
        "## 4. Kytkentäanalyysi",
        f"- Yhtenäisiä komponentteja: **{len(comps)}**",
        f"- Suurin komponentti: {sum(1 for x in comps[0] if x.startswith('M:'))} mallia, "
        f"{sum(1 for x in comps[0] if x.startswith('B:'))} benchmarkia",
    ]
    for c in comps[1:6]:
        out.append(f"- Irrallinen: {', '.join(sorted(x[2:] for x in c))}")
    out.append("")

    # Kunkin benchmarkin vahvin linkki aiemmin julkaistuun benchmarkiin
    bsets = {b: set(g["model"]) for (b,), g in obs.group_by("benchmark")}
    bdates = dict(obs.select("benchmark", "bench_date").unique().iter_rows())
    link_rows = []
    for b, ms in bsets.items():
        older = [x for x in bsets if x != b and bdates[x] and bdates[b] and bdates[x] < bdates[b]]
        best = max(older, key=lambda x: len(ms & bsets[x]), default=None)
        link_rows.append(
            {
                "benchmark": b,
                "julkaistu": bdates[b],
                "malleja": len(ms),
                "vahvin_linkki_vanhempaan": best,
                "yhteisiä": len(ms & bsets[best]) if best else 0,
            }
        )
    links = pl.DataFrame(link_rows).sort("yhteisiä")
    weak = links.filter((pl.col("yhteisiä") < 5) & pl.col("vahvin_linkki_vanhempaan").is_not_null())
    out += [
        "### Heikosti kiinnittyneet benchmarkit",
        "Benchmarkit, joiden paras yksittäinen yhteys johonkin vanhempaan testiin on < 5 mallia:",
        "",
        md_table(weak) if weak.height else "_Ei yhtään._",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("extracted", type=Path)
    ap.add_argument("--out", type=Path, default=Path("docs/datakokeilu_epoch_ai.md"))
    args = ap.parse_args()

    obs, bmeta, mmeta, notes = load(args.extracted)
    report = analyse(obs, bmeta, mmeta, notes)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
