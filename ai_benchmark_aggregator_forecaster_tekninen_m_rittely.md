# Tekninen määrittely: AI Benchmark Aggregator & Forecaster

## 1. Järjestelmän yleiskuvaus ja tavoitteet

Järjestelmän tarkoituksena on koota hajanaiset tekoälymallien suorituskyky- ja kustannustiedot yhteen tietokantaan, yhdistää eri vaikeustason ja eri aikoina julkaistut testit (benchmarkit) yhdelle jatkuvalle kyvykkyysasteikolle ($\theta$) ja tuottaa tilastollisesti perusteltuja tulevaisuuden ennusteita sekä kyvykkyyden kasvulle että API-kustannusten laskulle.

### Ratkaistavat ydinongelmat
1. **Harva havaintomatriisi (Sparsity):** Kaikkia malleja ei ole testattu kaikilla benchmarkeilla.
2. **Testien saturaatio:** Vanhat testit (esim. MMLU, GSM8K) saavuttavat yli 90 % tarkkuuden, jolloin ne menettävät erottelukykynsä.
3. **Asteikon ankkurointi:** Uudet, vaikeammat testit (esim. FrontierMath, SWE-bench) on liitettävä samaan vertailuasteikkoon vanhojen testien kanssa.
4. **Kohinan suodatus ennusteista:** Markkinoille tulevat pienet, edulliset mallit vetävät keskiarvoja alas; kehitystrendit on ennustettava Pareto-eturintamasta (*Frontier*).

---

## 2. Suositeltu teknologiapino (Tech Stack)

Koska benchmark-data on rakenteellista taulukkodataa (kymmeniä tuhansia rivejä, ei teratavuja), raskas hajautettu arkkitehtuuri on tarpeeton. Viisain toteutus perustuu moderniin, paikallisesti tai kevyessä pilvikontissa ajettavaan analyyttiseen pinoon:

| Kerros | Suositeltu teknologia | Perustelu |
| :--- | :--- | :--- |
| **Ohjelmointikieli** | **Python 3.11+** | Ainoa järkevä valinta data-analyysin, PyTorchin ja tilastollisen laskennan ekosysteemin vuoksi. |
| **Tietokanta** | **DuckDB** (+ Parquet) | Sulautettu kolumnipohjainen OLAP-tietokanta. Ei vaadi erillistä palvelinta, tukee suoraan SQL-ikkunafunktioita (Pareto-rintaman haku) ja lukee/kirjoittaa Parquet-tiedostoja natiivisti. |
| **Tiedonkeruu (Ingest)** | **HTTPX + Pydantic + Polars** | Asynkroninen datan haku, tiukka skeemavalidointi (Pydantic) ennen kantaan tallennusta ja nopea datan muokkaus (Polars). |
| **IRT-laskentamoottori** | **PyTorch** (CPU-versio) | Mahdollistaa oman jatkuvan 2PL-IRT-häviöfunktion, parametrien rajoittamisen (`softplus`), referenssimallien kiinnittämisen ja Hessian-pohjaisen keskivirheen laskennan ilman GPU-riippuvuutta. |
| **Ennustemallinnus** | **SciPy + Statsmodels / NumPy** | Kevyt ja luotettava toteutus Bootstrap-otannalle, palaittaiselle lineaariselle regressiolle (*piecewise linear*) ja log-lineaariselle hintamallinnukselle. |
| **Käyttöliittymä & API** | **Streamlit** (tai **FastAPI + React**) | **Streamlit + Plotly** on nopein tapa rakentaa interaktiivinen analytiikkanäkymä (suodattimet, luottamusvälit, omat painotukset). Jos järjestelmä integroidaan muihin palveluihin, rajapinnaksi **FastAPI**. |
| **Ajastus (Orchestration)**| **GitHub Actions** tai **Cron** | Riittää mainiosti, sillä benchmark-data päivittyy päivittäin tai viikoittain, ei reaaliajassa. |

---

## 3. Data-arkkitehtuuri ja tietokantaskeema

### 3.1. Tiedonkulkukaavio (ELT-putki)

```text
[Ulkoiset lähteet]             [Landing / Raw]        [Core DB: DuckDB]           [Analytics & UI]
Epoch AI (CSV/GitHub) ────┐
LiveBench / SWE-bench ────┼──► raakadata.parquet ───► models ───────────────────► Continuous 2PL-IRT
OpenRouter API (Hinnat) ──┤                           benchmarks                  Pareto Frontier -suodatus
Omat sisäiset testit ─────┘                           evaluations                 Bootstrap-ennusteet
                                                      capability_scores ◄─────────┘
```

