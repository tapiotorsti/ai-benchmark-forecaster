"""Yhteiset testiasetukset.

POLARS_MAX_THREADS=1 ennen polarsin ensimmäistä tuontia: dashboardin AppTest-testit ajavat
sivuja Streamlitin säikeissä, ja Polarsin säiepooli + Torchin OpenMP kaatoi prosessin
Windowsissa (ks. app/dashboard.py). Testidata on pientä, joten yksi säie ei hidasta.
"""

import os

os.environ.setdefault("POLARS_MAX_THREADS", "1")
