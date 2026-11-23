"""Dashboardin opasteet (5.1): termien selitteet ja sivukohtaiset "Miten tätä luetaan" -tekstit.

Tekstit ovat yhdessä paikassa, jotta ne pysyvät yhtenäisinä metodologian kanssa
(docs/metodologia.md) ja ne voidaan katselmoida kerralla (report-reviewer). Tulkintaväitteet
kirjataan faktana vain, jos ne on todennettu; muuten ne merkitään hypoteeseiksi tai
sopimuksiksi (CLAUDE.md).
"""

from __future__ import annotations

# --- Termit: mittareiden ja taulukon sarakkeiden help=-tekstit --------------------------

TERMS: dict[str, str] = {
    "theta": (
        "Kyvykkyys yhteisellä asteikolla (2PL-IRT). Ankkurit: GPT-4 (kesäkuu 2023) = 0 ja "
        "GPT-5 = 3.5. Asteikko on suhteellinen: θ ei ole prosentti eikä pistemäärä."
    ),
    "theta_se": (
        "θ:n keskivirhe. Suuri SE tarkoittaa epävarmaa θ:aa, yleensä koska mallilla on vähän "
        "tuloksia tai ne ovat heikosti erottelevista testeistä. Ennusteista jätetään pois "
        "mallit, joiden SE > 1."
    ),
    "frontier": (
        "Rintama (Pareto): malli, jonka θ ylitti kaikkien aiemmin julkaistujen mallien θ:n. "
        "Saman päivän julkaisuista vain paras."
    ),
    "slope": (
        "Kehitysvauhti: rintaman θ:n kasvu vuodessa. Bootstrap-jakauman mediaani, jossa ovat "
        "mukana sovitusikkunat (A28–A29)."
    ),
    "p50_interval": (
        "P50 on bootstrap-jakauman keskikohta, ei varma ennuste. P10–P90 on nimellisesti 80 %:n "
        "väli yksittäiselle tulevalle rintamamallille; kalibraatiota ei ole vielä todennettu."
    ),
    "n_frontier": (
        "Rintamapisteet sovitusjaksolla. Trendi sovitetaan vain näihin, joten pieni määrä "
        "tarkoittaa epävarmaa trendiä. Väli levenee, mutta pienellä otoksella se voi silti olla "
        "liian kapea (A27)."
    ),
    "halving": (
        "Aika, jossa tason halvin hinta puolittuu nykyisellä laskuvauhdilla. – tai ∞ = hinta "
        "ei laske."
    ),
    "historical_halving": "Puoliintumisaika koko sovitusjaksolta (vertailukohta nykyvauhdille).",
    "cost_status": (
        "Hinta laskee / lasku pysähtynyt (viimeisen 12 kk:n trendi ei laske) / ei laske / "
        "markkinan lattialla (halvin hinta on jo lähellä koko markkinan halvinta mallia)."
    ),
    "cheapest": (
        "Halvin julkaistu listahinta mallille, jonka θ ≥ taso: (3 × input + output) / 4 USD "
        "miljoonaa tokenia kohden. Toteutunut hinta voi olla moninkertainen."
    ),
    "b": (
        "Vaikeus: θ, jolla malli saa puolet arvausrajan ja katon välisestä vaihteluvälistä. "
        "Kun erottelukyky a on pieni, b ei kuvaa vaikeutta luotettavasti."
    ),
    "a": (
        "Erottelukyky: kuinka jyrkästi tulos nousee θ:n mukana. Pieni a = testi kertoo θ:sta vähän."
    ),
    "c": "Arvausraja: satunnaisarvauksen tulos normalisoidulla asteikolla (Epochin metatiedoista).",
    "saturation": (
        "Parhaan mallin odotettu osuus arvausrajan yläpuolisesta vaihteluvälistä. ≥ 0.9: testi "
        "erottelee kärkimalleja enää heikosti (raja on sopimus, ei mitattu)."
    ),
    "outliers": (
        "Sovituksesta poistetut epäilyttävän hyvät tulokset (robusti z > 3, enintään yksi per "
        "malli). Mahdollinen kontaminaatio, ei todennettu."
    ),
    "residual": (
        "Havaittu − ennustettu tulos. Positiivinen = malli pärjäsi testissä paremmin kuin sen θ "
        "ennustaa."
    ),
    "rmse": "Jäännösten neliökeskiarvon juuri: kuinka hyvin yksi θ selittää mallin tulokset.",
    "prospective": (
        "Aiemmin tallennetut ennusteet verrattuna niiden jälkeen julkaistuihin rintamamalleihin. "
        "Toisin kuin backtest, ennuste on tehty oikeasti etukäteen (A30). Toteumat ovat "
        "nykyisestä IRT-ajosta, joten mallin θ voi poiketa ennustehetken arvosta."
    ),
}