### 3.2. DuckDB-tietokantaskeema

```sql
-- 1. Tekoälymallien perustiedot ja hinnoittelu
CREATE TABLE models (
    model_id VARCHAR PRIMARY KEY,          -- Esim. 'anthropic/claude-3-5-sonnet-20241022'
    display_name VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    release_date DATE NOT NULL,            -- Kriittinen aikasarjaennusteille
    is_open_weights BOOLEAN DEFAULT FALSE,
    estimated_compute_flops DOUBLE,        -- Valinnainen (Epoch AI -datasta)
    input_cost_per_1m DOUBLE,              -- USD per 1M input-tokenia
    output_cost_per_1m DOUBLE,             -- USD per 1M output-tokenia
    blended_cost_per_1m DOUBLE,            -- Esim. 3:1 input/output -painotettu hinta
    is_anchor_model BOOLEAN DEFAULT FALSE  -- Kiinnitetäänkö θ-asteikon ankkuriksi
);

-- 2. Benchmark-testien metatiedot
CREATE TABLE benchmarks (
    benchmark_id VARCHAR PRIMARY KEY,      -- Esim. 'swe_bench_verified'
    name VARCHAR NOT NULL,
    domain VARCHAR NOT NULL,               -- 'coding', 'math', 'reasoning', 'agentic', 'general'
    metric_type VARCHAR NOT NULL,          -- 'accuracy', 'pass_at_1', 'elo'
    raw_min DOUBLE DEFAULT 0.0,            -- Teoreettinen minimi
    raw_max DOUBLE DEFAULT 100.0,          -- Teoreettinen maksimi
    guessing_floor DOUBLE DEFAULT 0.0,     -- Arvausraja c_j (esim. 0.25 nelivalinnalle)
    contamination_risk VARCHAR,            -- 'low', 'medium', 'high' (painotusta varten)
    weight DOUBLE DEFAULT 1.0              -- Asiantuntijapaino indeksissä
);

-- 3. Yksittäiset testitulokset (Harva matriisi)
CREATE TABLE evaluations (
    eval_id VARCHAR PRIMARY KEY,
    model_id VARCHAR REFERENCES models(model_id),
    benchmark_id VARCHAR REFERENCES benchmarks(benchmark_id),
    raw_score DOUBLE NOT NULL,
    normalized_score DOUBLE NOT NULL,      -- Skaalattu välille [0.001, 0.999]
    eval_date DATE,
    source_name VARCHAR,                   -- Esim. 'epoch_ai', 'livebench', 'internal'
    source_url VARCHAR,
    is_outlier BOOLEAN DEFAULT FALSE,      -- Liputetaan jos residuaali > 3σ (kontaminaatioepäily)
    UNIQUE(model_id, benchmark_id, source_name)
);

-- 4. Lasketut kyvykkyysindeksit (IRT-ajon lopputulos)
CREATE TABLE capability_scores (
    run_id VARCHAR NOT NULL,
    calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    model_id VARCHAR REFERENCES models(model_id),
    domain VARCHAR NOT NULL,               -- 'overall' tai yksittäinen osa-alue
    theta DOUBLE NOT NULL,                 -- Latentti kyvykkyys θ
    theta_se DOUBLE,                       -- Keskivirhe (epävarmuus)
    PRIMARY KEY (run_id, model_id, domain)
);

-- 5. Benchmarkien opitut IRT-parametrit (Diagnostiikkaa varten)
CREATE TABLE benchmark_irt_params (
    run_id VARCHAR NOT NULL,
    benchmark_id VARCHAR REFERENCES benchmarks(benchmark_id),
    difficulty_b DOUBLE NOT NULL,          -- Vaikeustaso b_j
    discrimination_a DOUBLE NOT NULL,      -- Erottelukyky a_j
    PRIMARY KEY (run_id, benchmark_id)
);
```

---

## 4. Matemaattinen määrittely

### 4.1. Raakapisteiden esikäsittely (Normalisointi)
Ennen IRT-mallinnusta jokainen raakatulos $r_{ij}$ muunnetaan avoimelle välille $(0, 1)$ risti-entropian numeerisen vakauden takaamiseksi ($\epsilon = 10^{-3}$):

