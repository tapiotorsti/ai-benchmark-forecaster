---
name: verify
description: Aja projektin laatutarkistukset (ruff lint + format, pytest, check-config) ja raportoi tulos. Käytä aina ennen kuin ilmoitat koodimuutoksen tai kokonaisuuden valmiiksi.
---

# Laatutarkistus

Aja projektin juuressa järjestyksessä. Jatka kaikki vaiheet loppuun, vaikka jokin epäonnistuisi, jotta raportti on täydellinen.

```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\forecaster.exe check-config
```

Jos `.venv` puuttuu, luo se: `py -3.12 -m venv .venv` ja `.venv\Scripts\python.exe -m pip install -e ".[dev,ui]"`. Aja sen jälkeen `.venv\Scripts\python.exe scripts\fix_editable_pth.py`.

Jos `forecaster.exe` kaatuu virheeseen `ModuleNotFoundError: No module named 'forecaster'`, aja `scripts\fix_editable_pth.py`. Syy on ä-kirjaimet polussa (ks. CLAUDE.md).

## Virheiden käsittely
- Ruff-lint: korjaa `ruff check --fix .` vain jos korjaukset ovat turvallisia; tarkista diff.
- Ruff-format: `ruff format .` saa ajaa suoraan.
- Pytest: **älä** muuta testin odotusarvoa saadaksesi sen läpi, ellei testi itse ole todistettavasti väärin määrittelyyn nähden. Erityisesti IRT- ja ennustetestien toleransseja ei löysätä ilman perustelua.
- check-config: virhe tarkoittaa, että `config/*.yaml` ja `forecaster/config.py`:n mallit ovat ristiriidassa.

## Raportti
Kerro lyhyesti: kunkin vaiheen tila (OK / epäonnistui), epäonnistuneiden testien nimet ja virheen ydin, sekä mitä korjasit. Jos jotain jäi epäonnistuneeksi, sano se suoraan.
