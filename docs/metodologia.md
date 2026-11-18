# Metodologia

Tämä sivu kuvaa, miten AI Benchmark Aggregator & Forecaster muuttaa benchmark-tulokset yhteiseksi kyvykkyysasteikoksi ja ennusteiksi, ja mitä tuloksista **ei** voi päätellä. Kaavojen yksityiskohdat ovat [teknisessä määrittelyssä](../ai_benchmark_aggregator_forecaster_tekninen_m_rittely.md). Määrittelystä poikkeavat ratkaisut (aukot A1–A27) ja niiden perustelut löytyvät [toteutussuunnitelmasta](../TOTEUTUSSUUNNITELMA.md).

## 1. Data

- **Benchmark-tulokset** tulevat Epoch AI:n avoimesta aineistosta (CC BY 4.0). Mukana on 61 testiä viidellä osa-alueella: general, math, coding, reasoning ja agentic. Yksikkö on *malliryhmä*, esimerkiksi "Claude 3.5 Sonnet (October 2024)". Versiotunnisteet käsitellään aliaksina.
- **Usea tulos samasta mallista:** samalle mallille ja testille otetaan lähteen sisällä versioiden paras tulos. Jos lähteitä on useita, niiden tulokset keskiarvoistetaan.
- **Hinnat** tulevat hintahistoriasta `model_prices`. Jokaisella hinnalla on voimassaolon alkupäivä. Lähteet ovat simonw/llm-prices, käsin tarkistetut julkaisuhinnat ja OpenRouterin nykyhinnat. Jos mallille ei löydy historiaa, oletetaan OpenRouterin nykyhinnan olleen voimassa julkaisusta lähtien. Tämä voi aliarvioida vanhoja hintoja.
- **Hinnan yhdistäminen:** blended-hinta lasketaan kaavalla (3 × input + output) / 4 USD miljoonaa tokenia kohden.
- **Listahinta, ei toteutunut hinta (päätös 2026-11-16):** hinnat ovat halvimpia julkaistuja listahintoja. Se on johdonmukainen ja automaattisesti kerättävä määritelmä ("halvin saatavilla oleva hinta"). OpenRouterissa pyyntö voi kuitenkin ohjautua kalliimmalle palveluntarjoajalle, joten toteutunut keskihinta voi olla listahintaan nähden moninkertainen. Kustannusennusteet kuvaavat siis parasta mahdollista hintaa, eivät tyypillistä.

## 2. Kyvykkyysasteikko (jatkuva 2PL-IRT)

Jokainen tulos skaalataan välille [0, 1] ja leikataan välille [0.001, 0.999]. Mallille *i* ja testille *j* odotettu tulos on

  ŷ_ij = c_j + (1 − c_j) · σ(a_j (θ_i − b_j))

- **θ_i** on mallin kyvykkyys, **b_j** testin vaikeus ja **a_j = softplus(ã_j)** testin erottelukyky.
- **c_j** on arvausraja. Se ei ole opittava parametri, vaan se luetaan testin metatiedoista (satunnaisarvauksen tulos jaettuna katolla).
- **Sovitus:** parametrit sovitetaan painotetulla binäärisellä ristientropialla, ja häviöön lisätään L2-priorit θ:lle ja b:lle sekä prior ln a:lle. Optimointi tehdään PyTorchilla L-BFGS-menetelmällä.
- **Ankkurointi:** asteikko kiinnitetään kahteen malliin, GPT-4 (kesäkuu 2023) = 0 ja GPT-5 = 3.5. Ankkurit määräävät asteikon nollakohdan ja yksikön, joten eri ajojen θ-arvot ovat samalla asteikolla. Muiden mallien θ voi silti muuttua ajojen välillä, kun data muuttuu.
- **Keskivirhe:** θ:n keskivirhe on HC3-sandwich-estimaatti. Synteettisessä Monte Carlo -validoinnissa 80 %:n välin kattavuus oli 0.82.
- **Osa-alueet:** osa-alueiden θ estimoidaan kokonaisajon kiinteillä testiparametreilla, jolloin osa-alueiden θ on suoraan samalla asteikolla.

**Laadunvalvonta**
- **Karsinta:** mallilla on oltava vähintään 3 tulosta ja testillä vähintään 5.
- **Poikkeamat:** jokaisesta tuloksesta testeissä, joilla on vähintään 12 tulosta, lasketaan standardoitu residuaali ja siitä benchmark-kohtainen robusti z (MAD). Jos |z| > 3, tulos liputetaan. Sovituksesta poistetaan vain epäilyttävän *hyvät* tulokset, jotka voivat viitata kontaminaatioon, ja niitäkin enintään yksi mallia kohden. Huonoa tulosta ei poisteta, koska se voi olla aito heikkous.
- **Tarkistukset:** asteikon vakaus tarkistetaan jättämällä yksi testi kerrallaan pois. Lisäksi asteikkoa verrataan Epochin omaan ECI-indeksiin, ja Spearmanin korrelaation tavoite on > 0.9.