$$y_{ij} = \text{clip}\left(\frac{r_{ij} - \text{raw\_min}_j}{\text{raw\_max}_j - \text{raw\_min}_j},\ \epsilon,\ 1 - \epsilon\right)$$

*Huom:* Elo-pohjaiset testit (esim. Chatbot Arena) muunnetaan ensin logistisella muunnoksella suhteessa perusmalliin (esim. $\text{Elo } 1000 = 0.5$) tai käsitellään erillisenä Gaussisena havaintoyhtälönä.

### 4.2. Jatkuva 2PL-IRT-malli (Continuous Item Response Theory)
Mallin $i$ odotettu normalisoitu pistemäärä $\hat{y}_{ij}$ benchmarkissa $j$ mallinnetaan yhtälöllä:

$$\hat{y}_{ij} = c_j + (1 - c_j) \cdot \sigma\Big(a_j (\theta_i - b_j)\Big)$$

missä:
* $\theta_i \in \mathbb{R}$: Mallin $i$ latentti kyvykkyys.
* $b_j \in \mathbb{R}$: Benchmarkin $j$ vaikeustaso.
* $a_j = \ln(1 + e^{\tilde{a}_j}) > 0$: Benchmarkin $j$ erottelukyky (*softplus*-aktivaatio takaa positiivisuuden).
* $c_j \in [0, 1)$: Kiinteä arvausraja (`guessing_floor`).

#### Häviöfunktio ja regularisointi
Optimointi minimoi painotetun binäärisen risti-entropian sekä L2-regularisointitermin (vastaa Bayesilaista $\mathcal{N}(0, 1)$ -prioria, joka estää parametrien karkaamisen täydellisen erottelun tilanteissa):

$$\mathcal{L}(\theta, b, \tilde{a}) = -\sum_{(i,j) \in \Omega} w_j \Big[ y_{ij} \ln(\hat{y}_{ij}) + (1 - y_{ij}) \ln(1 - \hat{y}_{ij}) \Big] + \lambda \left( \frac{1}{N} \sum_{i=1}^N \theta_i^2 + \frac{1}{M} \sum_{j=1}^M b_j^2 \right)$$

missä $\Omega$ on havaittujen testitulosten joukko, $w_j$ on benchmarkin painokerroin ja $\lambda \approx 0.01$.

#### Asteikon ankkurointi (Scale Anchoring)
Jotta $\theta$-arvot pysyvät vertailukelpoisina ajan yli uutta dataa lisättäessä:
1. Valitaan 2–3 laajasti testattua referenssimallia (esim. `gpt-4-0314` $\rightarrow \theta = 0.0$, `claude-3-5-sonnet` $\rightarrow \theta = 1.5$).
2. Kiinnitetään näiden mallien $\theta$-arvot vakioiksi gradienttipäivityksen aikana tai skaalataan koko $\theta$-vektori lineaarisesti optimoinnin jälkeen siten, että ankkurimallit osuvat tarkasti määriteltyihin pisteisiin.

### 4.3. Kontaminaation ja poikkeamien suodatus
Optimoinnin jälkeen lasketaan kullekin havainnolle standardoitu residuaali:

$$e_{ij} = |y_{ij} - \hat{y}_{ij}|$$

Jos havainnon virhe ylittää $3 \times \text{MAD}$ (Mediaani absoluuttinen poikkeama), se merkitään tietokantaan (`is_outlier = TRUE`) ja IRT-malli ajetaan kerran uudelleen ilman poikkeamia. Tämä estää yksittäisten vuotaneiden benchmark-tulosten vääristävän mallin kokonaiskuvaa.

---

## 5. Ennustemallinnus (Forecasting Engine)

Ennustemoduuli tuottaa kaksi erillistä ennustetta 6–24 kuukauden päähän: **Kyvykkyysrintaman kehityksen** ja **Kustannusten puolittumisajan**.

### 5.1. Kyvykkyysennuste (Pareto Frontier + Bootstrap)
1. **Eturintaman erotus:** Haetaan SQL:llä mallit, jotka ovat julkaisuhetkellään olleet kaikkien aikojen parhaita:
   $$\mathcal{F} = \left\{ i \ \middle|\ \theta_i > \max_{k:\ t_k < t_i} \theta_k \right\}$$
