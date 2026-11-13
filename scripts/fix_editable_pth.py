"""Korjaa editable-asennuksen .pth-tiedoston merkistön Windowsilla (Python < 3.13).

Python 3.12 ja vanhemmat lukevat .pth-tiedostot järjestelmän locale-merkistöllä
(esim. cp1252), mutta pip/hatchling kirjoittaa ne UTF-8:na. Jos projektipolussa
on ei-ASCII-merkkejä (esim. "Käyttäjä"), polku vääristyy eikä paketti löydy.

Aja jokaisen `pip install -e .` -komennon jälkeen:
    .venv\\Scripts\\python.exe scripts\\fix_editable_pth.py
"""

from __future__ import annotations

import locale
import site
import sys
from pathlib import Path


def main() -> int:
    if sys.version_info >= (3, 13):
        print("Python >= 3.13 lukee .pth-tiedostot UTF-8:na – korjausta ei tarvita.")
        return 0

    encoding = locale.getpreferredencoding(False)
    fixed = 0
    for sp in site.getsitepackages():
        for pth in Path(sp).glob("_editable_impl_*.pth"):
            raw = pth.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue  # ei UTF-8:aa → todennäköisesti jo korjattu
            if text.isascii():
                continue
            pth.write_bytes(text.encode(encoding))
            print(f"Korjattu ({encoding}): {pth}")
            fixed += 1
    if not fixed:
        print("Korjattavia .pth-tiedostoja ei löytynyt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