## 3. Kyvykkyysennuste

1. **Pareto-rintama:** rintaman muodostavat mallit, joiden θ ylittää kaikkien aiemmin julkaistujen mallien θ:n. Samana päivänä julkaistuista vain paras pääsee rintamaan. Sovitus alkaa 2023-01-01.
2. **Trendi:** rintaman θ ajan funktiona sovitetaan suoraan. Vaihtoehtona on palainen malli, jonka sarana on 12 kk ennen ennustepäivää. Palainen malli valitaan, kun saranan kertoimen p < 0.05.
3. **Bootstrap:** jokaisella kierroksella θ arvotaan jakaumasta N(θ̂, SE²), ja arvottu θ ratkaisee, mitkä mallit kuuluvat rintamaan. Rintamapisteet otetaan palauttaen, trendi sovitetaan mallien *alkuperäisiin* θ̂-arvoihin ja malli (lineaarinen vai palainen) valitaan uudelleen. Mittausvirhettä ei syötetä sovitukseen toiseen kertaan, koska se näkyy jo jäännöksissä. Ennusteväli sisältää näin epävarmuuden rintaman kokoonpanosta, trendistä ja siitä, onko kehitys lineaarista vai kiihtyvää.
4. **Raportoitavat välit:**
   - **trend**-väli kuvaa trendisuoran epävarmuutta.
   - **prediction**-väli kuvaa yksittäisen tulevan rintamamallin vaihtelua.
5. **Backtest:** malli sovitetaan vain lähtöpäivään mennessä julkaistulla datalla, ja ennustetta verrataan jokaiseen sen jälkeen toteutuneeseen rintamapisteeseen. Lähtöpäiviä on neljä (6, 12, 18 ja 24 kk sitten), koska yhden lähtöpäivän virheet ovat samanmerkkisiä ja muodostavat käytännössä vain yhden havainnon.
6. **Epävarmat mallit pois:** ennusteesta jätetään pois mallit, joiden θ-SE > 1. Ajossa 2026-11-16 tällaisia malleja oli 21. Lähes kaikilla niistä oli vain 3–5 tulosta, niiden testien erottelukyky oli keskimääräistä matalampi, ja ne sijoittuivat kauas testien vaikeuksien alapuolelle. Kaikki nämä heikentävät θ:n määräytymistä. Yhdelläkään mallilla kaikki tulokset eivät olleet arvausrajalla.

## 4. Kustannusennuste

Valitulle kyvykkyystasolle θ_target lasketaan joka kuukausi halvin malli, jonka θ ≥ θ_target ja jolla on voimassa oleva hinta.

- **Mitä sovitetaan:** regressio ln(hinta) = α0 − α1·t sovitetaan *hintaennätyksiin* eli kuukausiin, joina halvin hinta laski, eikä toistuviin kuukausiarvoihin. Toistuvat arvot kaventaisivat väliä keinotekoisesti.
- **Puoliintumisaika:** T½ = ln 2 / α1. Jos α1 ≤ 0, puoliintumisaika on määrittelemätön.
- **Pysähtynyt lasku:** palainen malli ja lattiatesti tunnistavat, jos lasku on pysähtynyt.
- **Ennusteen rajaus:** ennuste rajataan välille [hintalattia, tason nykyinen halvin hinta]. Halvin saatavilla oleva hinta ei voi nousta.
- **Alaraja horisonteittain:** kyvykkyys nousee β θ vuodessa (rintaman kehitysvauhti). Siksi h kuukauden päästä tason kyvykkyys maksaa suunnilleen sen, mitä β·h/12 θ heikompi malli maksaa nyt. Alarajana on halvin malli, jolle taso − β·h/12 ≤ θ < taso. Raja otetaan käyttöön vain, jos malli on selvästi halvempi (alle 2/3 tason nykyisestä halvimmasta). Muuten alarajana on koko markkinan halvin malli. Lähes saman hintainen lähimalli ei kerro, mihin hinta voi pudota, ja jäädyttäisi ennusteen. Tason ylittäviä malleja ei lasketa mukaan, koska tason oma halvin olisi silloin aina raja.
- **Tila `at_floor`:** tila tarkoittaa, että taso on jo käytännössä koko markkinan hintalattialla. Silloin kehitys näkyy kyvykkyyden nousuna samalla hinnalla, ei hinnan laskuna.

## 5. Rajoitukset – lue ennen tulkintaa

