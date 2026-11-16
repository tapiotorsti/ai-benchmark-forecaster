# Datakokeilu: Epoch AI -benchmarkdata

_Generoitu: `scripts/spike_epoch_bridges.py`. Johtopäätökset: TOTEUTUSSUUNNITELMA.md._

> Data: [Epoch AI](https://epoch.ai/data), lisenssi [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Tämä raportti on johdettu aineistosta: tulokset on muunnettu ja yhdistelty siltamallianalyysia varten.

Pistemäärät ovat Epochin ECI-muunnoksen mukaisia: (raaka − random_baseline) / (score_ceiling − random_baseline).

## 1. Yleiskuva
- Malleja: **262**, benchmarkeja: **59**, havaintoja: **2957**
- Matriisin täyttöaste: **19.1%** (puuttuu 80.9%)
- Mallien julkaisupäivät: 2023-02-24 – 2026-11-09, puuttuvia päivämääriä: 0
- model_metadata.csv: 1227 versionimeä → 594 mallia (valmis pohja aliastaululle, A2)

Lisätyt raakatiedostot (eivät ole Epochin ECI-aineistossa):
- SWE-Bench verified (swe_bench_verified.csv, sarake `Best score (across scorers)`): 35 riviä, 0 ilman vastinetta model_metadata.csv:ssä
- HLE (hle_external.csv, sarake `Accuracy`): 56 riviä, 0 ilman vastinetta model_metadata.csv:ssä

### Benchmarkien määrä per malli
- mediaani 10, min 1, max 31
- malleja, joilla < 3 benchmarkia: **6** (θ-estimaatti hyvin epävarma)

## 2. Benchmarkit ja saturaatio
`share_ge_0.9` = osuus malleista, joiden tulos ≥ 0.9.

| benchmark | bench_date | n_models | median | max | share_ge_0.9 |
|---|---|---|---|---|---|
| LAMBADA | 2016-06-20 | 20 | 0.733 | 0.798 | 0 |
| TriviaQA | 2017-05-09 | 32 | 0.784 | 0.876 | 0 |
| ARC AI2 | 2018-03-14 | 64 | 0.446 | 0.937 | 0.0469 |
| OpenBookQA | 2018-09-08 | 31 | 0.432 | 0.84 | 0 |
| HellaSwag | 2019-05-19 | 51 | 0.708 | 0.937 | 0.0196 |
| Winogrande | 2019-07-24 | 61 | 0.46 | 0.784 | 0 |
| ANLI | 2019-10-31 | 9 | 0.328 | 0.371 | 0 |
| ARC-AGI | 2019-11-05 | 91 | 0.8 | 0.985 | 0.352 |
| PIQA | 2019-11-26 | 41 | 0.624 | 0.774 | 0 |
| MMLU | 2020-09-07 | 92 | 0.583 | 0.841 | 0 |
| MATH level 5 | 2021-03-05 | 75 | 0.632 | 0.981 | 0.213 |
| GSM8K | 2021-10-27 | 53 | 0.517 | 0.92 | 0.0566 |
| BBH | 2022-10-17 | 39 | 0.367 | 0.856 | 0 |
| GPQA diamond | 2023-11-20 | 176 | 0.777 | 0.993 | 0.284 |
| OSWorld | 2024-04-11 | 8 | 0.534 | 0.721 | 0 |
| SWE-Bench verified | 2024-08-13 | 32 | 0.74 | 0.835 | 0 |
| Cybench | 2024-08-15 | 19 | 0.2 | 0.93 | 0.0526 |
| SimpleBench | 2024-10-31 | 95 | 0.452 | 0.939 | 0.0316 |
| Balrog | 2024-11-20 | 29 | 0.326 | 0.683 | 0 |
| The Agent Company | 2024-12-18 | 13 | 0.114 | 0.429 | 0 |
| OTIS Mock AIME 2024-2025 | 2024-12-19 | 169 | 0.84 | 1 | 0.373 |
| Aider polyglot | 2024-12-21 | 44 | 0.542 | 0.88 | 0 |
| WeirdML | 2025-01-16 | 116 | 0.456 | 0.936 | 0.0345 |
| HLE | 2025-01-23 | 46 | 0.179 | 0.548 | 0 |
| VPCT | 2025-01-30 | 26 | 0.102 | 0.865 | 0 |
| Fiction.LiveBench | 2025-02-21 | 50 | 0.639 | 0.972 | 0.1 |
| FrontierMath-2025-02-28-Private | 2025-02-28 | 34 | 0.0726 | 0.684 | 0 |
| GeoBench | 2025-03-01 | 44 | 0.735 | 0.89 | 0 |
| METR Time Horizons | 2025-03-19 | 33 | 0.566 | 0.789 | 0 |
| ARC-AGI-2 | 2025-03-24 | 90 | 0.371 | 0.95 | 0.1 |
| CadEval | 2025-04-22 | 14 | 0.45 | 0.74 | 0 |
| Terminal Bench | 2025-05-19 | 32 | 0.414 | 0.847 | 0 |
| GSO-Bench | 2025-05-29 | 28 | 0.167 | 0.882 | 0 |
| DeepResearch Bench | 2025-06-13 | 24 | 0.467 | 0.553 | 0 |
| FrontierMath-Tier-4-2025-07-01-Private | 2025-07-11 | 26 | 0.0347 | 0.312 | 0 |
| SimpleQA Verified | 2025-09-09 | 88 | 0.446 | 0.94 | 0.0114 |
| GDPval | 2025-09-25 | 11 | 0.348 | 0.497 | 0 |
| Remote Labor Index | 2025-10-29 | 17 | 0.0583 | 0.413 | 0 |
| Chess Puzzles | 2025-12-10 | 87 | 0.337 | 0.779 | 0 |
| APEX-Agents | 2026-01-21 | 58 | 0.52 | 0.842 | 0 |
| ProofBench | 2026-01-30 | 71 | 0.39 | 1 | 0.141 |
| CL-bench | 2026-02-03 | 18 | 0.19 | 0.279 | 0 |
| PostTrainBench | 2026-03-11 | 19 | 0.315 | 0.447 | 0 |
| CL-bench Life | 2026-04-29 | 12 | 0.134 | 0.222 | 0 |
| ExploitBench | 2026-05-13 | 7 | 0.236 | 0.474 | 0 |
| FrontierCode | 2026-06-08 | 42 | 0.419 | 0.546 | 0 |
| FrontierMath-Tiers-1-3-v2-Private | 2026-06-12 | 90 | 0.622 | 1 | 0.167 |
| FrontierMath-Tier-4-v2-Private | 2026-06-12 | 72 | 0.305 | 1 | 0.111 |
| Surface Evolver Bench | 2026-06-19 | 24 | 0.553 | 0.95 | 0.125 |
| OSWorld 2.0 | 2026-06-26 | 9 | 0.13 | 0.314 | 0 |
| MirrorCode | 2026-06-26 | 39 | 0.456 | 0.978 | 0.0769 |
| EBR-bench | 2026-07-01 | 31 | 0.305 | 0.905 | 0.0323 |
| Mystery Game Puzzles | 2026-07-30 | 82 | 0.163 | 0.89 | 0 |
| DTBench | 2026-08-12 | 150 | 0.684 | 0.987 | 0.193 |
| LMCA | 2026-08-12 | 126 | 0.441 | 0.831 | 0 |
| FrontierSWE | 2026-09-02 | 23 | 0.302 | 0.699 | 0 |
| WeirdML v3 | 2026-09-18 | 26 | 0.23 | 0.601 | 0 |
| Furniture Assembly | 2026-09-23 | 39 | 0.179 | 0.857 | 0 |
| SpireClimb | 2026-10-14 | 9 | 0.0917 | 0.34 | 0 |

## 3. Siltamallit vanhojen ja uusien testien välillä
Vanha = benchmark julkaistu ennen 2023-01-01, uusi = 2025-01-01 tai myöhemmin.

- Malleja, joilla ≥ 1 vanha **ja** ≥ 1 uusi tulos: **139**
- Malleja, joilla ≥ 2 vanhaa **ja** ≥ 2 uutta tulosta: **50**
- Malleja, joilla vain vanhoja: 60, vain uusia: 62

Vahvimmat siltamallit:

| model | n_old | n_mid | n_new | model_date |
|---|---|---|---|---|
| Gemini 3.1 Pro | 1 | 5 | 24 | 2026-02-19 |
| GPT-5.5 | 1 | 4 | 24 | 2026-04-23 |
| Claude Opus 4.5 | 1 | 7 | 23 | 2025-11-24 |
| Claude Fable 5.1 | 1 | 2 | 23 | 2026-09-01 |
| Claude Opus 4.6 | 1 | 5 | 23 | 2026-02-05 |
| GPT-5 | 2 | 6 | 22 | 2025-08-07 |
| GPT-5.2 | 1 | 4 | 23 | 2025-12-11 |
| Claude Opus 4.8 | 1 | 3 | 22 | 2026-05-28 |
| GPT-5.6 Sol | 1 | 4 | 22 | 2026-07-09 |
| Claude Fable 5 | 1 | 3 | 22 | 2026-06-09 |
| GPT-6 Astra | 1 | 4 | 22 | 2026-09-03 |
| GPT-5.4 | 1 | 3 | 22 | 2026-03-05 |
| Claude Opus 4.7 | 1 | 4 | 21 | 2026-04-16 |
| Claude Sonnet 4.5 | 2 | 6 | 20 | 2025-09-29 |
| o3 | 2 | 6 | 19 | 2025-04-16 |

### Yhteiset mallit määrittelyn esimerkkitestien välillä
Solu = mallien määrä, joilla on tulos molemmista. Tavoite suunnitelmassa ≥ 5–10.

| vanha \ uusi | GPQA diamond | SWE-Bench verified | HLE | FrontierMath-Tiers-1-3-v2-Private | FrontierMath-2025-02-28-Private |
|---|---|---|---|---|---|
| MMLU | 40 | 1 | 3 | 4 | 6 |
| GSM8K | 9 | 0 | 0 | 1 | 0 |
| HellaSwag | 7 | 0 | 0 | 0 | 1 |
| MATH level 5 | 75 | 8 | 18 | 15 | 20 |

## 4. Kytkentäanalyysi
- Yhtenäisiä komponentteja: **1**
- Suurin komponentti: 262 mallia, 59 benchmarkia

### Heikosti kiinnittyneet benchmarkit
Benchmarkit, joiden paras yksittäinen yhteys johonkin vanhempaan testiin on < 5 mallia:

| benchmark | julkaistu | malleja | vahvin_linkki_vanhempaan | yhteisiä |
|---|---|---|---|---|
| ARC-AGI | 2019-11-05 | 91 | ARC AI2 | 0 |
