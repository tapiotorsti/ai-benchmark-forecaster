# IRT-diagnostiikka

_Generoitu 2026-11-14 komennolla `forecaster fit --report`. Ajo `irt_overall_20261114T005945_a57a2632`._

> Data: [Epoch AI](https://epoch.ai/data), lisenssi [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Tämä raportti on johdettu aineistosta: tulokset on normalisoitu, aggregoitu ja niihin on sovitettu IRT-malli.

## 1. Ajo
- Domain: **overall**
- Mallit 337, benchmarkit 60, havainnot 3347
- Konvergoi: **True** (gradientti 9.6e-06, 699 iteraatiota)
- Ankkurit (fixed): gpt-4-jun-2023 = 0, gpt-5 = 3.5
- Hajontaparametri φ (diagnostiikka): 0.0305
- Ei varoituksia (Hessian positiividefiniitti, ankkurit löytyivät)

## 2. Karsinta
- Pudotettu 111 mallia (alle rajan tuloksia) ja 1 benchmarkia: superglue
- Pudotetut mallit: ai-co-mathematician, amazon-nova-micro, bloom-176b, chutes-glm-4-5-fp8, chutes-qwen3-next-80b-a3b-instruct, claude-1-3, claude-mythos-preview, codestral, codex-mini-2025-05-16, composer-2-5, computer-use-preview-2025-03-11, davinci-003, dbrx, deepinfra-qwen3-next-80b-a3b-instruct, deepseek-coder-v2-lite-instruct, deepseek-r1-0528-qwen3-8b, deepseek-r1-distill-qwen-1-5b, deepseek-v2-5-sep-2024, deepseek-v3-1-terminus, deepseek-v3-2-speciale, dracarys2-72b-instruct, dracarys2-llama-3-1-70b-instruct, eurus-2-7b-prime, gemini-2-0-flash-lite, gemini-2-5-deep-think, gemini-2-5-flash-lite-sep-2025, gemini-3-deep-think, gemini-robotics-er-1-5-preview, gemma-3-1b, glm-4-5, glm-4-5-air, gpt-3-5-turbo-instruct, gpt-3-6-7b, gpt-3-medium, gpt-3-xl-babbage, gpt-4o-jan-2025, gpt-4o-mar-2025, gpt-5-1-codex, gpt-5-1-codex-mini, gpt-5-2-codex … (+71)

## 3. Kytkentä ja siltamallit
- Yhtenäisiä komponentteja: **1** (kaikki samalla asteikolla)
- Siltamallit (≥1 vanha [<2023] ja ≥1 uusi [≥2025] testi): **155**, vahvat (≥2+2): 50
- Vain vanhoja: 96, vain uusia: 84

## 4. Poikkeamat (määrittely 4.3, A4)
52 havaintoa liputettu (|robusti z| > 3 σ-ekvivalenttia, z = (r − mediaani) / (1.4826·MAD) benchmarkin sisällä): 10 high, 42 low. **Sovituksesta poistettu 9** (vain high = mahdollinen kontaminaatio, enintään 1 per malli, ei karsintarajan alle); sovitus ajettu uudelleen kerran.

`low`-tulokset jätetään sovitukseen: ne voivat olla aitoa heikkoutta, jota yksiulotteinen malli ei selitä. Tarkista ne käsin ennen mahdollista poistoa.

| model_id | benchmark_id | y | yhat | resid | robust_z | direction | excluded |
|---|---|---|---|---|---|---|---|
| claude-opus-4-5 | vpct | 0.400 | 0.651 | -0.527 | -7.980 | low | False |
| chatglm2-6b | lambada | 0.543 | 0.713 | -0.377 | -6.445 | low | False |
| qwen3-235b-a22b | math_level_5 | 0.689 | 0.877 | -0.576 | -5.893 | low | False |
| deepseek-r1-distill-qwen-32b | livebench | 0.455 | 0.652 | -0.412 | -5.870 | low | False |
| gpt-5-nano | lmca | 0.093 | 0.312 | -0.472 | -5.798 | low | False |
| gpt-4-1-mini | cadeval | 0.160 | 0.390 | -0.471 | -5.476 | low | False |
| chatglm2-6b | piqa | 0.696 | 0.790 | -0.230 | -5.179 | low | False |
| claude-3-5-sonnet-october-2024 | math_level_5 | 0.569 | 0.764 | -0.457 | -4.708 | low | False |
| llama-3-1-8b | mmlu | 0.561 | 0.723 | -0.363 | -4.635 | low | False |
| gpt-4-jun-2023 | lmca | 0.201 | 0.090 | 0.388 | 4.526 | high | True |
| phi-2 | hellaswag | 0.536 | 0.780 | -0.588 | -4.483 | low | False |
| minimax-m3 | otis_mock_aime | 0.711 | 0.892 | -0.580 | -4.418 | low | False |
| qwen-3-8-27b | geobench | 0.380 | 0.746 | -0.839 | -4.318 | low | False |
| gemini-3-pro | vpct | 0.910 | 0.767 | 0.339 | 4.308 | high | True |
| gemini-2-0-flash-dec-2024 | mmlu | 0.797 | 0.897 | -0.328 | -4.196 | low | False |
| gemini-1-5-flash-sep-2024 | mmlu | 0.739 | 0.854 | -0.326 | -4.169 | low | False |
| deepseek-r1-distill-qwen-14b | gpqa_diamond | 0.461 | 0.662 | -0.426 | -4.161 | low | False |
| qwen-1-8b | lambada | 0.584 | 0.689 | -0.227 | -4.142 | low | False |
| qwen3-8b | lmca | 0.104 | 0.245 | -0.329 | -4.085 | low | False |
| gpt-oss-120b | otis_mock_aime | 0.889 | 0.627 | 0.542 | 3.938 | high | True |
| claude-opus-4 | math_level_5 | 0.850 | 0.939 | -0.373 | -3.876 | low | False |
| gpt-5-nano | proofbench | 0.120 | 0.012 | 0.973 | 3.860 | high | True |
| claude-sonnet-4-6 | otis_mock_aime | 0.858 | 0.959 | -0.505 | -3.855 | low | False |
| phi-2 | triviaqa | 0.452 | 0.734 | -0.638 | -3.852 | low | False |
| instructgpt-175b | lambada | 0.864 | 0.737 | 0.288 | 3.804 | high | True |
| phi-2 | winogrande | 0.547 | 0.736 | -0.429 | -3.723 | low | False |
| claude-3-5-sonnet-october-2024 | otis_mock_aime | 0.085 | 0.309 | -0.486 | -3.713 | low | False |
| qwen2-5-coder-0-5b | hellaswag | 0.484 | 0.703 | -0.478 | -3.692 | low | False |
| gpt-5-nano | dtbench | 0.627 | 0.768 | -0.336 | -3.690 | low | False |
| gpt-5-6-terra | simplebench | 0.489 | 0.738 | -0.566 | -3.576 | low | False |
| claude-sonnet-4 | geobench | 0.370 | 0.685 | -0.677 | -3.563 | low | False |
| gpt-5-4 | deepresearch_bench | 0.351 | 0.492 | -0.282 | -3.525 | low | False |
| deepseek-r1-distill-llama-70b | livebench | 0.545 | 0.656 | -0.233 | -3.519 | low | False |
| gpt-2-1-5b | metr_time_horizons | 0.101 | 0.034 | 0.375 | 3.487 | high | True |
| qwen-1-8b | mmlu | 0.282 | 0.415 | -0.270 | -3.455 | low | False |
| internlm-chat-20b | mmlu | 0.574 | 0.437 | 0.275 | 3.441 | high | False |
| claude-sonnet-4 | math_level_5 | 0.844 | 0.927 | -0.322 | -3.369 | low | False |
| glm-5-3 | otis_mock_aime | 0.911 | 0.977 | -0.438 | -3.362 | low | False |
| glm-5-2 | otis_mock_aime | 0.864 | 0.955 | -0.438 | -3.360 | low | False |
| stable-beluga-2 | lambada | 0.713 | 0.785 | -0.176 | -3.355 | low | False |

## 5. Benchmarkien parametrit (järjestetty vaikeuden b mukaan)

**Huom:** 6 testin erottelukyky a < 0.35 (triviaqa, lambada, piqa, hellaswag, geobench, deepresearch_bench): testi kertoo θ:sta vähän. Tyypillinen syy on alle 1:n todellinen katto (epäselvät tehtävät), jolloin 2PL loiventaa a:ta ja siirtää b:tä; älä tulkitse näiden b:tä vaikeutena. Korjaus: raw_max todelliseen kattoon (backlog).

| benchmark_id | b | a | c | n | domain |
|---|---|---|---|---|---|
| triviaqa | -8.40 | 0.15 | 0.00 | 36 | general |
| lambada | -7.70 | 0.18 | 0.00 | 24 | general |
| piqa | -5.07 | 0.15 | 0.50 | 56 | general |
| hellaswag | -4.76 | 0.29 | 0.25 | 64 | general |
| openbookqa | -2.09 | 0.74 | 0.25 | 41 | general |
| gsm8k | -1.91 | 0.99 | 0.00 | 66 | math |
| arc_ai2 | -1.87 | 0.80 | 0.25 | 75 | general |
| winogrande | -1.44 | 0.36 | 0.50 | 76 | general |
| mmlu | -1.32 | 0.59 | 0.25 | 118 | general |
| bbh | -1.02 | 0.76 | 0.25 | 45 | reasoning |
| geobench | -0.61 | 0.25 | 0.00 | 44 | general |
| anli | 0.44 | 0.86 | 0.33 | 11 | general |
| math_level_5 | 0.80 | 1.54 | 0.00 | 91 | math |
| livebench | 1.00 | 0.69 | 0.00 | 40 | general |
| fiction_livebench | 1.68 | 1.11 | 0.00 | 51 | general |
| gpqa_diamond | 1.69 | 1.09 | 0.26 | 210 | reasoning |
| metr_time_horizons | 1.77 | 0.47 | 0.00 | 36 | agentic |
| swe_bench_verified | 1.81 | 0.58 | 0.00 | 32 | coding |
| dtbench | 1.89 | 0.88 | 0.40 | 173 | reasoning |
| otis_mock_aime | 2.02 | 1.80 | 0.00 | 199 | math |
| aider_polyglot | 2.10 | 1.44 | 0.00 | 48 | coding |
| cadeval | 2.25 | 0.83 | 0.00 | 14 | coding |
| arc_agi | 2.91 | 1.94 | 0.00 | 94 | reasoning |
| weirdml | 2.93 | 0.72 | 0.00 | 123 | coding |
| cybench | 3.00 | 1.24 | 0.00 | 20 | agentic |
| osworld | 3.01 | 1.18 | 0.00 | 8 | agentic |
| the_agent_company | 3.01 | 1.28 | 0.00 | 14 | agentic |
| terminal_bench | 3.30 | 1.48 | 0.00 | 38 | agentic |
| frontiermath_2025_02 | 3.44 | 1.91 | 0.00 | 69 | math |
| surface_evolver_bench | 3.47 | 1.25 | 0.00 | 24 | reasoning |
| frontiermath_t1_3_v2 | 3.49 | 1.60 | 0.00 | 92 | math |
| simplebench | 3.61 | 0.84 | 0.17 | 99 | reasoning |
| lmca | 3.63 | 0.67 | 0.00 | 138 | reasoning |
| vpct | 3.65 | 2.48 | 0.33 | 24 | reasoning |
| arc_agi_2 | 3.91 | 2.32 | 0.00 | 92 | reasoning |
| balrog | 3.94 | 0.43 | 0.00 | 32 | agentic |
| proofbench | 4.10 | 2.70 | 0.00 | 75 | math |
| gdpval | 4.11 | 0.69 | 0.00 | 11 | agentic |
| apex_agents | 4.14 | 0.80 | 0.00 | 59 | agentic |
| simpleqa_verified | 4.17 | 0.68 | 0.00 | 94 | general |
| frontiermath_t4_v2 | 4.35 | 2.31 | 0.00 | 73 | math |
| frontiermath_tier4_2025_07 | 4.36 | 2.02 | 0.00 | 52 | math |
| gso_bench | 4.46 | 1.71 | 0.00 | 29 | coding |
| deepresearch_bench | 4.49 | 0.13 | 0.00 | 24 | agentic |
| mirrorcode | 4.50 | 1.36 | 0.00 | 39 | coding |
| chess_puzzles | 4.65 | 1.07 | 0.05 | 92 | reasoning |
| mystery_game_puzzles | 4.87 | 1.60 | 0.09 | 84 | reasoning |
| frontiercode | 4.92 | 0.76 | 0.00 | 42 | coding |
| ebr_bench | 4.92 | 1.71 | 0.00 | 31 | reasoning |
| exploitbench | 4.99 | 0.93 | 0.00 | 8 | agentic |
| hle | 5.02 | 0.94 | 0.05 | 43 | reasoning |
| frontierswe | 5.08 | 1.33 | 0.00 | 23 | coding |
| furniture_assembly | 5.10 | 2.66 | 0.30 | 39 | reasoning |
| weirdml_v3 | 5.38 | 1.40 | 0.00 | 26 | coding |
| osworld_2 | 5.56 | 1.35 | 0.00 | 9 | agentic |
| posttrainbench | 6.27 | 0.46 | 0.00 | 19 | agentic |
| remote_labor_index | 6.30 | 1.40 | 0.00 | 17 | agentic |
| cl_bench_life | 6.38 | 0.64 | 0.00 | 13 | general |
| spireclimb | 6.39 | 1.82 | 0.00 | 9 | agentic |
| cl_bench | 7.01 | 0.41 | 0.00 | 19 | general |

**Äärimmäiset θ (> 6 mediaanista):** cerebras-gpt-13b (-5.2), deepseek-coder-1-3b (-5.6), gpt-2-1-5b (-8.8), gpt-j-6b (-4.5), gpt-neo-2-7b (-8.4), opt-1-3b (-8.5), opt-13b (-4.3), stablelm-tuned-alpha-7b (-9.3)

## 6. Kärki (θ ± SE)

| model_id | theta | se | n_obs |
|---|---|---|---|
| claude-fable-5-2 | 5.92 | 0.26 | 19 |
| gemini-4-carbon | 5.57 | 0.21 | 14 |
| gpt-6-astra | 5.52 | 0.20 | 27 |
| claude-opus-5-5 | 5.47 | 0.19 | 23 |
| gpt-6-1-terra | 5.41 | 0.18 | 13 |
| gpt-6-1-sol | 5.38 | 0.20 | 21 |
| claude-fable-5-1 | 5.21 | 0.17 | 26 |
| gemini-4-argon | 5.17 | 0.16 | 22 |
| claude-sonnet-5-5 | 5.15 | 0.19 | 21 |
| claude-opus-5 | 5.00 | 0.16 | 24 |
| claude-fable-5 | 4.95 | 0.16 | 26 |
| gpt-6-sol | 4.88 | 0.16 | 20 |
| gpt-5-6-sol | 4.81 | 0.16 | 27 |
| gpt-5-5-pro | 4.78 | 0.15 | 12 |
| kimi-k3-1-pro | 4.69 | 0.15 | 13 |
| muse-wonder | 4.67 | 0.14 | 13 |
| gpt-5-4-pro | 4.53 | 0.13 | 12 |
| gpt-5-6-terra | 4.53 | 0.14 | 20 |
| gpt-5-5 | 4.49 | 0.15 | 31 |
| claude-haiku-5-5 | 4.48 | 0.18 | 20 |

## 7. Järjestyksen uskottavuus: vertailu Epoch ECI:hin (A10)
- Yhteisiä malleja 256, Spearman **0.999** (OK, tavoite > 0.9)
- Huom: ECI on IRT-malli samasta Epoch-datasta, joten tämä on toistettavuuden tarkistus, ei riippumaton validointi.

Suurimmat järjestyserot:

| model_id | theta | se | n_obs | eci | rank_theta | rank_eci |
|---|---|---|---|---|---|---|
| qwen3-8b | 1.92 | 0.31 | 5 | 132.09 | 140.00 | 152.00 |
| mistral-medium-3-5 | 2.74 | 0.31 | 6 | 141.59 | 102.00 | 114.00 |
| gpt-oss-120b | 2.04 | 0.21 | 12 | 140.16 | 137.00 | 125.00 |
| palm-2-l | -0.96 | 1.71 | 4 | 114.10 | 206.00 | 195.00 |
| deepseek-v3-1 | 2.15 | 0.37 | 5 | 140.85 | 131.00 | 120.00 |
| qwen-7b | -1.88 | 0.50 | 6 | 89.22 | 221.00 | 231.00 |
| internlm-20b | -1.28 | 0.40 | 6 | 99.09 | 208.00 | 218.00 |
| nemotron-3-ultra | 2.93 | 0.13 | 10 | 146.95 | 93.00 | 84.00 |
| qwen3-14b | 2.17 | 0.29 | 5 | 136.14 | 129.00 | 138.00 |
| qwen3-30b-a3b-instruct-jul-2025 | 2.09 | 0.46 | 4 | 135.15 | 133.00 | 142.00 |

## 8. Vakaus: yksi benchmark kerrallaan pois
Tunnusluvut vain malleille, jotka on testattu poistetulla benchmarkilla (`n_tested`). z = Δθ / √|SE²_lobo − SE²_full| (Hausman); jos poisto ei muuta mallia systemaattisesti, z ~ N(0,1) ja `share_z_within_1_96` ≈ 0.95. `median_shift_se` = Δθ / SE_full (havainnollinen). `untested_median_shift_se` = muiden mallien muutos (asteikon siirtymä).

Heikoin: **math_level_5** (0.98); mediaani yli testien 0.99.

| benchmark_id | n_tested | median_z | share_z_within_1_96 | median_shift_se | max_shift | untested_median_shift_se | n_dropped |
|---|---|---|---|---|---|---|---|
| math_level_5 | 81 | 0.30 | 0.98 | 0.35 | 0.39 | 0.02 | 8 |
| gpqa_diamond | 196 | 0.40 | 0.99 | 0.16 | 0.27 | 0.09 | 12 |