- **Pieni otos:** rintamassa on vain noin 20 pistettä. Ennustevälit ovat leveitä, ja P50 ei ole ennustus vaan jakauman keskikohta.
- **Kalibraatiota ei voi vielä todentaa:** neljän lähtöpäivän backtestissä lähtöpäivien keskimääräinen osumisosuus ennustevälille oli 0.88 ± 0.12 (SE), ja kaikista arvioiduista pisteistä osui 0.89 (tavoite 0.80, mitattu 2026-11-17; kahdella vanhimmalla lähtöpäivällä pienen otoksen korjaus oli käytössä, A27). Samalla datalla ilman korjausta luvut ovat 0.75 ± 0.25 ja 0.78, eli ero johtuu korjauksesta. Vanhimman lähtöpäivän osumisosuus (0.50, 3/6 pistettä) vaihtelee kuitenkin jo bootstrap-arvonnan mukaan: eri rivijärjestyksellä tulos oli 2/6. Saman lähtöpäivän virheet ovat samanmerkkisiä, joten tehokas otoskoko on korkeintaan lähtöpäivien määrä (4) eikä pisteiden määrä. Lähtöpäivät jakavat lisäksi arviointipisteitä ja koulutusdataa, joten keskivirhe on todennäköisesti aliarvio. Aineisto ei siis kerro, ovatko välit hyvin kalibroituja, liian leveitä vai liian kapeita.
- **Rakennemuutosta ei voi ennakoida:** lähtöpäivän 2024-11 ennuste aliarvioi myöhemmän kehityksen (keskivirhe noin +1.1 θ), ja sen pisteistä vain puolet osui välille. Silloin kehitys oli hidasta (noin 0.8 θ/v), eikä myöhempää kiihtymistä voinut nähdä etukäteen. Kolmen uusimman lähtöpäivän pisteet osuivat kaikki välille. Välit kattavat pisteiden välisen hajonnan, mutta eivät koko trendin virhettä, kun kehitysvauhti muuttuu.
- **Rintamavalinnan harha:** rintamaan päätyy malleja, joiden mittausvirhe sattui olemaan positiivinen. Siksi havaitut ennätykset ovat *todellista* θ:aa korkeampia (synteettisesti noin +0.3 θ, kun SE on 0.3). Ennuste koskee havaittuja ennätyksiä, joihin tulevatkin ennätykset valikoituvat samalla tavalla, ja niihin nähden ennuste on harhaton. Todellisen kyvykkyyden suhteen se on hieman ylöspäin vinoutunut.
- **Pienen otoksen välit:** synteettisessä Monte Carlo -kokeessa (2026-11-17; lineaarinen tosi trendi) 80 %:n ennusteväli kattoi tulevista rintamapisteistä 0.81 ± 0.02, kun rintamassa oli noin 16 pistettä, mutta vain 0.72 ± 0.02, kun pisteitä oli noin 9. Siksi välit levennetään automaattisesti, kun rintamassa on alle 12 pistettä (A27; kokeessa 0.80 ± 0.02). Raja 12 on päätös (2026-11-17), ei mitattu taitekohta: kattavuutta on mitattu vain noin 9 ja 16 pisteen rintamilla. Korjaus koskee tällä hetkellä liukuvan backtestin kahta vanhinta lähtöpäivää (rintamassa 6 ja 9 pistettä) ja pieniä osa-alueita, ja raportti kertoo, milloin korjaus oli käytössä. Raportoitu kulmakertoimen väli (P10–P90) on liian kapea: samassa 250 toiston kokeessa se kattoi todellisen arvon vain 0.68–0.73 (± 0.03), myös korjauksen kanssa. Synteettisessä kokeessa pakotetulla lineaarisella mallilla syy oli välin kapeus (bootstrapin sd 0.106 vs. estimaattorin sd 0.128), ei piste-estimaatin harha. Kapeuden todennäköinen syy (hypoteesi) on, että ennätyspisteet eivät ole toisistaan riippumattomia, jolloin bootstrap aliarvioi kulmakertoimen hajontaa.
- **Hintalattia on heuristiikka:** jos lähitasolla ei ole selvästi halvempia malleja, alarajana on koko markkinan halvin malli, vaikka se olisi kyvykkyydeltään tason alapuolella. Raja voi myös olla liian tiukka, ja backtest raportoi, jos toteutunut hinta alitti koulutetun alarajan. Rajattu backtest-virhe mittaa silloin lattian osuvuutta, ei trendimallia, joten raportissa näytetään myös rajaamaton virhe.
- **Kontaminaatio:** poikkeamasuodatus tunnistaa vain jyrkät yksittäistapaukset. Laajaa, tasaista kontaminaatiota se ei havaitse.
- **Yksiulotteisuus:** IRT olettaa yhden kyvykkyysulottuvuuden. Osa-alueajot antavat osa-alueittaisen näkymän, mutta käyttävät samoja testiparametreja.
- **Hintadatan lisenssi:** simonw/llm-prices-datalla ei ole lisenssiä, ja OpenRouterin käyttöehdot kieltävät tietojen automaattisen keräämisen ja jakamisen. Hintoihin perustuvia lukuja ei siksi julkaista: ennusteraportti ja dashboard ovat vain paikallisia.
