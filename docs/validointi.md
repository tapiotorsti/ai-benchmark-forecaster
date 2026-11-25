# θ-asteikon validointi (A36)

_Generoitu 2026-11-25 komennolla `forecaster validate`. Data: sama havaintomatriisi kuin tuotantoajossa (poikkeamat pois; viimeisin IRT-ajo `irt_overall_20261123T082225990742_6ff11b99`), 3427 havaintoa._

> **Lähteet.** Benchmark-data: [Epoch AI](https://epoch.ai/data), lisenssi [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Raportti on johdettu aineistosta: tulokset on normalisoitu, aggregoitu ja niihin on sovitettu IRT-malli.

## Mitä tämä mittaa
ECI-vertailu (A10) on toistettavuuden tarkistus, koska ECI on Epochin IRT-tyyppinen malli pääosin samasta datasta. Tässä mitataan, ennustaako θ-asteikko tuloksia, joita sovitus ei nähnyt. Mittarit ovat normalisoidulla asteikolla (tulos suhteessa testin kattoon, 0–1). IRT ennusti parhaiten (RMSE 0.089 vs. 1PL-tyyppinen 0.114 ja testin keskiarvo 0.233). Validointi on aineiston sisäinen: se kertoo, kuinka hyvin yksi θ per malli ja kaksi parametria per testi selittävät havaitut tulokset, mutta ei sitä, että θ mittaisi yleistä kyvykkyyttä datan ulkopuolella.

Rajoitukset: piilotetut tulokset ovat satunnaisotos havaituista, vaikka todellinen puuttuvuus ei ole satunnaista (todennäköisesti koska testejä ajetaan valikoiden). Poikkeamat, karsinta ja ankkurit on päätetty koko datalla, mikä voi tehdä tuloksesta hieman optimistisen. Kylmäkäynnistyksen kalibrointimallit arvotaan satunnaisesti koko θ-alueelta; uusia testejä ajetaan tyypillisesti ensin kärkimalleille, joten kylmäkäynnistyksen tulos on todennäköisesti optimistinen (hypoteesi).

## 1. Pois jätetyt tulokset (5-osainen ristiinvalidointi)
Havainnot jaetaan satunnaisesti 5 osaan; kukin osa ennustetaan muilla sovitetulla mallilla. Havaintoa ei arvioida, jos mallille tai testille jäisi alle karsintarajan (3 / 5) opetushavaintoja. Arvioituja havaintoja 3201 (300 mallia, 60 testiä). Vertailukohdat sovitetaan samoihin opetushavaintoihin: 1PL-tyyppinen malli = c + (1 − c)·σ(μ + m_i + d_j) samalla BCE-häviöllä ja leikkauksella kuin IRT, mutta yhteisellä erottelukyvyllä (kevyt ridge); testin keskiarvo = ei tietoa mallista. IRT:n ja 1PL:n ero mittaa siis testikohtaisen erottelukyvyn hyötyä (IRT:n priorit ja optimointi eroavat hieman). RMSE (testit) = testikohtaisten RMSE:iden keskiarvo. Konvergoitumattomia IRT-sovituksia 0 / 5.

| menetelmä | RMSE | RMSE (testit) | MAE |
|---|---|---|---|
| IRT (2PL) | 0.089 | 0.086 | 0.063 |
| 1PL-tyyppinen additiivinen malli | 0.114 | 0.108 | 0.087 |
| testin keskiarvo | 0.233 | 0.189 | 0.187 |

Osa-alueittain (RMSE):

| osa-alue | n | IRT | 1PL-tyyppinen | testin keskiarvo |
|---|---|---|---|---|
| agentic | 292 | 0.084 | 0.121 | 0.164 |
| coding | 368 | 0.098 | 0.113 | 0.207 |
| general | 718 | 0.093 | 0.118 | 0.156 |
| math | 679 | 0.103 | 0.133 | 0.316 |
| reasoning | 1144 | 0.074 | 0.096 | 0.238 |

Pisteennusteen osuvuus desiileittäin: ennusteen desiilit, ennusteen ja havainnon keskiarvo. Myös testin keskiarvon suurin desiiliero on vain 0.04 (lokerot ryhmittelevät testejä), joten taulukko ei yksin todista erottelukykyä; ennustevälejä se ei mittaa.

| desiili | IRT: ennuste | havaittu | testin keskiarvo: ennuste | havaittu |
|---|---|---|---|---|
| 1 | 0.07 | 0.08 | 0.22 | 0.24 |
| 2 | 0.20 | 0.20 | 0.35 | 0.34 |
| 3 | 0.31 | 0.32 | 0.41 | 0.44 |
| 4 | 0.40 | 0.40 | 0.45 | 0.44 |
| 5 | 0.49 | 0.49 | 0.49 | 0.51 |
| 6 | 0.58 | 0.58 | 0.55 | 0.54 |
| 7 | 0.68 | 0.68 | 0.59 | 0.63 |
| 8 | 0.76 | 0.75 | 0.64 | 0.63 |
| 9 | 0.85 | 0.84 | 0.71 | 0.73 |
| 10 | 0.95 | 0.95 | 0.77 | 0.78 |

## 2. Uuden testin kylmäkäynnistys (5 kalibrointimallia)
Testi kerrallaan pois: IRT sovitetaan ilman sitä, ja testin parametrit sovitetaan 5 satunnaisen mallin tuloksiin (θ tunnettu; 20 arvontaa). Muiden mallien tulokset ennustetaan. `vain b` = erottelukyky kiinnitetty muiden testien mediaaniin. Vertailukohta: kalibrointimallien keskiarvo. Testikohtaiset luvut ovat mediaaneja arvontojen yli. Testit, joilla on alle 10 tunnetun θ:n mallia, ohitetaan (3 kpl); pienen `arvioitu`-määrän rivit ovat kohinaisia.

Testejä 57, arvontoja 1140. 2PL parempi kuin keskiarvo 0.86 testeistä, vain b 0.82. 2PL huonompi kuin vain b 0.29 arvonnoista (todennäköisesti koska kaksi parametria estimoidaan 5 pisteestä; hypoteesi). Konvergoitumattomia IRT-sovituksia 0, epäonnistuneita testin sovituksia 0.

| RMSE | 2PL | vain b | kalibrointimallien keskiarvo |
|---|---|---|---|
| testikohtaisten mediaanien mediaani | 0.092 | 0.109 | 0.196 |
| keskiarvo kaikista arvonnoista | 0.105 | 0.121 | 0.206 |
| P90 kaikista arvonnoista | 0.164 | 0.189 | 0.328 |

| testi | arvioitu | 2PL | vain b | keskiarvo |
|---|---|---|---|---|
| aider_polyglot | 40 | 0.139 | 0.143 | 0.246 |
| anli | 6 | 0.081 | 0.084 | 0.082 |
| apex_agents | 59 | 0.115 | 0.096 | 0.176 |
| arc_agi | 92 | 0.081 | 0.137 | 0.330 |
| arc_agi_2 | 90 | 0.113 | 0.193 | 0.395 |
| arc_ai2 | 70 | 0.124 | 0.124 | 0.208 |
| balrog | 26 | 0.088 | 0.149 | 0.173 |
| bbh | 36 | 0.070 | 0.065 | 0.196 |
| cadeval | 9 | 0.113 | 0.110 | 0.200 |
| chess_puzzles | 91 | 0.106 | 0.096 | 0.219 |
| cl_bench | 14 | 0.078 | 0.065 | 0.043 |
| cl_bench_life | 8 | 0.050 | 0.036 | 0.048 |
| cybench | 14 | 0.105 | 0.123 | 0.272 |
| deepresearch_bench | 19 | 0.088 | 0.147 | 0.064 |
| dtbench | 158 | 0.055 | 0.049 | 0.175 |
| ebr_bench | 31 | 0.100 | 0.142 | 0.299 |
| fiction_livebench | 45 | 0.142 | 0.132 | 0.194 |
| frontiercode | 37 | 0.098 | 0.083 | 0.139 |
| frontiermath_2025_02 | 62 | 0.085 | 0.129 | 0.302 |
| frontiermath_t1_3_v2 | 91 | 0.074 | 0.106 | 0.301 |
| frontiermath_t4_v2 | 72 | 0.110 | 0.173 | 0.333 |
| frontiermath_tier4_2025_07 | 46 | 0.063 | 0.106 | 0.196 |
| frontierswe | 21 | 0.080 | 0.085 | 0.211 |
| furniture_assembly | 38 | 0.096 | 0.154 | 0.244 |
| gdpval | 6 | 0.102 | 0.098 | 0.129 |
| geobench | 39 | 0.139 | 0.209 | 0.149 |
| gpqa_diamond | 197 | 0.053 | 0.054 | 0.240 |
| gsm8k | 54 | 0.181 | 0.178 | 0.283 |
| gso_bench | 23 | 0.092 | 0.130 | 0.282 |
| hellaswag | 58 | 0.123 | 0.180 | 0.136 |
| hle | 37 | 0.094 | 0.067 | 0.165 |
| lambada | 19 | 0.109 | 0.151 | 0.075 |
| livebench | 33 | 0.072 | 0.082 | 0.169 |
| lmca | 128 | 0.059 | 0.077 | 0.215 |
| math_level_5 | 78 | 0.089 | 0.125 | 0.322 |
| metr_time_horizons | 29 | 0.043 | 0.102 | 0.156 |
| mirrorcode | 36 | 0.204 | 0.189 | 0.250 |
| mmlu | 106 | 0.072 | 0.109 | 0.198 |
| mystery_game_puzzles | 82 | 0.085 | 0.112 | 0.239 |
| openbookqa | 36 | 0.091 | 0.093 | 0.190 |
| otis_mock_aime | 187 | 0.106 | 0.144 | 0.404 |
| piqa | 49 | 0.082 | 0.106 | 0.051 |
| posttrainbench | 16 | 0.093 | 0.099 | 0.091 |
| proofbench | 68 | 0.140 | 0.199 | 0.340 |
| remote_labor_index | 14 | 0.051 | 0.052 | 0.135 |
| simplebench | 98 | 0.099 | 0.092 | 0.231 |
| simpleqa_verified | 93 | 0.127 | 0.130 | 0.211 |
| spireclimb | 8 | 0.155 | 0.124 | 0.137 |
| surface_evolver_bench | 19 | 0.150 | 0.136 | 0.236 |
| swe_bench_verified | 27 | 0.072 | 0.076 | 0.113 |
| terminal_bench | 30 | 0.101 | 0.117 | 0.243 |
| the_agent_company | 9 | 0.072 | 0.069 | 0.157 |
| triviaqa | 32 | 0.143 | 0.200 | 0.116 |
| vpct | 19 | 0.084 | 0.096 | 0.148 |
| weirdml | 114 | 0.084 | 0.094 | 0.219 |
| weirdml_v3 | 22 | 0.054 | 0.076 | 0.191 |
| winogrande | 71 | 0.068 | 0.100 | 0.099 |