# --- Sivukohtaiset ohjeet ------------------------------------------------------------------

PAGE_GUIDES: dict[str, str] = {
    "overview": """
- **Kuvaaja:** jokainen piste on malli julkaisupäivän ja kyvykkyyden θ mukaan. Siniset pisteet
  ovat rintama eli mallit, jotka ylittivät kaikki aiemmat; niissä virhepalkki on ± SE.
- **Kaistat:** tummempi kaista on trendin epävarmuus, vaaleampi väli yksittäiselle tulevalle
  rintamamallille (P10–P90, nimellisesti 80 %). P50 on jakauman keskikohta, ei varma ennuste.
- **Kehitysvauhti** on rintaman θ:n kasvu vuodessa. Ennuste olettaa, että vauhti jatkuu
  samana; se ei ennakoi tulevia muutoksia vauhdissa.
- **Mitä ei voi päätellä:**
  - Rintamassa on vain muutamia kymmeniä pisteitä (määrä näkyy mittarissa), joten välit ovat
    leveitä. Kalibraatiota ei voi vielä todentaa: aiempien ennusteiden seuranta alkoi
    2026-11-19.
  - Kehitysvauhdin P10–P90 on todennäköisesti liian kapea (A27).
  - Rintamaan päätyvät mallit, joiden mittausvirhe sattui olemaan positiivinen, joten ennuste
    koskee havaittuja ennätyksiä. Todellinen kyvykkyys voi olla hieman matalampi.
  - Osa-alueiden θ on laskettu kokonaisasteikon testiparametreilla.
""",
    "models": """
- **Taulukko:** mallien kyvykkyys θ ja sen keskivirhe SE. Kahden mallin järjestys on epävarma,
  jos niiden θ-ero on SE:iden suuruusluokkaa.
- **Mallikohtainen näkymä:** havaittu tulos (piste) ja IRT-mallin ennuste (viiva) jokaisessa
  testissä vaikeusjärjestyksessä. Ennuste näytetään myös testeille, joita mallilla ei ole.
- **Jäännös** = havaittu − ennustettu. Suuret jäännökset kertovat, ettei yksi θ selitä mallin
  tuloksia hyvin (esim. malli on vahva yhdellä osa-alueella).
- **Poikkeama:** sovituksesta poistettu epäilyttävän hyvä tulos. Huonoja tuloksia ei poisteta,
  koska ne voivat olla aitoa heikkoutta.
- **Mitä ei voi päätellä:** θ mittaa kykyä niissä testeissä, jotka aineistossa ovat. Se ei ole
  yleinen "älykkyys", ja testien valinta vaikuttaa siihen.
""",
    "benchmarks": """
- **b (vaikeus):** θ, jolla malli saa puolet arvausrajan ja katon välisestä vaihteluvälistä.
- **a (erottelukyky):** kuinka jyrkästi tulos nousee θ:n mukana. Kun a on pieni (alle noin
  0.35), testi kertoo θ:sta vähän, eikä sen b:tä kannata tulkita vaikeutena.
- **c (arvausraja):** satunnaisarvauksen tulos normalisoidulla asteikolla.
- **Saturaatio:** parhaan mallin odotettu osuus vaihteluvälistä. Kun se on ≥ 0.9, testi erottelee
  kärkimalleja enää heikosti (raja on sopimus, ei mitattu).
- **Poistetut poikkeamat:** epäilyttävän hyvät yksittäistulokset (enintään yksi per malli), jotka
  voivat viitata kontaminaatioon. Myös poikkeuksellisen huonot tulokset liputetaan, mutta niitä
  ei poisteta. Laajaa, tasaista kontaminaatiota suodatin ei havaitse.
""",
    "costs": """
- **Iso-kyvykkyyskäyrä:** joka kuukausi halvin malli, jonka θ ≥ valittu taso. Kun käyrä laskee,
  sama kyvykkyys halpenee.
- **Hinta** on halvin julkaistu listahinta (3 × input + output) / 4 USD / 1 M tokenia.
  Toteutunut keskihinta voi olla moninkertainen, joten luvut kuvaavat parasta mahdollista
  hintaa, eivät tyypillistä.
- **Puoliintumisaika** kertoo, kuinka nopeasti tason halvin hinta puolittuu nykyvauhdilla.
- **Ennuste** on rajattu välille [alaraja, nykyinen halvin]: halvin saatavilla oleva hinta ei
  voi nousta. Alaraja h kuukauden päähän on halvin malli, joka on enintään β·h/12 θ tasoa
  heikompi (β = kehitysvauhti; 24 kk:n päähän noin 3.7 θ), jos se on selvästi (alle 2/3)
  halvempi kuin tason nykyinen halvin. Muuten alaraja on koko markkinan halvin (A26,
  heuristiikka).
- **Markkinan lattialla:** tason halvin hinta on jo lähellä koko markkinan halvinta mallia.
  Ennuste olettaa, ettei hinta laske markkinan nykyisen halvimman alle, joten kehityksen
  oletetaan näkyvän kyvykkyyden nousuna samalla hinnalla. Uudet halvat mallit voivat laskea
  lattiaa.
- **Mitä ei voi päätellä:** jos tasolla on alle vuoden hintahistoria, puoliintumisaika on hyvin
  epävarma, eikä lyhyt jakso kerro, jatkuuko lasku. Hintadataa ei julkaista lisenssisyistä.
""",
    "simulator": """
- **Mitä tapahtuu:** IRT sovitetaan uudelleen muutetuilla painoilla tai ilman valittuja testejä.
  Tallennettuun ajoon ja tietokantaan ei kosketa.
- **Δθ** on mallin θ:n muutos tallennettuun ajoon nähden. Suuri muutos kertoo, että mallin θ
  nojaa paljon muutettuihin testeihin.
- **Rintaman kulmakerroin** on tässä lineaarinen pienimmän neliösumman suora koko jaksolle (ei
  bootstrapia, ei palaista mallia, ei SE-suodatusta). Se ei ole vertailukelpoinen Yleiskuvan
  kehitysvauhtiin eikä sen koko jakson kulmakertoimeen. Vertaa sitä vain tallennetun ajon
  samaan lukuun (delta).
- Ankkurit pidetään kiinteinä, kunhan ankkurimallit jäävät aineistoon, joten asteikot ovat
  vertailukelpoisia. Keskivirheitä ei lasketa.
""",
    "data_quality": """
- **Päivitysajot:** viikoittaisen `forecaster update` -ajon tila ja hälytykset. WARNING ei ole
  virhe: se tarkoittaa yleensä uusia tunnistamattomia mallinimiä.
- **Ingest-ajot:** jokaisen datalähteen tuonti, haetut ja tallennetut rivit.
- **Tuntemattomat nimet:** pääosin hintalähteiden mallinimet, joita ei tunnistettu. Ne eivät
  ole virheitä: useimmista malleista ei ole benchmark-tuloksia. Ehdotukset kohdistuksiksi saa
  komennolla `forecaster aliases`.
- **Karanteeni:** rivit, joita ei voitu tuoda (esim. malli ilman julkaisupäivää).
""",
}


def term(key: str) -> str:
    """Termin selite help=-parametriin."""
    return TERMS[key]