2. **Trendin sovitus:** Koska $\theta$ on logistisessa logit-avaruudessa, lineaarinen kasvu $\theta$:ssa kuvaa eksponentiaalista kehitystä raakapisteissä. Sovitetaan regressiomalli:
   $$\theta(t) = \beta_0 + \beta_1 t + \epsilon$$
   *(Valinnainen laajennus: Sovitetaan palaittainen lineaarinen regressio, jos viimeisen 12 kuukauden kulmakerroin $\beta_1$ poikkeaa tilastollisesti merkitsevästi historiallisesta).*
3. **Epävarmuusvälit:** Suoritetaan $B = 1000$ Bootstrap-otosta joukosta $\mathcal{F}$. Lasketaan tuleville aikapisteille $t_{\text{future}}$ jakauman 10., 50. ja 90. persentiilit.

### 5.2. Kustannusennuste (Iso-Capability Cost Trend)
1. Valitaan kiinteä kyvykkyystaso $\theta_{\text{target}}$ (esim. GPT-4-taso tai Claude 3.5 Sonnet -taso).
2. Poimitaan kunkin kuukauden halvin malli, joka täyttää ehdon $\theta_i \ge \theta_{\text{target}}$.
3. Sovitetaan log-lineaarinen malli ajan suhteen:
   $$\ln(\text{cost}(t)) = \alpha_0 - \alpha_1 t + \epsilon$$
4. Lasketaan hinnan puolittumisaika kuukausina:
   $$T_{1/2} = \frac{\ln(2)}{\alpha_1}$$

---

## 6. Modulaarinen projektirakenne

```text
ai-benchmark-forecaster/
├── pyproject.toml               # Riippuvuudet (duckdb, torch, polars, pydantic, streamlit, plotly)
├── config/
│   ├── benchmarks.yaml          # Benchmarkien metatiedot, arvausrajat c_j ja painotukset w_j
│   └── anchors.yaml             # Ankkurimallit ja niiden kiinnitetyt θ-arvot
├── data/
│   ├── raw/                     # Ladatut CSV/JSON-tiedostot päivämääräleimoilla
│   └── benchmark.duckdb         # Päätietokanta
├── src/
│   ├── ingest/
│   │   ├── epoch_ai.py          # Epoch AI -datasarjojen haku ja validointi
│   │   ├── livebench.py         # LiveBench-tulosten haku
│   │   ├── openrouter.py        # API-hintojen ja mallien julkaisupäivien päivitys
│   │   └── internal.py          # Omien sisäisten testitulosten tuonti (CSV/JSON)
│   ├── models/
│   │   ├── irt_engine.py        # PyTorch Continuous 2PL-IRT ja outlier-suodatus
│   │   └── forecaster.py        # Pareto-suodatus, Bootstrap-ennusteet ja hintatrendit
│   └── db/
│       └── repository.py        # DuckDB-yhteydet, skeeman alustus ja SQL-kyselyt
├── app/
│   └── dashboard.py             # Streamlit + Plotly -käyttöliittymä
└── tests/
    └── test_irt_convergence.py  # Yksikkötestit synteettisellä datalla
```

---

## 7. Toteutussuunnitelma (Vaiheistus)

1. **Vaihe 1: Tietokanta ja synteettinen validointi (Viikko 1)**
   * Alusta DuckDB-skeema.
   * Rakenna `irt_engine.py` ja testaa sitä synteettisellä datalla, jossa tunnetut $\theta$- ja $b_j$-arvot sekä keinotekoinen saturaatio palautetaan virheettömästi.
2. **Vaihe 2: Datankeruuputket ja ankkurointi (Viikko 2)**
   * Rakenna ingest-skriptit Epoch AI:n avoimelle datalle ja OpenRouterin hintarajapinnalle.
   * Määritä `anchors.yaml` ja varmista, että vanhojen ja uusien testien välillä on vähintään 5–10 siltamallia (*bridge models*).
3. **Vaihe 3: Ennustemoottori ja kontaminaatiosuodatus (Viikko 3)**
   * Toteuta residuaalipohjainen poikkeamien tunnistus ja Bootstrap-ennustemalli.
4. **Vaihe 4: Interaktiivinen dashboard (Viikko 4)**
   * Rakenna Streamlit-näkymä, jossa käyttäjä voi:
     * Tarkastella $\theta$-kehitystä osa-alueittain (koodaus, päättely, kokonaiskyvykkyys).
     * Säätää benchmarkien painotuksia reaaliajassa.
     * Simuloida kustannus-kyvykkyys-suhdetta tuleville kvartaaleille.