# Toteutussuunnitelma: AI Benchmark Aggregator & Forecaster

Pohjana: [ai_benchmark_aggregator_forecaster_tekninen_m_rittely.md](ai_benchmark_aggregator_forecaster_tekninen_m_rittely.md)

Suunnitelma jakaa työn **kahdeksaan kokonaisuuteen (K0–K7)**. Jokainen kokonaisuus tuottaa jotain valmista ja testattua, jonka päälle seuraava rakentuu. Järjestys on valittu niin, että vaikein ja riskialttein osa (IRT-moottori) validoidaan synteettisellä datalla ennen kuin oikeaa dataa tuodaan sisään.

```text
K0 Perusta ──► K1 Tietokanta ──► K2 IRT-moottori ──► K4 Ankkurointi & laadunvalvonta ──► K5 Ennusteet ──► K6 Dashboard ──► K7 Automaatio
                     │                                      ▲
                     └──────► K3 Datankeruu ────────────────┘
```

K2 ja K3 voidaan tehdä rinnakkain, koska K2 käyttää vain synteettistä dataa.

## Tila

| Kokonaisuus | Tila | Huomiot |
|---|---|---|
| K0 Perusta | valmis | 14 testiä läpi. Paketti src-layoutissa (`src/forecaster/`). Polun ä-kirjaimet vaativat korjausskriptin `scripts/fix_editable_pth.py`. |
| K1 Tietokanta | valmis | 12 taulua, 24 repository-testiä. Hinnat ovat vain `model_prices`-taulussa (`models`-taulusta poistettu, A1). `evaluations` säilyttää versiotason (A9). DuckDB 2.x: tulokset luetaan aina loppuun (ks. CLAUDE.md). |
| K2 IRT-moottori | valmis | 18 nopeaa ja 3 Monte Carlo -testiä. math-reviewer-katselmointi on tehty ja sen varmat löydökset 1–3 ja 5 on korjattu (4 = tunnettu rajoitus, ks. A13). 262 × 59 -kokoinen ajo kestää noin 3 s. Avoimena K4:lle: osa-aluekohtaiset ajot pitää ankkuroida overall-ajon θ-arvoihin, koska ne eivät ole ilman ankkureita vertailukelpoisia (A12). Lisäksi konvergenssikriteeri on vielä absoluuttinen eikä suhteellinen |
| K3 Datankeruu | valmis | Lähteet `epoch_ai`, `openrouter` ja `internal`. LiveBench tulee Epochin aineiston mukana, joten sille ei tarvita omaa adapteria. Oikea data: 61 benchmarkia, 579 mallia (448 mallilla tuloksia), 4 687 tulosta ja hinta 148 mallille (141 näistä on myös tuloksia). Ensimmäinen IRT-ajo konvergoi, ja **Spearman ECI:hin on 0.998** (256 yhteistä mallia). 13 ingest-testiä ilman verkkoa. |
| K4 Ankkurointi & QA | valmis | Päätökset 2026-11-13: mallilla ≥ 3 tulosta (benchmarkilla ≥ 5), ankkurit GPT-4 (Jun 2023) = 0 ja GPT-5 = 3.5, toistuvista tuloksista paras. Oikea data: 334 mallia, 60 benchmarkia, 1 komponentti, 155 siltamallia, Hessian PD, ECI-Spearman 0.999. Poikkeamia liputettiin 52 (10 high, 42 low), joista 9 high poistettiin. LOBO testatuilla malleilla: mediaani 0.98 \|z\| < 1.96, heikoin gso_bench 0.81. Domain-ajot kiinteillä osioparametreilla. math-reviewer-katselmoinnin löydökset 1–7 ja 13 korjattu. **Backlog:** benchmark-kohtainen todellinen katto (lambada, triviaqa, piqa, hellaswag: a < 0.35), studentisoidut residuaalit, äärimmäiset θ pienimmillä vanhoilla malleilla (gpt-2, opt-1.3b: θ ≈ −9). Arvioidut domainit `(?)` ovat edelleen tarkistamatta. Vanha muistiinpano: K3:n jälkeen avoimena: (1) Oikealla datalla Hessian ei ole positiividefiniitti (pienin ominaisarvo −2.9e-6). Jokin parametri on heikosti identifioitu, ja syy pitää selvittää. (2) 108 mallilla 448:sta on vain 1–2 tulosta, joten pitää päättää vähimmäishavaintomäärä tai merkitä nämä mallit epävarmoiksi. (3) Toistuvista tuloksista otetaan paras, mikä yhdistää myös eri few-shot-asetukset; arvioi, onko tämä oikein. (4) Domain-arviot, jotka on merkitty (?), tarkistetaan `epoch_benchmarks.yaml`:ssa. (5) Ankkurien valinta. |
| K5 Ennusteet | valmis | `forecaster forecast` → `docs/ennusteet.md`. Kyvykkyys: 1.68 θ/v, backtestissä 9/9 välin sisällä (yksi lähtöpäivä, A20). Kustannukset: kaikki 3 tasoa lattialla. Math-reviewer käsitelty. Backlog: eksplisiittinen lattiamalli, tasokohtainen lattia, θ-kohinan kaksoislaskenta (A5) |
| K6 Dashboard | valmis | `forecaster dashboard`, 6 sivua. Oikealla datalla sivut latautuvat välimuistista 0.02–0.11 s ja ensimmäisellä kerralla ≤ 14 s (yleiskuvan bootstrap). Simulaattorin uudelleensovitus vie 5–6 s. Skeemaversio 2 (tuntemattomat nimet). A21–A22 |
| K7 Automaatio | valmis | `forecaster update`: oikea ajo vei 31 s, ja IRT ajettiin kokonaisuudelle ja 5 osa-alueelle. Viikoittainen ajastus tehdään skriptillä `scripts/schedule_update.ps1`. Hälytykset näkyvät dashboardissa (`pipeline_runs`, skeemaversio 3). CI (ruff ja pytest) GitHub Actionsissa. Dokumentaatio: `docs/metodologia.md` ja `docs/uusi_datalahde.md`. A23 |

---

## Datakokeilun tulokset (Epoch AI, 2026-11-13)

