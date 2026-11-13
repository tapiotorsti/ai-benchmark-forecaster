---
name: implement-milestone
description: Toteuta yksi toteutussuunnitelman kokonaisuus (K0–K7) alusta loppuun hyväksymiskriteereineen. Käytä kun käyttäjä pyytää aloittamaan, jatkamaan tai viimeistelemään kokonaisuuden, esim. "tee K2" tai "seuraava vaihe".
argument-hint: K<n>
---

# Kokonaisuuden toteutus

Argumentti: kokonaisuuden tunnus (esim. `K2`). Jos argumenttia ei ole, valitse `TOTEUTUSSUUNNITELMA.md`:n tilataulukosta ensimmäinen kokonaisuus, jonka riippuvuudet ovat valmiita ja joka ei itse ole valmis.

## 1. Lue konteksti
1. `TOTEUTUSSUUNNITELMA.md`: kokonaisuuden osio (tavoite, tehtävät, hyväksymiskriteerit), riippuvuudet ja siihen kohdistuvat aukot A1–A7.
2. Määrittelystä ne luvut, joihin kokonaisuus viittaa (skeema 3.2, IRT 4.x, ennusteet 5.x).
3. Olemassa oleva koodi, johon kokonaisuus kytkeytyy.

Jos riippuvuus on kesken, kerro se käyttäjälle ennen kuin jatkat.

## 2. Suunnittele
Kirjoita lyhyt työlista (tiedostot, funktiot, testit). Jokaiselle hyväksymiskriteerille pitää olla vähintään yksi automaattinen testi tai, jos kriteeri on laadullinen (esim. "järjestys uskottava"), raportti jonka käyttäjä voi tarkistaa.

## 3. Toteuta testit edellä
- Kirjoita hyväksymiskriteerien testit ensin (`tests/test_<moduuli>.py`).
- Toteuta koodi `src/forecaster/`-paketin alle CLAUDE.md:n konventioiden mukaan.
- Kytke valmis toiminto CLI-komentoon (poista vastaava `_not_implemented`-kutsu).
- Matematiikka: noudata määrittelyä ja aukkojen korjauksia. Jos poikkeat määrittelystä, kirjaa poikkeama ja syy suunnitelman aukkotaulukkoon.

## 4. Varmista
- Aja `/verify`.
- IRT- tai ennustekoodi (K2, K4, K5): pyydä `math-reviewer`-agentilta katselmointi ja käsittele löydökset.

## 5. Päivitä tila
- Merkitse `TOTEUTUSSUUNNITELMA.md`:n tilataulukkoon kokonaisuus valmiiksi (tai kesken + mitä puuttuu).
- Päivitä README:n komentotaulukko.
- Raportoi käyttäjälle: mitä tehtiin, kunkin hyväksymiskriteerin tila, havaitut uudet riskit ja seuraava suositeltu askel.
