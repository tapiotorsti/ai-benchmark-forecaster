# Ajankohtaan sidottu backtest (A35)

_Generoitu 2026-11-25 komennolla `forecaster backtest-vintage`. Toteumien θ: IRT-ajo `irt_overall_20261123T082225990742_6ff11b99`. Menetelmäversio 1._

> **Lähteet.** Benchmark-data: [Epoch AI](https://epoch.ai/data), lisenssi [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); vanhat datapaketit [Internet Archivesta](https://web.archive.org/). Raportti on johdettu aineistosta: tulokset on normalisoitu, aggregoitu ja niihin on sovitettu IRT-malli.

## Mitä tämä mittaa
Tavallinen backtest käyttää nykyisen IRT-ajon θ-arvoja, jotka on sovitettu myös lähtöpäivän jälkeen julkaistulla datalla. Tässä IRT sovitetaan jokaiselle lähtöpäivälle uudelleen vain niillä tuloksilla, jotka olivat Epochin datapaketissa (arkistoitu tilannekuva viimeistään lähtöpäivänä; lähtöpäivät ovat tilannekuvien päiviä), ja ennustetta verrataan samoihin toteutuneisiin rintamapisteisiin enintään 12 kk eteenpäin. **Ajankohtaan sidottu** = likimääräinen etukäteistilanne (päätulos); **nykyisillä θ:illa** = tavallinen backtest samoilla lähtöpäivillä. Toteumat arvioidaan nykyisen IRT-ajon θ:lla. **Virhe** = toteuma − P50: positiivinen = ennuste oli toteumaa matalampi. **Tasosiirtymällä korjattu** on herkkyystarkastelu: toteumat siirretään lähtöpäivän asteikolle vakiintuneiden mallien (SE < 0.3, julkaistu yli vuosi ennen lähtöpäivää) θ-muutoksen mediaanilla.

**Kypsät toteumat** = mallit, jotka on julkaistu viimeistään 2026-08-25. Tuoreimpien mallien θ perustuu vielä harvoihin tuloksiin, joten niiden toteumat ovat epävarmoja; hypoteesina rintamamallin θ voi olla valintaharhan vuoksi yläkanttiin (vrt. A31). Kypsät toteumat ovat myös lyhyemmän horisontin ja aiemman jakson pisteitä, joten ero kypsien ja kaikkien välillä ei erota näitä syitä.

Rajoitukset: lähtöpäivät ovat tilannekuvien päiviä (data ei ole vanhentunutta). Tilannekuvasta käytetään vain tietoa siitä, mitkä tulokset olivat julkaistuja; pistemäärät, mallien julkaisupäivät, versioiden ryhmittely ja aliakset, testien valinta ja arvausrajat sekä ankkurit ovat nykyisestä datasta. Menetelmän asetukset (A27, A28, A33) on valittu tämän jakson backtestien perusteella, joten asetelma ei ole menetelmän valinnan kannalta otoksen ulkopuolinen. Lähtöpäivät arvioivat osin samoja malleja, joten tehokas otoskoko on enintään eri mallien määrä, ja lähtöpäivien SE on alaraja.

## Yhteenveto
Lähtöpäiviä 10. Odotus välin sisällä ≈ 0.80.

Kaikki toteumat:

| | välin sisällä, lähtöpäivien ka. ± SE (min–max) | välin sisällä, pisteistä | pisteitä / eri malleja | virhe θ | itseisvirhe θ | välin leveys θ |
|---|---|---|---|---|---|---|
| ajankohtaan sidottu | 0.84 ± 0.08 (0.20–1.00) | 0.89 | 72 / 10 | +0.13 | 0.21 | 1.21 |
| – tasosiirtymällä korjattu (herkkyys) | 0.71 ± 0.10 (0.20–1.00) | 0.78 | 72 / 10 | +0.19 | 0.24 | 1.21 |
| nykyisillä θ:illa (tavallinen) | 1.00 ± 0.00 (1.00–1.00) | 1.00 | 72 / 10 | +0.13 | 0.19 | 1.07 |

Kypsät toteumat (julkaistu viimeistään 2026-08-25):

| | välin sisällä, lähtöpäivien ka. ± SE (min–max) | välin sisällä, pisteistä | pisteitä / eri malleja | virhe θ | itseisvirhe θ | välin leveys θ |
|---|---|---|---|---|---|---|
| ajankohtaan sidottu | 1.00 ± 0.00 (1.00–1.00) | 1.00 | 24 / 5 | -0.03 | 0.11 | 1.20 |
| – tasosiirtymällä korjattu (herkkyys) | 1.00 ± 0.00 (1.00–1.00) | 1.00 | 24 / 5 | +0.03 | 0.11 | 1.20 |
| nykyisillä θ:illa (tavallinen) | 1.00 ± 0.00 (1.00–1.00) | 1.00 | 24 / 5 | +0.02 | 0.11 | 1.05 |

## Lähtöpäivittäin

| lähtöpäivä | tilannekuva | tuloksia | malleja | rintama | θ/v | θ/v nyk. | tasosiirtymä | pisteitä | sisällä | sisällä nyk. | virhe | virhe nyk. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2025-11-25 | 2025-11-25 | 1690 | 192 | 15 | 2.00 | 1.80 | -0.05 | 10 | 1.00 | 1.00 | -0.15 | +0.19 |
| 2025-12-14 | 2025-12-14 | 1736 | 196 | 15 | 1.89 | 1.84 | -0.05 | 9 | 1.00 | 1.00 | +0.02 | +0.07 |
| 2026-01-01 | 2026-01-01 | 1774 | 200 | 15 | 1.85 | 1.84 | -0.05 | 9 | 1.00 | 1.00 | +0.06 | +0.07 |
| 2026-02-04 | 2026-02-04 | 1826 | 202 | 15 | 1.78 | 1.75 | -0.05 | 9 | 1.00 | 1.00 | +0.14 | +0.17 |
| 2026-03-20 | 2026-03-20 | 2189 | 215 | 18 | 1.74 | 1.80 | -0.08 | 8 | 1.00 | 1.00 | +0.19 | +0.11 |
| 2026-04-27 | 2026-04-27 | 2264 | 222 | 18 | 1.70 | 1.79 | -0.09 | 7 | 1.00 | 1.00 | +0.21 | +0.09 |
| 2026-06-10 | 2026-06-10 | 2412 | 233 | 18 | 1.67 | 1.79 | -0.08 | 6 | 0.67 | 1.00 | +0.28 | +0.12 |
| 2026-07-22 | 2026-07-22 | 2685 | 252 | 19 | 1.72 | 1.78 | -0.08 | 6 | 0.83 | 1.00 | +0.20 | +0.13 |
| 2026-08-22 | 2026-08-22 | 3160 | 266 | 19 | 1.64 | 1.77 | -0.08 | 5 | 0.20 | 1.00 | +0.35 | +0.21 |
| 2026-09-29 | 2026-09-29 | 4157 | 316 | 22 | 1.74 | 1.77 | +0.00 | 3 | 0.67 | 1.00 | +0.33 | +0.26 |

`rintama` ja `θ/v` = lähtöpäivän IRT-ajon rintamapisteet ja kehitysvauhti; `nyk.` = sama nykyisillä θ:illa; `tasosiirtymä` = vakiintuneiden mallien θ-muutoksen mediaani (nykyinen − lähtöpäivän).