Toistettava skripti: [scripts/spike_epoch_bridges.py](scripts/spike_epoch_bridges.py). Täysi raportti: [docs/datakokeilu_epoch_ai.md](docs/datakokeilu_epoch_ai.md). Lähde: [Epoch AI Benchmarking Hub](https://epoch.ai/benchmarks/use-this-data), lisenssi CC BY 4.0, joten lähde mainitaan dashboardissa.

**Johtopäätös: asteikkojen yhdistäminen on mahdollista, mutta yhdistävät testit ovat eri kuin määrittelyssä oletettiin.**

| Mittari | Tulos |
|---|---|
| Data | 262 mallia, 59 benchmarkia, 2 957 havaintoa, täyttöaste 19 % |
| Kytkentä | **1 yhtenäinen komponentti**: kaikki mallit ja testit voidaan asettaa samalle asteikolle |
| Siltamalleja (≥ 1 vanha ja ≥ 1 uusi testi) | **139**, joista 50 on vahvoja (≥ 2 + 2). Tavoite 5–10 ylittyy selvästi |
| Benchmarkeja per malli | mediaani 10; 6 mallilla alle 3 |
| Julkaisupäivät | ei puuttuvia (aukko A3 on Epochin osalta pieni) |

Mitä tämä tarkoittaa suunnitelmalle:

1. **Suorat sillat määrittelyn esimerkkitestien välillä ovat heikkoja.** MMLU:n ja SWE-bench Verifiedin yhteisiä malleja on 1, ja GSM8K:lla ei ole lähes yhtään yhteistä mallia uusien testien kanssa. Uusia malleja ei enää ajeta vanhoilla testeillä. Saturaatio näkyy siis datassa puuttuvina tuloksina eikä katossa olevina pisteinä.
2. **Asteikko kiinnittyy ketjun kautta.** Vanhat ja uudet testit yhdistyvät MATH level 5:n (75 yhteistä mallia GPQA:n kanssa, 8–20 uusien kanssa) ja GPQA Diamondin (176 mallia) kautta. Tästä syntyy riski: vanhan ja uuden aikakauden θ-ero riippuu näistä kahdesta testistä. K4:n stabiilisuustesti (yksi benchmark kerrallaan pois) on tehtävä erityisesti niille.
3. **Datassa saturoituneet testit** (≥ 0,9-tuloksia): OTIS Mock AIME 37 %, ARC-AGI 35 %, GPQA Diamond 28 %, MATH L5 21 %. Arvausrajalla korjattuna MMLU ei näy saturoituneena, koska kärkimalleja ei ole ajettu sillä.
4. **Ankkurit:** `gpt-4-0314` löytyy, mutta sillä on vain 6 tulosta, joten se on heikko ankkuri. `claude-3-5-sonnet-20240620` löytyy 12 tuloksella. Suositus: vaihdetaan GPT-4-ankkuri malliin, jolla on enemmän tuloksia, tai otetaan kolmas ankkuri (esim. Claude 3.5 Sonnet (Oct 2024), 17 tulosta). Päätetään K4:ssä.
5. Uudet aukot A8–A10 on lisätty alla olevaan taulukkoon.

---

## Määrittelyn aukot, jotka korjataan matkan varrella

Nämä havaittiin määrittelyä läpikäydessä. Ne on sijoitettu alla oleviin kokonaisuuksiin.

| # | Aukko | Miksi ongelma | Korjaus | Kokonaisuus |
|---|---|---|---|---|
| A1 | Hinnat ovat vain `models`-taulussa yhtenä arvona | Kustannusennuste (5.2) tarvitsee hinnan **ajan yli**, ja mallien hinnat muuttuvat julkaisun jälkeen | Uusi taulu `model_prices(model_id, valid_from, input, output, blended, source)` | K1 |
| A2 | Mallien nimet eroavat lähteittäin (Epoch AI vs. OpenRouter vs. LiveBench) | Ilman nimien yhdistämistä sama malli näkyy monena rivinä, ja havaintomatriisi pirstoutuu | Uusi taulu `model_aliases(alias, source_name, model_id)` ja käsin ylläpidettävä `config/model_aliases.yaml` | K1, K3 |
| A3 | `release_date NOT NULL`, mutta kaikilla lähteillä ei ole päivämäärää | Rivien tuonti kaatuu | Tuonti erilliseen karanteenitauluun, kunnes päivämäärä on täydennetty | K3 |
| A4 | Kohdan 4.3 kaava $e_{ij} = \|y - \hat{y}\|$ ei ole standardoitu, vaikka tekstissä puhutaan standardoidusta residuaalista | Saturoituneilla testeillä (y ≈ 0.95) virheet ovat luonnostaan pieniä, keskitason testeillä suuria | Käytetään logit-avaruuden residuaalia tai jaetaan $\sqrt{\hat{y}(1-\hat{y})}$:lla, ja MAD lasketaan benchmark-kohtaisesti | K4 |
| A5 | Pareto-rintama poimitaan pistearvioista, eikä $\theta$:n keskivirhettä huomioida | Epävarma malli voi päätyä rintamaan sattumalta | Bootstrapissa otetaan $\theta$ jokaisella kierroksella jakaumasta $\mathcal{N}(\theta, se^2)$, ja rintama lasketaan joka kierroksella uudelleen | K5 |
| A6 | Osa-aluekohtainen $\theta$ (`domain`) mainitaan, mutta sen laskentatapaa ei määritellä | Avoin kysymys | Ensin erilliset IRT-ajot per domain, ja myöhemmin harkitaan moniulotteista IRT:tä | K2 |
| A7 | Elo-muunnoksen perusmalli ja skaala ovat avoimia | Elo-pisteet eivät ole vertailukelpoisia ilman kiinnitystä | Kiinnitetään konfiguraatiossa (`elo_reference`, `elo_scale = 400`) | K3 |
| A8 | Epochin `processed_data_for_eci.csv` on jo muunnettu kaavalla (raaka − arvausraja) / (katto − arvausraja) | Jos IRT vähentää arvausrajan c_j uudelleen, korjaus tehdään kahdesti | Tuodaan **raakatiedostot** ja normalisoidaan itse. Epochin `random_baseline` → `guessing_floor` ja `score_ceiling` → `raw_max`. ECI-aineistoa käytetään vain vertailuun | K3 |
| A9 | Samalla mallilla on useita versioita (päättelytaso `_max`/`_high`, eri palveluntarjoajat): 1 242 versionimeä → 594 mallia, joista 455 päättyy päättelytason päätteeseen | Pitää päättää, onko IRT:n yksikkö malli vai versio | Oletus: malli = Epochin `model_group`, ja tuloksena käytetään versioiden parasta tulosta (kuten kokeilussa). Versiotaso säilytetään `evaluations`-taulussa, jotta päätöstä voi muuttaa | K1, K3 |
| A14 | Määrittely 4.3: poikkeamat poistetaan ja malli ajetaan uudelleen | Yksiulotteisen mallin residuaali ei erota mittausvirhettä aidosta kyvykkyysprofiilista. Low-poikkeamien poisto nosti θ:aa systemaattisesti (esim. Claude 3.5 Sonnet Oct 2024 +2.9 SE, kun molemmat math-tulokset poistettiin) | Liputetaan molemmat suunnat robustilla z:lla (3 σ-ekvivalenttia = 3 · 1.4826 · MAD, benchmark-kohtainen, n ≥ 12). Sovituksesta poistetaan **vain high**, enintään 1 per malli, eikä koskaan karsintarajan alle. Liput ja ajo kirjoitetaan yhdessä transaktiossa | K4 |
| A15 | A12:n domain-linkitys PNS-regressiolla | α = cov/var kutistaa domain-asteikkoa korrelaatiokertoimen verran (coding 0.95) | Domain-ajot käyttävät **kiinteitä osioparametreja** overall-ajosta (`IRTConfig.fixed_items`). θ estimoidaan suoraan overall-asteikolla, eikä linkitysmuunnosta tarvita | K4 |
| A16 | Määrittely 5.2: kunkin kuukauden halvin malli + log-lineaarinen regressio | (a) Kuukausisarja on porrasfunktio, ja toistuvat arvot kaventavat luottamusvälin keinotekoisesti. (b) Hinta osui lattiaan (~0.04–0.05 $/1M) jo 2024–25, ja puhdas eksponenttimalli ennusti backtestissä ~100× liian halvan hinnan | Regressio **hintaennätyksiin** + sensuroidut pisteet (porrasfunktion arvo saranakuukautena ja nyt) + **palainen/lattiatesti** (sarana 12 kk, p < 0.05). **Huom (K5-katselmointi):** rajattu backtest-virhe 0.21 johtuu A17:n lattiarajauksesta (P50 = lattia), ei regressiosta. Rajaamaton virhe on GPT-4-tasolla 0.43 ja Sonnet-tasolla 4.10, ja molemmat raportoidaan. Sensuroidut pisteet ovat heuristiikka, eivät tilastollisesti sensuroituja havaintoja. Backlog: eksplisiittinen lattiamalli ln c = ln(f + A·e^{−αt}) tai sensuroitu uskottavuus | K5 |
| A20 | K5-katselmointi: mallivalinta (lineaarinen/palainen) vain piste-estimaatilla | Bootstrap aliarvioi rakenne-epävarmuuden. Useasta lähtöpäivästä ajettu backtest kattoi 0.33–1.00, ja virheet olivat samanmerkkisiä | Kyvykkyysbootstrap valitsee mallin **jokaisessa drawissa** (t-testi drawin uniikeilla pisteillä; kaksoiskappaleilla väärä valinta ~30 %). Puoliintumisajan persentiilit lasketaan kaikista drawista, ja ei-laskeva draw on ∞. `add_months` leikkaa kuun lopun. Ennuste rajataan as_of-päivään | K5 |
| A17 | Ennuste voi pudota alle kaiken havaitun tai nousta | Halvin saatavilla oleva hinta on ei-kasvava, ja markkinalla on lattia | Ennuste rajataan väliin [markkinan halvin hinta millä tahansa tasolla, tason nykyinen halvin]. Tila `at_floor`, kun halvin ≤ 1.5 × lattia, ja "aika lattiaan" -arvio (päätös 2026-11-14) | K5 |
| A18 | Suuri Wald-SE heikosti määrätyille malleille (vähän tuloksia tai heikosti erottelevia testejä; ääritapauksessa tulokset arvausrajalla) | SE jopa 11.8 (llama-3-2-1b: 3 tulosta, joista 2 arvausrajalla) → bootstrap-draw nosti mallin rintamaan ja P90 räjähti (14.8) | Ennusteista pois mallit, joiden θ-SE > `max_theta_se` (1.0); raportissa listataan. Lattiatulosten sensurointi IRT:ssä arvioitiin 2026-11-23 ja jätettiin tekemättä (päätös): 21 mallista, joilla SE > 1, yhdelläkään kaikki tulokset eivät ole arvausrajalla ja vain kahdella vähintään puolet. Näille malleille on tyypillistä vähäinen tulosmäärä (20/21:llä 3–5) ja heikosti erottelevat testit (a:n mediaani 0.34–0.79, kun SE ≤ 1 -malleilla 1.10). Sama yhdistelmä esiintyy kuitenkin 30 mallilla, joiden SE ≤ 1, joten syy on hypoteesi (todennäköisesti myös θ:n sijainti testien vaikeuksiin nähden vaikuttaa). Ääritapaus llama-3-2-1b on arvausrajatapaus. Mallit on jätetty pois ennustesyötteestä, eikä yksikään niistä ole rintamaehdokas (julkaistu 2019–2024, pieniä tai vanhoja); IRT-sovituksessa ne ovat mukana. Sen sijaan dashboardin Mallit-sivu merkitsee ne epävarmoiksi | K5 |
| A19 | llm-prices: päivämäärätön tunniste (`gpt-4o`) ja vanhat versiot | Nykyhinta kirjautuisi vanhalle versiolle (liian halpa) | Päivämäärätön → **uusin** päivätty Epoch-versio. Päiväyspääte (`-20240620`, `-2505`) valitsee version, jonka julkaisupäivä on lähimpänä (enintään 180 pv). `-latest` ilman from_datea kohdistetaan vain päivättyyn versioon. Vanhojen versioiden julkaisuhinnat tulevat tiedostosta `config/manual_prices.yaml` (lähde `manual`, 7 mallia, myös mistral-large). Kontekstitasot (`-272k`) ohitetaan | K5 |
| A21 | K6: "saturaatioaste" on määrittelemättä | Benchmarkit-sivu tarvitsee mittarin | Saturaatio = σ(a(θ_max − b)), eli parhaan mallin odotettu osuus arvausrajan yläpuolisesta välistä. Kun arvo on ≥ 0.9, testi ei enää erottele kärkimalleja. Simulaattori ei laske SE:tä (nopeus), ja ankkurit pidetään kiinteinä, jotta asteikot ovat vertailukelpoisia | K6 |
| A22 | K6: Polars 2.1 + Torch/BLAS OpenMP Streamlitin säikeissä | Prosessi kaatui Windowsissa (access violation) toistuvasti sivujärjestyksellä yleiskuva → mallit → benchmarkit → kustannukset | `POLARS_MAX_THREADS=1` asetetaan dashboardissa (`app/dashboard.py`, CLI) ja testeissä (`tests/conftest.py`) ennen polarsin tuontia. Välimuisti on `st.cache_resource` (ei pickle-sarjallistusta) | K6 |
| A23 | K7: ajastettu ajo GitHub Actionsissa, tietokanta artefaktina | Tietokanta ja dashboard ovat paikallisia (Tailscale). llm-prices-dataa (ei lisenssiä) ei saa tallentaa julkisen repon artefakteihin. DuckDB:n tiedostolukko on poissulkeva prosessien välillä | `forecaster update` ajetaan omalla koneella Windowsin Tehtävien ajoituksella (`scripts/schedule_update.ps1`). Tulos ja hälytykset tallentuvat tauluun `pipeline_runs` (skeemaversio 3) ja näkyvät dashboardissa. GitHub Actions ajaa vain CI:n. Päivitys avaa kannan vaihe kerrallaan ja odottaa lukkoa, ja dashboard näyttää päivityksen aikana viestin "päivitys käynnissä". FastAPI-rajapintaa ei tehty, koska integraatiotarvetta ei ole | K7 |
| A24 | Kalibraatio mitattiin yhdeltä lähtöpäivältä | Saman lähtöpäivän virheet ovat samanmerkkisiä, joten 9/9 osumaa on käytännössä yksi havainto | Liukuva backtest (`backtest_capability_rolling`): lähtöpäivät as_of − 6/12/18/24 kk (`backtest_origins_months`). Raportoidaan kaikkien pisteiden osuus välillä, lähtöpäivien keskiarvo ± SE, vaihteluväli ja uniikkien pisteiden määrä. Oikea data 2026-11-16: 0.78 (lähtöpäivät 0.00–1.00, 15 uniikkia pistettä). Katselmoinnin tulkinta: otantahajonta on ±0.10, joten luku ei erota arvoa 0.8 arvosta ~0.5. Kuun lopun reunatapaus on korjattu | K5+ |
| A25 | Bootstrapissa θ* ~ N(θ̂, SE²) syötettiin myös sovitukseen | Mittausvirhe laskettiin kahdesti (se on jo jäännöksissä), ja rintaman uudelleenpoiminta siirsi mediaania ylöspäin. Synteettisesti SE 0.3: harha +0.56, kattavuus 0.60 | θ* ratkaisee vain rintamajäsenyyden, ja sovitus tehdään alkuperäisiin θ̂:hin. SE 0.3: harha +0.31. Katselmointi vahvisti: bootstrapin mediaani ≈ pistekohtainen OLS, eli bootstrap ei tuo omaa harhaa. Jäljelle jäävä noin +0.3 on rintamavalintaa ja koskee todellista θ:aa. Havaittuihin ennätyksiin (backtestin kohde) nähden ennuste on harhaton. Backlog: pienen otoksen välin korjaus √(n/(n−p)) tai wild-bootstrap, ja kutistus, jos kohteeksi halutaan todellinen θ | K5+ |
| A26 | Kustannusennusteen alaraja oli koko markkinan halvin malli | Malli voi olla tasoa paljon heikompi (ministral-3b, θ −0.4), jolloin lattia on tason kannalta merkityksetön | **Horisonttikohtainen** alaraja (päätös 2026-11-16, katselmoinnin jälkeen): h kk:n alaraja on halvin malli, jolle taso − β·h/12 ≤ θ < taso. β on rintaman pistekohtainen kulmakerroin samasta aineistosta, joten backtestiin ei vuoda tulevaa dataa. Raja otetaan käyttöön vain, jos malli on selvästi halvempi (< nykyinen / 1.5). Muuten käytetään koko markkinaa. Tila at_floor ja aika lattiaan lasketaan aina koko markkinasta. Ensimmäinen versio (kiinteä δ = 1, "mikä tahansa halvempi") jäädytti ennusteen: katselmoinnissa 2 %:n hintaero muutti 24 kk:n ennusteen 4.3-kertaiseksi ja käänsi tilan at_flooriksi. Backtest raportoi, alittiko toteuma koulutetun alarajan. Oikealla datalla ei tällä hetkellä vaikutusta, koska sama malli (glm-5-3-flash, θ 3.8, listahinta 0.05 $) on halvin kaikilla tasoilla | K5+ |
| A27 | Pisteittäinen bootstrap aliarvioi kertoimien hajonnan pienellä otoksella | Synteettinen Monte Carlo 2026-11-17 (lineaarinen tosi trendi, kohina 0.25, θ-SE 0.15; 250 toistoa; 80 %:n ennustevälin kattavuus tuleville rintamapisteille 12 kk): rintama ~9 pistettä 0.72 ± 0.02 (tavoite 0.80), ~16 pistettä 0.81 ± 0.02 | Korjaus `small_sample` (forecast.yaml): drawin kertoimet skaalataan piste-estimaatin ympäriltä ja ennustekohina kertoimella √(n/(n−p)) (n = drawin rintamapisteet). Tulos: ~9 pistettä 0.80 ± 0.02, ~16 pistettä 0.85 ± 0.02 (näyttää ylikattavan). Oletus `auto` (päätös 2026-11-17): korjaus vain, kun rintamassa on alle 12 pistettä (raja on päätös, ei mitattu taitekohta). Oikealla datalla (sama as_of 2026-11-17) liukuvan backtestin kalibraatio: none 0.75 ± 0.25, auto 0.88 ± 0.12. Rintaman koko 2026-11-17: kokonaisennuste 21, liukuvan backtestin lähtöpäivät 6 / 9 / 12 / 16, osa-alueet 8–23. Raportti näyttää, millä lähtöpäivillä korjaus oli käytössä. **Päivitys 2026-11-23:** sovitusikkunoiden (A28) kanssa kulmakertoimen P10–P90 kattaa tasaisessa kehityksessä 0.88 (rintama ~12 pistettä) – 0.92 (~20 pistettä), eli hieman leveä; samassa kokeessa ilman ikkunoita 0.69 / 0.60; murrosskenaariota ei mitattu; OLS-, HC3- ja lohkobootstrap-välit 0.48–0.72 (200 toistoa). Alkuperäinen havainto ilman ikkunoita: kulmakertoimen P10–P90 kattaa todellisen β:n vain ~0.7 kummassakin tilassa. Math-reviewer (150 toistoa, pakotettu lineaarinen malli): syy on välin kapeus (bootstrapin sd 0.106 vs. estimaattorin todellinen sd 0.128), ei harha (kattavuus estimaattorin omaan keskiarvoon nähden 0.68). Hypoteesi (ei todennettu): rintaman pisteet eivät ole i.i.d. (ennätysvalinta), joten pisteittäisen bootstrapin vaje on suurempi kuin n/(n−p) | K5+ |
| A28 | Kyvykkyystrendi sovitettiin aina koko jaksoon (2023-01 alkaen) | Lähtöpäivän 2024-11 kulmakerroin oli 0.79 θ/v. Vuodelta 2023 rintamassa vain 2 pistettä (0.39 → 0.58); lähtöpäivään mennessä julkaistuissa vuoden 2024 pisteissä vauhti ~1.7 θ/v (4 pistettä; joulukuun o1 mukaan lukien 2.2). 12 kk:n saranatesti ei tunnistanut muutosta | Sovitusikkunan epävarmuus (`fit_windows_months`, päätös 2026-11-18: 18/24/36): jokaisessa drawissa sovitus alkaa satunnaisesti (yhtä suuret painot) fit_startista tai as_of − w kk; pois ikkunat, jotka alkaisivat ennen fit_startia tai joiden rintamassa < min_points + 1 pistettä; lyhyissä ikkunoissa ei saranatestiä eikä historiakaistan ekstrapolointia ikkunaa edeltävään aikaan; pienen otoksen korjaus ikkunan oman rintaman koon mukaan. Synteettinen Monte Carlo (200 toistoa, ~16 pisteen rintama, ± ~0.02): tasainen kehitys 0.82 → 0.87 (yli tavoitteen), kiihtyminen 15 kk ennen lähtöpäivää 0.70 → 0.81. Vaihtoehdot: 24 kk 0.84 / 0.77, 12/18/24/36 kk 0.89 / 0.83. Ikkunat valittiin samalla skenaariolla kuin hidas testi → ei riippumaton validointi. Lähtöpäivällä 2024-11 vain 18 kk:n ikkuna on käytettävissä; virhe yhä ~+0.9 θ. Ei ennakoi ennustepäivän jälkeistä muutosta | K5+ |
| A29 | A28:n jälkeen otsikkokulmakerroin (koko jakson OLS, 1.68) ja ennusteiden jakauma (P50 1.84) erosivat, ja A26:n lattia käytti koko jakson arvoa | Kyvykkyys- ja kustannusennuste olettivat eri kehitysvauhdin samalle rintamalle (math-reviewer) | `slope_per_year` = bootstrap-jakauman mediaani; koko jakson OLS kentässä `slope_per_year_full_period` (diagnostiikka). A26:n lattia saa saman arvon (forecast_run, dashboard Kustannukset-sivu Yleiskuvan välimuistista); backtest_cost laskee sen kyvykkyysennusteesta vain cutoffiin asti julkaistulla datalla. Ilman annettua arvoa (testit) lattia käyttää koko jakson OLS:ää; lähde näkyy kentässä `frontier_slope_source`. Katselmoinnissa mediaani jäi tunnistetun murroksen jälkeen 10–20 % liian matalaksi, koska saranan yli ulottuvat ikkunat sovitettiin suoralla. Korjaus: kun koko jakson malli on palainen, saranatesti tehdään myös saranan yli ulottuvissa ikkunoissa (A28:n kattavuus säilyi: 0.87 / 0.81) | K5+ |
| A30 | Kalibraatiota arvioitiin vain takautuvalla backtestillä (4 lähtöpäivää, malli sovitetaan uudelleen) | Neljä lähtöpäivää ei riitä, eikä backtest mittaa oikeasti etukäteen tehtyjä ennusteita | Jokaisen ennusteen mukana tallennetaan kuukausikaista (as_of … as_of + 24 kk, `capability.monthly`). `prospective_calibration` vertaa tallennettuja ennusteita (uusin per ISO-viikko) niiden jälkeen julkaistuihin rintamapisteisiin julkaisupäivänä (kaista interpoloidaan kuukausipisteiden välillä). Toteuma: ennätykset koko nykydatasta nykyisen IRT-ajon θ:lla. Tulos raporttiin ja tauluun forecast_runs (kind = calibration). Seuranta alkoi 2026-11-19; aiemmista ennusteista kuukausikaista puuttuu | K5+ |
| A31 | Rintamavalinnan harha: havaitut ennätykset ovat todellista θ:aa korkeampia, ja ennuste koskee vain havaittuja ennätyksiä | Synteettisesti (100 toistoa) ennuste yliarvioi todelliset tulevat ennätykset 0.27 θ:lla (SE 0.3) ja 0.12 θ:lla (SE 0.15) | Vaihtoehtoinen "todellisen kyvykkyyden" ennuste pääennusteen rinnalla (`selection_bias`, `debias_capability`): harha parametrisella bootstrapilla SAMALLE estimaattorille kuin pääennuste (θ̂ todeksi, kohina N(0, SE²), capability_forecastin P50 kohinaisella − kohinattomalla datalla yhteisillä satunnaisluvuilla; raportti 30 × 100, dashboard 12 × 80), korjaus siirtää koko jakaumaa. Ensimmäinen versio käytti yhtä koko jakson trendimallia; math-reviewerin oraakkelikokeessa sen harha ei vastannut ikkunasekoitusta (lyhyellä ikkunalla väärä suunta), joten se korvattiin. Synteettisesti (`scripts/validate_selection_bias.py`) todellisten ennätysten keskivirhe −0.27 → −0.03 (SE 0.3) ja −0.12 → −0.06 (SE 0.15); siirretyn välin kattavuus todellisille ennätyksille 0.94 / 0.85 (hieman leveä). θ̂ sisältää kohinaa, joten harha jää yleensä hieman alakanttiin. Nykydatalla harha 0.15–0.25 θ (6–24 kk). Pääennuste pysyy oletuksena | K5+ |
| A32 | Rintamassa vähän pisteitä (nyt 23, backtestin vanhimmilla lähtöpäivillä 24–30 kk 4–6) → kulmakerroin epävarma | Koe 2026-11-23 (samat 9 lähtöpäivää 3–27 kk, 65 päällekkäistä pistettä; 30 kk:lla pääennuste ei onnistu): pääennusteen keskim. itseisvirhe 0.41 θ (3–21 kk 0.13–0.31; 24/27 kk 0.96/1.52), välin sisällä lähtöpäivien ka. 0.84 (pisteistä 0.86) | Vertailuennuste `top_k_capability_forecast` (`comparison_top_k: 3`): vauhti 3 parhaasta mallista per vuosineljännes, taso rintamasta (vakiotermi = rintamapisteiden jäännösten keskiarvo), kohina rintaman jäännöksistä. math-reviewerin löydösten jälkeen: bootstrap neljänneksittäin (pisteittäin vauhdin väli oli synteettisesti liian kapea, kattavuus 0.55–0.68 math-reviewerin kokeessa; neljänneksittäin 0.70–0.75 kertaluonteisessa kokeessa, 60 toistoa ± 0.06, asetelma kuin slow-testissä), keskeneräinen neljännes pois vauhdista (painoi sitä alas), omat kohinavirrat, raportin backtest-vertailu vain yhteisiltä lähtöpäiviltä. `scripts/compare_top_k.py` (korjattu estimaattori, as_of 2026-11-23, IRT-ajo `irt_overall_20261123T082225990742_6ff11b99`): itseisvirhe 0.14 (3–21 kk 0.10–0.18; 24/27 kk 0.13/0.21), keskivirhe +0.00, kattavuus 1.00 (väli omiin virheisiinsä nähden liian leveä: puolileveys ~0.65 vs itseisvirhe 0.14; syy selvitetty A33:ssa: GPT-4 vipupisteenä, jonka vaikutusta jyrkempi top-3-kulmakerroin kasvattaa), leveys 1.30 vs 1.47; k = 1 0.55, k = 5 0.25. Ensimmäisessä, korjaamattomassa kokeessa kvanttiiliregressio (q 0.9 0.26, q 0.75 0.22) ei ollut top-3:a parempi (kertaluonteinen, ei skriptiä). Kulmakertoimen väli kapeampi (ajo 2026-11-24: 1.82–1.94 vs 1.67–2.11), osin rakenteen vuoksi (ei ikkuna- eikä rakenne-epävarmuutta; sovitusikkunat lisätty myöhemmin, ks. A33). Nykydatalla ennusteet lähes samat (1.87 vs 1.86 θ/v, 12 kk P50 7.75 vs 7.69). Raportin liukuva backtest (4 yhteistä lähtöpäivää, 31 pistettä): itseisvirhe 0.12 vs 0.36, leveys 1.27 vs 1.49. Päätös: raportoidaan vertailuna liukuvan backtestin kanssa, pääennuste ennallaan. (Luvut ilman sovitusikkunoita; ikkunoiden jälkeen ks. A33.) | K5+ |
| A33 | Ennustevälit näyttävät oikealla datalla leveämmiltä kuin virheet (havaittu A32:ssa) | Pääennusteen kattavuus 1.00 kaikilla lähtöpäivillä 3–21 kk, vertailun kaikilla | Diagnostiikka `scripts/diagnose_interval_width.py` (as_of 2026-11-23, sama IRT-ajo kuin A32). **Pääennuste:** välin leveyttä hallitsee trendin epävarmuus (3–21 kk: trendin P10–P90 1.13 / ennusteen 1.42); trendi osui lähtöpäivillä 3–21 kk ja meni ohi 24–27 kk:lla (virhe 1–1.5, kattavuus 0.57 / 0.00). Pääennusteen välit eivät ole todistetusti liian leveitä; tulos sopii lähtöpäivän sisällä korreloituneisiin virheisiin (hypoteesi), mutta aineisto ei erota sitä tulkinnasta "liian leveä lyhyillä lähtöpäivillä, liian kapea murroksen jälkeen". Keskiarvo 0.84 (7 × 1.00, 0.57, 0.00) ei osoita kalibraatiota. **Vertailu:** trendiosa kapea (0.60), leveys tulee kohinasta (välistä johdettu σ ≈ 0.34 olettaen normaalisuus ja riippumattomuus, vs. toteutunut RMSE 0.18 lähtöpäivillä 3–21 kk). **Pääsyy:** GPT-4 (2023-03, jäännös +0.97, sovitusjakson ensimmäinen rintamapiste) on vipupiste, jota seurasi tasanne vuoden 2024 puoliväliin (gpt-4o, claude-3-5-sonnet ≈ −0.5). Lähtöpäivillä 0–15 kk jäännöshajonta 0.31–0.38 (top-3-kulmakertoimella 0.34–0.45), ilman GPT-4:ää 0.19–0.23 ≈ toteutunut RMSE. Sama piste painaa pääennusteen kulmakerrointa vanhoilla lähtöpäivillä (21 kk: 1.13 vs 1.75 ilman; 24 kk: 0.79 vs 1.27), joten ohilyönti 24–27 kk:lla ei ole riippumatonta näyttöä trendin satunnaisesta epävarmuudesta. Päätös 2026-11-24: vertailuennusteelle samat sovitusikkunat (A28) kuin pääennusteelle. Vaikutus pieni: väli kapeni 3–21 kk:lla ~14 % ja 24–27 kk:lla ~5 %, `compare_top_k.py` leveys 1.30 → 1.17, mutta itseisvirhe 0.14 → 0.16 (k = 1 0.33, k = 5 0.17), kattavuus 1.00, ja väli on yhä ~2 × virheet: ¼–⅓ drawista sovitetaan koko jaksoon (GPT-4 mukana), ja vanhimmilla lähtöpäivillä mahtuu vain 18 kk:n ikkuna (27 kk:lla sekin sisältää GPT-4:n). Vauhtivälin kattavuus synteettisesti (100 toistoa, neljännesvaikutus 0.3): ilman ikkunoita 0.72, ikkunoilla 0.89. Raportti 2026-11-24: vauhti 1.90 [1.81–2.04], 12 kk P50 7.78 [7.37–8.20] vs pääennuste 7.69 [7.23–8.17]. Kohinamallia ei muutettu (ylisovitusriski: samat vähät lähtöpäivät). Rajoitus: θ:t nykyisestä IRT-ajosta (sovitettu myös lähtöpäivän jälkeisellä datalla) → toteutuneet virheet todennäköisesti hieman optimistisia. Drawin hyväksyntä vertailussa löysempi (≥ 2 rintamapistettä) kuin pääennusteessa (min_points); nykydatalla ikkunadrawien osuudet nimellisiä | K5+ |
| A34 | Menetelmää on viritetty samalla vähällä datalla (A24–A33: ~23 rintamapistettä, 2–3 riippumatonta lähtöpäivää); tallennettujen ennusteiden toteumaseuranta (A30) sekoittaisi eri menetelmien ennusteet | Ulkoinen arvio 2026-11-24 (jatkokehitys 6.1): haarautuvien polkujen ongelma, tehokas otoskoko ei riitä erottamaan, mikä korjaus auttaa | Menetelmä jäädytetään versioksi 1. `METHOD_VERSION` tallennetaan kyvykkyys- ja kustannusennusteeseen ja näytetään raportissa; toteumaseuranta raportoi tulokset versioittain (`by_method_version`; aiemmat ennusteet = versio 0). Sormenjälkitesti `tests/test_method_version.py` (sormenjäljet versioittain, vanhoja ei muokata) kaatuu, jos tulos muuttuu samalla synteettisellä datalla tai YAMLin menetelmäkenttä tai koodin oletus muuttuu; tapaukset kattavat saranan, ikkunat, pienen otoksen korjauksen, harhakorjauksen sekä kustannuksen lattiatilan, palaisen mallin ja lähitason lattian (math-reviewerin mutaatiokokeiden jälkeen). Viikkokarsinnan avaimessa on versio. Satunnaislukujen kulutusjärjestyksen muutos on menetelmämuutos; riippuvuuspäivityksen aiheuttama muutos ei ole. Vertailuennuste (A32) ei kuulu versioon. Muutosprosessi CLAUDE.md:ssä: peruste kirjataan ennen arviointia oikealla datalla, synteettinen validointi, math-reviewer, versionnosto ja rivi alla olevaan taulukkoon | K5+ |
| A11 | Pareto-kaava 5.1 vertaa vain *aiemmin* julkaistuihin malleihin | Kaksi samana päivänä julkaistua mallia päätyisivät molemmat rintamaan, vaikka toinen on heikompi | Saman päivän julkaisuista rintamaan pääsee vain päivän paras. Tasapeli aiemman maksimin kanssa ei riitä (tiukka >) | K1 |
| A12 | Määrittely regularisoi vain θ:aa ja b:tä | Ilman ankkureita määrittelyn häviöllä θ,b-prior painaa skaalan nollaan ja a karkaa (sd θ̂ 0.02, a ≈ 53, ei konvergoi). Saturoituneen testin karkaamisen estää b-prior, ei tämä | Lisätty heikko prior λ_a · mean(ln a)², λ_a = 0.01. Ajo ilman ankkureita varoittaa aina, koska skaala riippuu silloin λ:sta, N:stä ja M:stä | K2 |
| A13 | Määrittely ehdottaa keskivirheeksi pelkkää Hessian-käänteismatriisia | Jatkuvan BCE:n Hessian olettaa Bernoulli-varianssin, joten SE on moninkertainen (kattavuus 1.0). Quasi-φ (0.71) ja korjaamaton sandwich (0.65) taas aliarvioivat, koska mallikohtaisia havaintoja on vähän (keskimääräinen vipuarvo 0.22) | **HC3-sandwich** H⁻¹(Σ g gᵀ/(1−h)²)H⁻¹. Monte Carlo -kattavuus erillisillä siemenillä: fixed 0.78, post_hoc 0.83, vaihteleva havaintomäärä 0.80 (testi `pytest -m slow`). post_hoc-tilan SE lasketaan delta-menetelmällä täydestä kovarianssista. Hessianin definiittisyys ja kunto tarkistetaan. **Tunnettu rajoitus:** yli 4 000 parametrin ongelmissa käytetään diagonaaliapproksimaatiota, joka aliarvioi SE:n noin 40 %. Korjaus (θ-lohkon Schurin komplementti) on backlogissa; nykydata jää selvästi kynnyksen alle | K2 |
| A10 | Epoch julkaisee jo oman IRT-pohjaisen kyvykkyysindeksin (ECI), jossa on luottamusvälit 256 mallille | Asteikon rakentaminen tuplaa olemassa olevaa työtä, mutta antaa samalla valmiin vertailukohdan | ECI:tä käytetään K2/K4:n ulkoisena validointina (tavoite: Spearman > 0.9). Projektin lisäarvo on kustannusennusteissa, osa-aluekohtaisessa θ:ssa, omissa painoissa ja omissa testeissä | K4 |

### Menetelmäversiot (6.1, A34)

Kyvykkyys- ja kustannusennusteen menetelmän versiot. Uusi rivi kirjataan **ennen** kuin muutosta arvioidaan oikealla datalla (tila "suunniteltu"), ja se täydennetään, kun muutos otetaan käyttöön. Toteumaseuranta (A30) raportoi tulokset versioittain.

| versio | käytössä alkaen | tila | sisältö / muutos | peruste ja validointi |
|---|---|---|---|---|
| 0 | 2026-11-19 | vanha | Tallennetut ennusteet ennen versiointia. Menetelmä muuttui niiden välillä (A27–A33). | – |
| 1 | 2026-11-24 | käytössä | Jäädytetty menetelmä: Pareto-rintama 2023-01-01 alkaen, palainen malli per draw (A20), A25, pienen otoksen korjaus auto (A27), sovitusikkunat 18/24/36 kk (A28), otsikkoluku bootstrap-mediaani (A29), rintamavalinnan harhakorjaus vaihtoehtona (A31), θ-SE > 1 pois (A18); kustannus A16/A17/A26. | Kirjattu A24–A33; sormenjälki `tests/test_method_version.py`. |

---

## K0 – Projektin perusta

**Tavoite:** Tyhjä mutta toimiva projektirunko, johon kaikki muu lisätään.

**Tehtävät**
- Alustetaan git-repo ja kirjoitetaan `.gitignore` (data/, *.duckdb, .venv).
- Kirjoitetaan `pyproject.toml`, jossa riippuvuudet: duckdb, polars, pydantic, httpx, torch (CPU), scipy, statsmodels, numpy, pyyaml, streamlit, plotly. Dev-riippuvuuksiksi pytest ja ruff.
- Luodaan hakemistorakenne määrittelyn luvun 6 mukaan.
- Tehdään `src/config.py`, joka lataa YAML-tiedostot Pydantic-malleiksi ja validoi ne käynnistyksessä.
- Lisätään CLI-runko (esim. `typer`): `forecaster init-db | ingest | fit | forecast | dashboard`.

**Hyväksymiskriteerit**
- `pip install -e .` ja `pytest` menevät läpi puhtaassa ympäristössä.
- `forecaster --help` listaa komennot.

---

## K1 – Tietokantakerros

**Tavoite:** Skeema ja repository-kerros, jonka kautta kaikki muut moduulit lukevat ja kirjoittavat dataa.

**Tehtävät**
- `src/db/schema.sql`: määrittelyn viisi taulua sekä lisäykset `model_prices` (A1), `model_aliases` (A2), `ingest_runs` (lokitus: ajo, lähde, rivimäärä, virheet) ja `forecast_runs`.
- `src/db/repository.py`:
  - `init_db()` on idempotentti, ja skeemaversio tallennetaan `schema_version`-tauluun.
  - Upsert-funktiot jokaiselle taululle. `eval_id` muodostetaan deterministisenä hashina avaimesta `(model_id, benchmark_id, source_name)`.
  - Lukufunktio `get_observation_matrix(domain=None, exclude_outliers=True)` palauttaa Polars-DataFramen.
  - Pareto-rintaman SQL-kysely ikkunafunktiolla, jossa verrataan mallia aiempien mallien kumulatiiviseen maksimiin.
  - Lukufunktio `get_price_at(model_id, date)`.
- Pydantic-mallit jokaiselle taululle (`src/db/models.py`), joita myös ingest käyttää.

**Hyväksymiskriteerit**
- Yksikkötestit muistinvaraisella DuckDB:llä: skeeman luonti, upsertin idempotenssi, viiteavainten rikkominen kaatuu odotetusti ja Pareto-kysely palauttaa oikeat mallit käsin tehdyllä aineistolla.

---

## K2 – IRT-laskentamoottori (synteettinen validointi)

**Tavoite:** Projektin matemaattinen ydin, joka on todistetusti oikein ennen kuin se näkee oikeaa dataa.

**Tehtävät**
- `src/models/synthetic.py`: generaattori, joka tuottaa tunnetuilla parametreilla ($\theta$, $a$, $b$, $c$) harvan havaintomatriisin. Mukana on säädettävä puuttuvuusaste, kohina, saturoituneet benchmarkit ja aikariippuvuus (uudet mallit testataan vain uusilla testeillä).
- `src/models/irt_engine.py`:
  - PyTorch-moduuli parametreille $\theta$, $b$ ja $\tilde{a}$, jossa $a$ saadaan softplus-funktiolla ja $c$ on kiinteä.
  - Häviönä painotettu jatkuva BCE ja L2-regularisointi (λ konfiguroitavissa).
  - Optimointi L-BFGS:llä (Adam varavaihtoehtona) ja konvergenssikriteerinä gradientin normi.
  - Ankkurointi: molemmat tavat (kiinteät $\theta$ tai jälkikäteinen lineaarimuunnos) valittavissa konfiguraatiosta.
  - Keskivirhe: Hessianin (`torch.autograd.functional.hessian`) käänteismatriisin diagonaali. Isoissa ajoissa käytetään lohkodiagonaalista approksimaatiota.
  - Tulos on dataclass `IRTResult(theta, theta_se, a, b, loss, converged, run_id)`.
- Domain-kohtaiset ajot (A6): sama moottori ajetaan suodatetulle matriisille.
- Tulosten tallennus tauluihin `capability_scores` ja `benchmark_irt_params`.

**Hyväksymiskriteerit** (`tests/test_irt_convergence.py`)
- Synteettisellä datalla (≥ 50 mallia, ≥ 15 benchmarkia, 60 % puuttuvuus) korrelaatio $\theta$:n todellisten ja estimoitujen arvojen välillä on > 0.95 ja $b$:n RMSE < 0.3.
- Saturoituneen benchmarkin $a$ ja $b$ eivät karkaa äärettömiin.
- Ankkurimallit osuvat täsmälleen määritettyihin arvoihin.
- Noin 80 % todellisista arvoista osuu 80 %:n luottamusvälin sisään (SE:n kalibrointi).
- Saman siemenen ajot ovat deterministisiä.

---

## K3 – Datankeruuputket

**Tavoite:** Oikea data tietokantaan toistettavasti ja jäljitettävästi.

**Tehtävät**
- Yhteinen ingest-runko (`src/ingest/base.py`), joka hoitaa:
  1. haun (HTTPX, uudelleenyritykset, välimuisti),
  2. raakadatan tallennuksen `data/raw/<lähde>/<päivämäärä>.parquet`,
  3. Pydantic-validoinnin,
  4. nimien yhdistämisen aliastaulun kautta (A2), jossa tuntemattomat nimet kirjataan raporttiin eikä niitä hylätä hiljaa,
  5. normalisoinnin kaavalla 4.1 sekä Elo-muunnoksen (A7),
  6. upsertin ja `ingest_runs`-lokin.
- Lähdekohtaiset adapterit tässä järjestyksessä:
  1. `epoch_ai.py`: laajin historiallinen pohja, mukana julkaisupäivät ja compute-arviot,
  2. `openrouter.py`: hinnat `model_prices`-tauluun päivittäisinä havaintoina,
  3. `livebench.py`: tuoreet ja kontaminaatioriskiltään pienet testit,
  4. `internal.py`: CSV/JSON-tuonti omille testeille.
- `config/benchmarks.yaml` täytetään: domain, metric_type, raw_min/max, guessing_floor, contamination_risk ja weight.
- Karanteeni (A3): rivit, joilta puuttuu pakollinen tieto, menevät tauluun `ingest_quarantine`.

**Hyväksymiskriteerit**
- Jokaiselle adapterille on testi tallennetulla raakatiedostolla (ei verkkoyhteyttä testeissä).
- Kahdesti ajettu ingest ei tuota duplikaatteja.
- Ingest-raportissa näkyvät uudet mallit, tuntemattomat aliakset ja karanteeniin menneet rivit.

---

## K4 – Ankkurointi ja laadunvalvonta oikealla datalla

**Tavoite:** IRT ajetaan ensimmäistä kertaa oikealla datalla, ja tulosten uskottavuus varmistetaan.

**Tehtävät**
- `config/anchors.yaml`: valitaan 2–3 laajasti testattua ankkurimallia.
- Siltamallianalyysi: raportti, joka näyttää jokaiselle benchmark-parille yhteisten mallien määrän. Jos vanhan ja uuden testiryhmän välillä on alle 5 siltamallia, se merkitään varoitukseksi.
- Kytkentäanalyysi: graafi, jossa mallit ja benchmarkit ovat solmuja. Irralliset komponentit tunnistetaan, koska niitä ei voi asettaa samalle asteikolle.
- Poikkeamien tunnistus (A4): standardoitu residuaali ja benchmark-kohtainen raja 3 × MAD. Poikkeamat liputetaan, ja IRT ajetaan kerran uudelleen ilman niitä.
- Diagnostiikkaraportti (`forecaster fit --report`): konvergenssi, residuaalijakaumat, benchmarkien $a$- ja $b$-arvot, liputetut havainnot ja $\theta$-järjestyksen järkevyystarkistus (esim. tunnetusti vahvempi malli yli heikomman).
- Stabiilisuustesti: kun yksi benchmark jätetään vuorollaan pois, kuinka paljon $\theta$-arvot liikkuvat.

**Hyväksymiskriteerit**
- Mallien järjestys on asiantuntijan silmissä uskottava.
- Kun uusi data lisätään, ankkurimallien ulkopuolisten mallien $\theta$ muuttuu ajojen välillä vähemmän kuin niiden keskivirhe.

---

## K5 – Ennustemoottori

**Tavoite:** Kaksi ennustetta luottamusväleineen 6–24 kuukauden päähän.

**Tehtävät** (`src/models/forecaster.py`)
- **Kyvykkyysennuste (5.1)**
  - Pareto-rintama (SQL-kysely K1:stä).
  - Lineaarinen trendi $\theta(t)$ ja valinnaisena palaittainen malli, jonka katkoskohta testataan Chow-testillä tai valitaan AIC:n perusteella.
  - Bootstrap, B = 1000, jossa $\theta$-epävarmuus on mukana (A5). Tuloksena persentiilit P10, P50 ja P90.
- **Kustannusennuste (5.2)**
  - Valitaan $\theta_{target}$ konfiguraatiosta tai ankkurimallin tasosta.
  - Poimitaan kuukausittain halvin malli, jolla $\theta \ge \theta_{target}$, käyttäen `model_prices`-taulun historiaa (A1).
  - Log-lineaarinen regressio, puoliintumisaika $T_{1/2}$ ja sille bootstrap-luottamusväli.
- Backtest: ennuste sovitetaan datalla, joka päättyy 12 kuukautta sitten, ja sitä verrataan toteutuneeseen. Tämä on ainoa rehellinen mittari ennusteen laadulle.
- Tulokset tallennetaan `forecast_runs`-tauluun ja yhdistetään IRT-ajoon `run_id`:n kautta.

**Hyväksymiskriteerit**
- Synteettisellä lineaarisella trendillä estimoitu kulmakerroin osuu luottamusväliin.
- Backtest-raportti on olemassa, ja sen virhe on dokumentoitu.
- Jos $\alpha_1 \le 0$ (hinnat eivät laske), $T_{1/2}$ ilmoitetaan määrittelemättömänä eikä negatiivisena lukuna.

---

## K6 – Interaktiivinen dashboard

**Tavoite:** Streamlit-näkymä, jossa tuloksia voi tutkia ja oletuksia säätää.

**Sivut** (`app/dashboard.py` ja `app/pages/`)
1. **Yleiskuva:** $\theta$ ajan funktiona hajontakuvana, jossa Pareto-rintama ja ennustekaista (P10–P90) on korostettu. Domain-valinta.
2. **Mallit:** taulukko mallien $\theta$-arvoista ja keskivirheistä sekä mallikohtainen näkymä, jossa havaitut ja ennustetut pisteet näkyvät benchmarkeittain.
3. **Benchmarkit:** $a$- ja $b$-parametrit, saturaatioaste, kattavuus ja liputetut poikkeamat.
4. **Kustannukset:** iso-kyvykkyyskäyrä, liukusäädin $\theta_{target}$:lle ja $T_{1/2}$ luottamusväleineen.
5. **Simulaattori:** benchmarkien painojen ja kontaminaatiosuodatuksen säätö sekä IRT:n uudelleenajo. IRT-tulokset välimuistitetaan `st.cache_data`:lla ja painojen hashilla, jotta näkymä pysyy nopeana.
6. **Datan laatu:** ingest-ajojen loki, karanteeni ja tuntemattomat aliakset.

**Hyväksymiskriteerit**
- Kaikki sivut latautuvat alle 3 sekunnissa välimuistista.
- Painojen muuttaminen päivittää tulokset alle 30 sekunnissa.

---

## K7 – Automaatio ja ylläpito

**Tavoite:** Järjestelmä päivittyy itsestään ja kertoo, jos jokin hajoaa.

**Tehtävät**
- GitHub Actions -työnkulku (viikoittain ja käsin käynnistettävissä): ingest → fit → forecast. Tietokanta tai Parquet-tilannekuva tallennetaan artefaktina tai julkaistaan.
- CI: ruff ja pytest jokaisessa pushissa.
- Hälytykset: ingest kaatuu, uusia tuntemattomia aliaksia löytyy tai IRT ei konvergoi.
- Dokumentaatio: README (asennus, käyttö), metodologiasivu (matematiikka ja rajoitukset) ja ohje uuden datalähteen lisäämiseen.
- Valinnaisesti: FastAPI-rajapinta (`/models`, `/scores`, `/forecast`), jos tarvitaan integraatiota.

**Hyväksymiskriteerit**
- Viikoittainen ajo menee läpi ilman käsityötä, kun uusia aliaksia ei ole tullut.

---

## Yhteenveto ja arvioitu työmäärä

| Kokonaisuus | Lopputulos | Riippuu | Arvio |
|---|---|---|---|
| K0 Perusta | Projektirunko, CLI, konfiguraatio | – | 0,5 pv |
| K1 Tietokanta | Skeema ja repository, hinta- ja aliastaulut | K0 | 1–2 pv |
| K2 IRT-moottori | Validoitu 2PL-IRT, keskivirhe ja ankkurointi | K1 | 3–5 pv |
| K3 Datankeruu | 4 adapteria, aliakset ja karanteeni | K1 | 3–5 pv |
| K4 Ankkurointi & QA | Poikkeamasuodatus, siltamalli- ja stabiilisuusraportit | K2, K3 | 2–3 pv |
| K5 Ennusteet | Kyvykkyys- ja kustannusennuste sekä backtest | K4 | 2–3 pv |
| K6 Dashboard | 6-sivuinen Streamlit-sovellus | K5 | 3–4 pv |
| K7 Automaatio | CI, ajastus, hälytykset ja dokumentaatio | K6 | 1–2 pv |

**Ensimmäinen käyttökelpoinen versio (MVP)** syntyy K0–K4-kokonaisuuksista, kun mukana on vain Epoch AI -data: silloin saadaan uskottava kyvykkyysasteikko. K5–K7 muuttavat sen ennustetyökaluksi.

## Suurimmat riskit

1. **Siltamallien puute:** jos uusia testejä (FrontierMath, SWE-bench) ei ole ajettu riittävästi vanhoille malleille, asteikkoja ei voi yhdistää luotettavasti. Tämä tarkistetaan K4:n kytkentäanalyysillä mahdollisimman aikaisin, ja K3:n jälkeen tehdään nopea esianalyysi.
2. **Lähteiden saatavuus ja lisenssit:** Epoch AI:n ja LiveBenchin datan muoto ja käyttöehdot tarkistetaan ennen K3:a.
3. **Hintahistoria (päätetty 2026-11-13):** Ensisijainen lähde on [simonw/llm-prices](https://github.com/simonw/llm-prices) (`historical-v1.json`). Siinä on julkaisuhinnat ja päivätyt hinnanmuutokset, ja se vastaa 91:tä sovitettua mallia. Loput mallit täydennetään OpenRouterin nykyhinnalla, jonka oletetaan olleen voimassa julkaisupäivästä alkaen. **Lisenssi puuttuu (LICENSE 404) → data vain omaan käyttöön.** Julkaisu, mukaan lukien julkinen dashboard, vaatii luvan tai lähteen vaihdon. Selvityksessä hylättiin [antriksht/llm-api-pricing](https://github.com/antriksht/llm-api-pricing), koska sen historia alkaa vasta lokakuusta 2026. Adapteri toteutetaan K5:n alussa.
4. **OpenRouterin päivämäärättömät tunnisteet:** `mistralai/mistral-large` voi tarkoittaa uusinta versiota, vaikka automaattinen vastaavuus osuu Epochin samannimiseen vanhempaan malliin. Tarkistetaan ennen kustannusanalyysiä, ja tarvittaessa ohitetaan käsin `model_aliases.yaml`:ssa.
5. **Ennusteen ylitulkinta:** rintamassa on vähän pisteitä (kymmeniä), joten luottamusvälit ovat leveitä. Tämä kerrotaan dashboardissa näkyvästi.
