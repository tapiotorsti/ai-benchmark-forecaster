"""K5: ennustemoottori (määrittely 5).

Kyvykkyys (5.1)
- Pareto-rintama: mallit, joiden θ ylittää kaikkien aiemmin julkaistujen maksimin;
  saman päivän julkaisuista vain paras (A11). Sovitusikkuna fit_start → (päätös: 2023-01-01).
- Trendi θ(t) = β0 + β1·t (t vuosina). Palainen laajennus: sarana viimeisen
  `piecewise_window_months` kohdalla, otetaan käyttöön jos saranan kerroin p < α.
- Bootstrap B kertaa (A5): θ* ~ N(θ̂, SE²) KAIKILLE malleille → rintama lasketaan uudelleen
  (θ* ratkaisee vain, mitkä mallit ovat rintamassa) → rintamapisteet otetaan palauttaen →
  OLS alkuperäisiin θ̂:hin (A25: ei mittausvirheen kaksoislaskentaa) → mallivalinta
  (lineaarinen/palainen) joka drawissa. Raportoidaan kaksi kaistaa:
    trend      = trendisuoran epävarmuus (luottamusväli)
    prediction = trend + jäännöskohina N(0, s²) (ennusteväli tulevan rintamapisteen arvolle)

Kustannus (5.2, A16)
- Kullekin tasolle θ_target: kunkin kuukauden halvin malli, jolla θ ≥ θ_target ja voimassa
  oleva hinta (model_prices, ASOF). Kuukausisarja on porrasfunktio → regressio sovitetaan
  HINTAENNÄTYKSIIN (kuukaudet, joina halvin hinta laski), ei toistuviin kuukausiarvoihin,
  jotka kaventaisivat luottamusvälin keinotekoisesti.
- ln(cost) = α0 − α1·t (t kuukausina), T½ = ln 2 / α1. α1 ≤ 0 → T½ määrittelemätön.
- Bootstrap: θ-draw (kuka ylittää tason) + ennätyspisteiden uudelleenotanta.

Backtest: sovitus vain cutoff-päivään asti julkaistulla datalla, vertailu toteutuneeseen.
Huom: θ-arvot tulevat nykyisestä IRT-ajosta (sisältää myöhemmän datan) – lievä vuoto.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np
import polars as pl
import statsmodels.api as sm
from scipy import stats

DAYS_PER_YEAR = 365.25
DAYS_PER_MONTH = DAYS_PER_YEAR / 12
FLOOR_TOLERANCE = 1.5  # halvin hinta ≤ 1.5 × markkinan lattia → taso on "lattialla"


@dataclass(frozen=True)
class ForecastConfig:
    fit_start: date = date(2023, 1, 1)
    horizons_months: tuple[int, ...] = (6, 12, 18, 24)
    n_boot: int = 1000
    seed: int = 0
    piecewise_window_months: int = 12
    piecewise_alpha: float = 0.05
    min_points: int = 4
    level_floor: bool = False  # A26: horisonttikohtainen lähitason lattia (False = markkina)
    # Pienen otoksen korjaus kyvykkyyden bootstrapiin (A27): "none" | "inflate" | "auto"
    # (auto = inflate, kun rintamassa on alle small_sample_max_points pistettä)
    small_sample: str = "none"
    small_sample_max_points: int = 12
    # Sovitusikkunan epävarmuus (A28): jokaisessa drawissa sovitus alkaa satunnaisesti joko
    # fit_startista tai as_of − w kk (w näistä). Tyhjä = pois (vain koko jakso).
    fit_windows_months: tuple[int, ...] = ()
    cost_targets: dict[str, float] = field(default_factory=dict)


# --- Apufunktiot ----------------------------------------------------------------


def add_months(d: date, months: int) -> date:
    """Kalenterikuukausien lisäys; kuun loppu leikataan (31.10. + 4 kk → 28.2.)."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def month_starts(start: date, end: date) -> list[date]:
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        out.append(cur)
        cur = add_months(cur, 1)
    return out


def years_since(d: np.ndarray | list, origin: date) -> np.ndarray:
    arr = np.asarray(d, dtype="datetime64[D]")
    return (arr - np.datetime64(origin, "D")).astype(float) / DAYS_PER_YEAR


def frontier_mask(release: np.ndarray, theta: np.ndarray) -> np.ndarray:
    """Pareto-rintama (5.1 + A11). release: datetime64[D], theta: float. Palauttaa bool-maskin."""
    order = np.lexsort((-theta, release))  # päivä nousevasti, saman päivän paras ensin
    mask = np.zeros(len(theta), dtype=bool)
    best_before = -np.inf
    i = 0
    while i < len(order):
        j = i
        day = release[order[i]]
        while j < len(order) and release[order[j]] == day:
            j += 1
        top = order[i]  # päivän paras
        if theta[top] > best_before:
            mask[top] = True
        best_before = max(best_before, theta[order[i:j]].max())
        i = j
    return mask


def _design(t: np.ndarray, knot: float | None) -> np.ndarray:
    cols = [np.ones_like(t), t]
    if knot is not None:
        cols.append(np.maximum(0.0, t - knot))
    return np.column_stack(cols)


def _ols(t: np.ndarray, y: np.ndarray, knot: float | None):
    return sm.OLS(y, _design(t, knot)).fit()


def _pct(x: np.ndarray, q=(10, 50, 90)) -> list[float | None]:
    x = x[np.isfinite(x)]
    return [float(v) for v in np.percentile(x, q)] if x.size else [None] * len(q)


def _pct_unbounded(x: np.ndarray, q=(10, 50, 90)) -> list[float | None]:
    """Persentiilit KAIKISTA drawista, joissa +inf on sallittu arvo (esim. T½, kun hinta ei
    laske). Ääretön persentiili → None (= "ei laske"). Ei interpolointia inf-arvojen yli."""
    x = x[~np.isnan(x)]
    if not x.size:
        return [None] * len(q)
    vals = np.percentile(x, q, method="inverted_cdf")
    return [float(v) if np.isfinite(v) else None for v in vals]


def _piece_p_value(X: np.ndarray, y: np.ndarray, beta: np.ndarray, resid: np.ndarray) -> float:
    """OLS-t-testin p-arvo saranan kertoimelle (sarake 2), kevyt versio bootstrap-silmukkaan."""
    dof = len(y) - X.shape[1]
    if dof <= 0:
        return 1.0
    s2 = float(resid @ resid) / dof
    try:
        var = s2 * np.linalg.inv(X.T @ X)[2, 2]
    except np.linalg.LinAlgError:
        return 1.0
    if not np.isfinite(var) or var <= 0:
        return 1.0
    return float(2 * stats.t.sf(abs(beta[2]) / math.sqrt(var), dof))


# --- Kyvykkyysennuste ---------------------------------------------------------------


def capability_forecast(
    scores: pl.DataFrame,
    cfg: ForecastConfig,
    as_of: date,
    eval_dates: list[date] | None = None,
) -> dict:
    """scores: model_id, release_date, theta, theta_se (kaikki mallit; rintama lasketaan tässä).

    eval_dates: lisäpäivät, joille bootstrap-jakaumat palautetaan (backtest).
    """
    rel = scores["release_date"].to_numpy().astype("datetime64[D]")
    theta = scores["theta"].to_numpy().astype(float)
    se = scores["theta_se"].to_numpy().astype(float)
    in_window = rel >= np.datetime64(cfg.fit_start, "D")
    t_all = years_since(rel, cfg.fit_start)
    knot = float(years_since([add_months(as_of, -cfg.piecewise_window_months)], cfg.fit_start)[0])

    fmask = frontier_mask(rel, theta) & in_window
    t_f, y_f = t_all[fmask], theta[fmask]
    if fmask.sum() < cfg.min_points:
        raise ValueError(
            f"Rintamassa vain {int(fmask.sum())} pistettä ikkunassa (min {cfg.min_points})"
        )

    lin = _ols(t_f, y_f, None)
    piece_p, use_piece, recent_slope = None, False, None
    if (t_f > knot).sum() >= 2:
        pw = _ols(t_f, y_f, knot)
        piece_p = float(pw.pvalues[2])
        recent_slope = float(pw.params[1] + pw.params[2])
        use_piece = piece_p < cfg.piecewise_alpha

    horizons = [add_months(as_of, h) for h in cfg.horizons_months]
    eval_dates = list(eval_dates or [])
    t_h = years_since(horizons + eval_dates, cfg.fit_start)

    def fit(X: np.ndarray, y: np.ndarray):
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        return beta, y - X @ beta

    if cfg.small_sample not in ("none", "inflate", "auto"):
        raise ValueError(f"small_sample: none | inflate | auto (saatiin {cfg.small_sample!r})")
    inflate = cfg.small_sample == "inflate" or (
        cfg.small_sample == "auto" and int(fmask.sum()) < cfg.small_sample_max_points
    )
    # Pienen otoksen korjaus ("inflate", A27): pisteittäinen bootstrap aliarvioi kertoimien
    # hajonnan ja drawin jäännöshajonnan (s*² ≈ σ²·(n−p)/n). Drawin kertoimet skaalataan
    # alkuperäisen rintaman piste-estimaatin ympäriltä kertoimella k = √(n/(n−p)) ja
    # ennustekohina samalla k:lla. n = drawin rintamapisteet, p = parametrit.
    # Rajoitukset (katselmointi): rintaman pisteet eivät ole i.i.d., joten kulmakertoimen väli
    # jää silti kapeaksi; palaisen drawin korjaus vain, jos alkuperäisessä rintamassa on
    # saranan jälkeen ≥ 2 pistettä (muuten saranakerroin interpoloisi yhden pisteen).
    ref_params = {None: lin.params}
    if (t_f > knot).sum() >= 2 and fmask.sum() > 3:
        ref_params[knot] = _ols(t_f, y_f, knot).params

    # Sovitusikkunan epävarmuus (A28): kehitysvauhti on vaihdellut (esim. 2023 hidas, 2024
    # alkaen nopea), eikä tiedetä, mikä jakso kuvaa nykyistä vauhtia. Jokaisessa drawissa
    # sovitus alkaa satunnaisesti valitusta kohdasta (yhtä suuret painot). Väli levenee aina
    # hieman ja enemmän, kun tuore ja pitkän aikavälin vauhti eroavat; mediaani siirtyy tuoreen
    # vauhdin suuntaan. Lyhyissä ikkunoissa ei saranatestiä (ikkuna itse kuvaa tuoretta vauhtia).
    # Ikkuna, joka alkaa ennen fit_startia, jätetään pois (= koko jakso).
    # Ikkuna otetaan mukaan vain, jos sen alkuperäisessä rintamassa on vähintään min_points + 1
    # pistettä ja 3 eri ajankohtaa: muuten lähes jokainen sen drawi epäonnistuisi, mikä
    # vääristäisi sekoituksen painot ja voisi kaataa koko ennusteen (katselmointi).
    window_starts: list[float | None] = [None]
    window_ref: dict[float, tuple[np.ndarray, int]] = {}
    window_ref_piece: dict[float, np.ndarray] = {}  # palaisten ikkunadrawien A27-referenssi
    for w in cfg.fit_windows_months:
        ws = float(years_since([add_months(as_of, -w)], cfg.fit_start)[0])
        m = t_f >= ws
        if (ws <= 0 or ws in window_starts or m.sum() < cfg.min_points + 1
                or np.unique(t_f[m]).size < 3):  # fmt: skip
            continue
        window_starts.append(ws)
        window_ref[ws] = (_ols(t_f[m], y_f[m], None).params, int(m.sum()))
        tw = t_f[m]
        if use_piece and ws < knot and (tw > knot).sum() >= 2 and (tw <= knot).sum() >= 2:
            window_ref_piece[ws] = _ols(tw, y_f[m], knot).params

    # Mallivalinta tehdään JOKAISESSA drawissa uudelleen (katselmointi K5): pelkän
    # piste-estimaatin valinta aliarvioi rakenne-epävarmuuden (lineaarinen vs. kiihtyvä).
    rng = np.random.default_rng(cfg.seed)
    trend_draws, pred_draws, slopes, n_piece = [], [], [], 0
    n_windowed, n_piece_win = 0, 0
    for _ in range(cfg.n_boot):
        # Ikkunan arvonta vain, kun ikkunoita on: muuten satunnaislukuvirta on ennallaan
        ws = window_starts[rng.integers(len(window_starts))] if len(window_starts) > 1 else None
        th = theta + rng.normal(0.0, 1.0, len(theta)) * se
        fm = frontier_mask(rel, th) & in_window
        if ws is not None:
            fm &= t_all >= ws
        idx = np.flatnonzero(fm)
        if idx.size < cfg.min_points:
            continue
        pick = rng.choice(idx, size=idx.size, replace=True)
        # A25: θ-arvonta ratkaisee vain rintamajäsenyyden; sovitus alkuperäisiin θ̂:hin. θ̂:n
        # mittausvirhe on jo jäännöksissä (pisteittäinen bootstrap) – arvotun θ:n käyttö laskisi
        # sen kahdesti ja rintaman uudelleenpoiminta siirtäisi mediaania ylöspäin.
        tb, yb = t_all[pick], theta[pick]
        if np.unique(tb).size < 3:
            continue
        draw_knot = None
        X = _design(tb, None)
        beta, resid = fit(X, yb)
        # Valintatesti drawin UNIIKEILLA pisteillä: palauttaen otannan kaksoiskappaleet
        # tekisivät t-testistä liian liberaalin (lineaarisella datalla ~30 % väärää valintaa).
        uniq = np.unique(pick)
        tu, yu = t_all[uniq], theta[uniq]
        # Saranatesti koko jakson draweissa. Kun koko jakson malli on palainen (murros jo
        # tunnistettu), myös saranan yli ulottuvissa ikkunoissa: muuten ikkunan suora sekoittaisi
        # vanhan ja uuden vauhdin ja painaisi mediaania alas (katselmointi A29: 10–20 %).
        # Ikkunassa vaaditaan lisäksi ≥ 2 pistettä ennen saranaa (muuten sarakkeet ovat
        # kollineaariset ja p-arvo merkityksetön).
        hinge_ok = ws is None or (use_piece and ws < knot and (tu <= knot).sum() >= 2)
        if hinge_ok and (tu > knot).sum() >= 2 and uniq.size >= 5:
            Xu = _design(tu, knot)
            bu, ru = fit(Xu, yu)
            if _piece_p_value(Xu, yu, bu, ru) < cfg.piecewise_alpha:
                Xp = _design(tb, knot)
                bp, rp = fit(Xp, yb)
                draw_knot, X, beta, resid = knot, Xp, bp, rp
                if ws is None:
                    n_piece += 1
                else:
                    n_piece_win += 1
        s = math.sqrt(max(resid @ resid / max(len(yb) - X.shape[1], 1), 0.0))
        p = X.shape[1]
        noise = rng.normal(0.0, s, len(t_h))
        if ws is None:
            ref = ref_params.get(draw_knot) if inflate else None
        else:
            n_windowed += 1
            wr = window_ref[ws]
            # auto: pienen otoksen korjaus ikkunan oman (alkuperäisen) rintaman koon mukaan
            w_inflate = cfg.small_sample == "inflate" or (
                cfg.small_sample == "auto" and wr[1] < cfg.small_sample_max_points
            )
            # Referenssi saman ikkunan alkuperäisestä rintamasta (lineaarinen tai palainen)
            if not w_inflate:
                ref = None
            elif draw_knot is None:
                ref = wr[0]
            else:
                ref = window_ref_piece.get(ws)
        if ref is not None and idx.size > p:
            k = math.sqrt(idx.size / (idx.size - p))
            beta = ref + k * (beta - ref)
            noise = noise * k
        mu = _design(t_h, draw_knot) @ beta
        pr = mu + noise
        if ws is not None:
            # Ikkunan suoraa ei ekstrapoloida ikkunaa edeltävään aikaan (dashboardin
            # historiakaista): NaN jätetään persentiileistä pois (_pct)
            before = t_h < ws
            mu, pr = np.where(before, np.nan, mu), np.where(before, np.nan, pr)
        trend_draws.append(mu)
        pred_draws.append(pr)
        slopes.append(beta[1] + (beta[2] if draw_knot is not None else 0.0))
    if len(trend_draws) < cfg.n_boot // 2:
        raise ValueError(f"Bootstrap epäonnistui ({len(trend_draws)}/{cfg.n_boot} onnistunutta)")
    trend, pred = np.array(trend_draws), np.array(pred_draws)

    point = (pw if use_piece else lin).params
    return {
        "as_of": as_of,
        "fit_start": cfg.fit_start,
        "n_frontier": int(fmask.sum()),
        "frontier_models": scores.filter(pl.Series(fmask))["model_id"].to_list(),
        "model": "piecewise" if use_piece else "linear",
        # Otsikkoluku = bootstrap-jakauman mediaani (A29): sama jakauma, josta horisonttien
        # ennusteet tulevat (sis. sovitusikkunat A28). Koko jakson OLS jää diagnostiikaksi.
        "slope_per_year": _pct(np.array(slopes))[1],
        "slope_per_year_full_period": float(point[1] + (point[2] if use_piece else 0.0)),
        "slope_per_year_p10_p50_p90": _pct(np.array(slopes)),
        "linear_slope_per_year": float(lin.params[1]),
        "piecewise_p_value": piece_p,
        "recent_slope_per_year": recent_slope,
        "current_frontier_theta": float(theta[fmask].max()),
        "n_boot_ok": len(trend_draws),
        "share_piecewise_draws": n_piece / len(trend_draws),
        "small_sample_applied": inflate,
        "fit_windows_months": list(cfg.fit_windows_months),
        "fit_window_starts": [
            add_months(as_of, -w)
            for w in cfg.fit_windows_months
            if float(years_since([add_months(as_of, -w)], cfg.fit_start)[0]) in window_ref
        ],
        "share_windowed_draws": n_windowed / len(trend_draws),
        # Saranatesti tehdään vain koko jakson draweissa → osuus niistä
        "share_piecewise_full_draws": (
            n_piece / (len(trend_draws) - n_windowed) if len(trend_draws) > n_windowed else 0.0
        ),
        # Palaiset ikkunadrawit (vain kun koko jakson malli on palainen, A29) / ikkunadrawit
        "share_piecewise_window_draws": n_piece_win / n_windowed if n_windowed else 0.0,
        "horizons": [
            {
                "months": h,
                "date": d,
                "trend_p10_p50_p90": _pct(trend[:, k]),
                "prediction_p10_p50_p90": _pct(pred[:, k]),
            }
            for k, (h, d) in enumerate(zip(cfg.horizons_months, horizons, strict=True))
        ],
        "eval": [
            {
                "date": d,
                "trend_p10_p50_p90": _pct(trend[:, len(horizons) + k]),
                "prediction_p10_p50_p90": _pct(pred[:, len(horizons) + k]),
            }
            for k, d in enumerate(eval_dates)
        ],
    }


# --- Kustannusennuste ------------------------------------------------------------------


@dataclass
class _PriceGrid:
    """Hintamatriisi P[kuukausi, malli]; inf = ei vielä julkaistu tai ei voimassa olevaa hintaa."""

    months: list[date]
    ids: list[str]
    P: np.ndarray

    @classmethod
    def build(cls, prices: pl.DataFrame, release: dict[str, date], ids: list[str]) -> _PriceGrid:
        months = sorted(prices["at"].unique().to_list())
        mi = {m: k for k, m in enumerate(months)}
        ii = {x: k for k, x in enumerate(ids)}
        P = np.full((len(months), len(ids)), np.inf)
        for at, mid, c in prices.select("at", "model_id", "blended").iter_rows():
            if mid in ii and c is not None and release[mid] <= at:
                P[mi[at], ii[mid]] = c
        return cls(months, ids, P)

    def cheapest(self, qualified: np.ndarray) -> list[tuple[date, float, str]]:
        """Kuukausittain halvin (kuukausi, hinta, malli) tason ylittävistä malleista."""
        sub = np.where(qualified[None, :], self.P, np.inf)
        k = sub.argmin(axis=1)
        best = sub[np.arange(len(self.months)), k]
        return [(self.months[r], float(best[r]), self.ids[k[r]])
                for r in range(len(self.months)) if np.isfinite(best[r])]  # fmt: skip


def _frontier_slope_point(scores: pl.DataFrame, fit_start: date) -> float | None:
    """Rintaman lineaarinen kulmakerroin θ/v (piste-estimaatti, ei bootstrapia; A26)."""
    rel = scores["release_date"].to_numpy().astype("datetime64[D]")
    th = scores["theta"].to_numpy().astype(float)
    fm = frontier_mask(rel, th) & (rel >= np.datetime64(fit_start, "D"))
    if fm.sum() < 3:
        return None
    t = years_since(rel[fm], fit_start)
    if np.unique(t).size < 2:
        return None
    return float(np.polyfit(t, th[fm], 1)[0])


def _record_lows(series: list[tuple[date, float, str]]) -> list[tuple[date, float, str]]:
    out, best = [], math.inf
    for m, c, mid in series:
        if c < best:
            out.append((m, c, mid))
            best = c
    return out


def cost_forecast(
    scores: pl.DataFrame,
    prices: pl.DataFrame,
    target: float,
    cfg: ForecastConfig,
    as_of: date,
    frontier_slope: float | None = None,
) -> dict:
    """prices: Repository.get_price_at_dates(month_starts(...)) → at, model_id, blended.

    frontier_slope: rintaman kehitysvauhti θ/v A26:n lähitason lattiaan. Anna kyvykkyysennusteen
    slope_per_year (A29), jotta molemmat ennusteet olettavat saman vauhdin; None → koko jakson
    OLS samasta aineistosta (varakeino, esim. testit)."""
    release = dict(scores.select("model_id", "release_date").iter_rows())
    priced = set(prices["model_id"]) & set(release)
    sc = scores.filter(pl.col("model_id").is_in(list(priced)))
    ids = sc["model_id"].to_list()
    theta = sc["theta"].to_numpy().astype(float)
    se = sc["theta_se"].to_numpy().astype(float)
    grid = _PriceGrid.build(prices, release, ids)

    knot_date = add_months(as_of, -cfg.piecewise_window_months)
    knot = (knot_date - cfg.fit_start).days / DAYS_PER_MONTH

    def t_of(m: date) -> float:
        return (m - cfg.fit_start).days / DAYS_PER_MONTH

    def fit_points(series):
        """Hintaennätykset + porrasfunktion arvo saranakuukautena ja viimeisenä kuukautena.
        Kaksi jälkimmäistä ovat sensuroituja havaintoja ("halvin hinta oli tuolloin tämä");
        ilman niitä pysähtynyt lasku (lattia) ei näkyisi sovituksessa lainkaan (A16)."""
        pts = {m: (m, c, mid) for m, c, mid in _record_lows(series)}
        before_knot = [s for s in series if s[0] <= knot_date]
        for extra in ([before_knot[-1]] if before_knot else []) + ([series[-1]] if series else []):
            pts.setdefault(extra[0], extra)
        return sorted(pts.values())

    def lstsq(points, use_knot: bool):
        t = np.array([t_of(m) for m, _, _ in points])
        y = np.log([c for _, c, _ in points])
        X = _design(t, knot if use_knot else None)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        return beta

    def current_alpha(beta, use_knot: bool) -> float:
        return float(-(beta[1] + (beta[2] if use_knot else 0.0)))  # positiivinen = halpenee

    # Markkinan lattia (päätös 2026-11-14): halvin hinta millä tahansa tasolla viimeisenä
    # kuukautena. Ennusteet rajataan alhaalta tähän; ylhäältä nykyiseen halvimpaan hintaan,
    # koska kerran saatavilla oleva hinta ei nouse (halvin hinta on ei-kasvava).
    # A26 (päätös 2026-11-16, korjattu katselmoinnin jälkeen): HORISONTTIKOHTAINEN lattia.
    # Kyvykkyys nousee β θ/v, joten h kk:n päästä tason kyvykkyys maksaa suunnilleen sen, mitä
    # β·h/12 θ heikompi malli maksaa nyt. Lattia_h = halvin malli, jolle
    # taso − β·h/12 ≤ θ < taso, jos se on tason nykyistä halvinta halvempi; muuten koko
    # markkina. Kiinteä δ oli pitkillä horisonteilla liian tiukka ja jäädytti ennusteen.
    # Tason ylittävät mallit eivät kuulu joukkoon (tason oma halvin olisi aina lattia).
    # β = rintaman pistekohtainen kulmakerroin SAMASTA aineistosta (backtestissä ei vuotoa).
    # Tila at_floor ja aika lattiaan lasketaan aina koko markkinan lattiasta.
    last_month = prices["at"].max()
    last = prices.filter(pl.col("at") == last_month)
    market_floor = float(last["blended"].min())
    current_now = grid.cheapest(theta >= target)
    slope, slope_source = None, None
    if cfg.level_floor:
        if frontier_slope is not None:
            slope, slope_source = frontier_slope, "capability_median"
        else:
            slope = _frontier_slope_point(scores, cfg.fit_start)
            slope_source = "ols_fallback" if slope is not None else None
    horizon_floors: list[dict] = []
    for h in cfg.horizons_months:
        entry = {"floor": market_floor, "floor_scope": "market", "floor_delta_theta": None}
        if slope is not None and slope > 0 and current_now:
            delta = slope * h / 12
            near = [m for m, t in zip(ids, theta, strict=True) if target - delta <= t < target]
            lp = last.filter(pl.col("model_id").is_in(near))["blended"].drop_nulls()
            entry["floor_delta_theta"] = delta
            # Vain SELVÄSTI halvempi (< nykyinen / 1.5): lähes saman hintainen lähimalli ei
            # kerro, mihin hinta voi pudota, ja jäädyttäisi ennusteen (katselmointi: 2 %:n
            # hintaero muutti 24 kk:n ennusteen 4.3-kertaiseksi).
            if lp.len() and float(lp.min()) < current_now[-1][1] / FLOOR_TOLERANCE:
                entry |= {"floor": float(lp.min()), "floor_scope": "level"}
        horizon_floors.append(entry)
    floors = np.array([e["floor"] for e in horizon_floors]) if horizon_floors else np.empty(0)

    point_series = grid.cheapest(theta >= target)
    lows = _record_lows(point_series)
    pts = fit_points(point_series)
    base = {
        "target_theta": target,
        "as_of": as_of,
        "monthly_cheapest": [
            {"month": m, "cost": c, "model_id": mid} for m, c, mid in point_series
        ],
        "record_lows": [{"month": m, "cost": c, "model_id": mid} for m, c, mid in lows],
        "fit_points": [{"month": m, "cost": c, "model_id": mid} for m, c, mid in pts],
    }
    if len(lows) < 3 or len(pts) < 4:
        return base | {"status": "insufficient", "halving_months": None,
                       "note": f"vain {len(lows)} hintaennätystä (min 3)"}  # fmt: skip

    # Palaisuus/lattiatesti: muuttuiko laskuvauhti viimeisen ikkunan aikana?
    t_pts = np.array([t_of(m) for m, _, _ in pts])
    y_pts = np.log([c for _, c, _ in pts])
    lin = _ols(t_pts, y_pts, None)
    hist_alpha = float(-lin.params[1])
    piece_p, recent_alpha, use_piece = None, None, False
    if (t_pts > knot).sum() >= 1 and len(pts) >= 5:
        pw = _ols(t_pts, y_pts, knot)
        piece_p = float(pw.pvalues[2]) if np.isfinite(pw.pvalues[2]) else None
        recent_alpha = float(-(pw.params[1] + pw.params[2]))
        use_piece = piece_p is not None and piece_p < cfg.piecewise_alpha
    a_now = recent_alpha if use_piece else hist_alpha

    rng = np.random.default_rng(cfg.seed)
    alphas, horizon_costs, horizon_raw = [], [], []
    h_t = np.array([t_of(add_months(as_of, h)) for h in cfg.horizons_months])
    for _ in range(cfg.n_boot):
        th = theta + rng.normal(0.0, 1.0, len(theta)) * se
        pb = fit_points(grid.cheapest(th >= target))
        if len(pb) < 4:
            continue
        pick = [pb[k] for k in rng.integers(0, len(pb), len(pb))]
        tb = np.array([t_of(m) for m, _, _ in pick])
        if np.unique(tb).size < 3 or (use_piece and (tb > knot).sum() < 1):
            continue
        beta = lstsq(pick, use_piece)
        alphas.append(current_alpha(beta, use_piece))
        raw = np.exp(_design(h_t, knot if use_piece else None) @ beta)
        horizon_raw.append(raw)
        horizon_costs.append(np.clip(raw, floors, np.maximum(pb[-1][1], floors)))
    alphas_arr = np.array(alphas)
    pos = alphas_arr > 0
    # Ei-laskeva draw → T½ = +inf; persentiilit kaikista drawista (katselmointi K5).
    halving = np.full(alphas_arr.shape, np.inf)
    halving[pos] = math.log(2) / alphas_arr[pos]
    hc = np.array(horizon_costs) if horizon_costs else np.empty((0, len(h_t)))
    hr = np.array(horizon_raw) if horizon_raw else np.empty((0, len(h_t)))

    def halving_of(a):
        return float(math.log(2) / a) if a is not None and a > 0 else None

    current = point_series[-1][1]
    at_floor = current <= FLOOR_TOLERANCE * market_floor
    if at_floor:
        status = "at_floor"
    elif a_now > 0:
        status = "ok"
    else:
        status = "plateau" if use_piece and hist_alpha > 0 else "not_declining"
    months_to_floor = (
        float(math.log(current / market_floor) / a_now) if a_now > 0 and not at_floor else None
    )
    return base | {
        "status": status,
        "market_floor": market_floor,  # koko markkina: at_floor ja aika lattiaan
        "level_floor": cfg.level_floor,
        "frontier_slope_per_year": slope,  # A26: lähitason etäisyys δ_h = slope · h / 12
        "frontier_slope_source": slope_source,  # A29: capability_median | ols_fallback
        "at_floor": at_floor,
        "months_to_floor": months_to_floor,
        "model": "piecewise" if use_piece else "linear",
        "piecewise_p_value": piece_p,
        "alpha1_per_month": a_now,
        "halving_months": halving_of(a_now),
        "historical_halving_months": halving_of(hist_alpha),
        "recent_halving_months": halving_of(recent_alpha),
        "halving_months_p10_p50_p90": _pct_unbounded(halving),
        "share_not_declining": float(np.mean(~pos)) if alphas else None,
        "n_boot_ok": len(alphas),
        "current_cheapest": base["monthly_cheapest"][-1] if point_series else None,
        "horizons": [
            {
                "months": h,
                "cost_p10_p50_p90": _pct(hc[:, k]) if hc.size else [None] * 3,
                # Regressio ilman rajausta: näyttää, kuinka paljon tuloksesta on rajauksen ansiota
                "unclipped_cost_p10_p50_p90": _pct(hr[:, k]) if hr.size else [None] * 3,
                **horizon_floors[k],  # floor, floor_scope (level/market), floor_delta_theta
            }
            for k, h in enumerate(cfg.horizons_months)
        ],
    }


# --- Backtest -------------------------------------------------------------------------


def backtest_capability(
    scores: pl.DataFrame,
    cfg: ForecastConfig,
    as_of: date,
    holdout_months: int = 12,
    cutoff: date | None = None,
) -> dict:
    """Sovitus vain cutoffiin asti julkaistuilla malleilla → ennuste jokaiselle cutoffin
    jälkeen toteutuneelle rintamapisteelle SEN OMALLA julkaisupäivällä.

    (Vertailu "viimeisin ennätys vs. trendi nykyhetkellä" olisi harhainen: rintama on
    porrasfunktio, joten viimeisin ennätys voi olla kuukausia vanha.)
    Mittarit: osuus pisteistä ennustevälin P10–P90 sisällä (odotus ≈ 0.8), keskivirhe.
    cutoff: anna suoraan (liukuva backtest), jotta kuun lopun päiviä ei ketjuteta.
    """
    cutoff = cutoff or add_months(as_of, -holdout_months)
    rel = scores["release_date"].to_numpy().astype("datetime64[D]")
    theta = scores["theta"].to_numpy().astype(float)
    fmask = frontier_mask(rel, theta)  # todellinen ennätys koko datasta
    after = fmask & (rel > np.datetime64(cutoff, "D")) & (rel <= np.datetime64(as_of, "D"))
    realized = scores.filter(pl.Series(after)).select("model_id", "release_date", "theta")

    train = scores.filter(pl.col("release_date") <= cutoff)
    bt_cfg = replace(cfg, horizons_months=(holdout_months,), cost_targets={})
    fc = capability_forecast(train, bt_cfg, cutoff, eval_dates=realized["release_date"].to_list())

    points, errs, inside = [], [], []
    for (mid, d, th), ev in zip(realized.iter_rows(), fc["eval"], strict=True):
        p10, p50, p90 = ev["prediction_p10_p50_p90"]
        errs.append(th - p50)
        inside.append(p10 <= th <= p90)
        points.append({"model_id": mid, "date": d, "theta": th,
                       "prediction_p10_p50_p90": [p10, p50, p90]})  # fmt: skip
    return {
        "cutoff": cutoff,
        "as_of": as_of,
        "trained_model": fc["model"],
        "trained_slope_per_year": fc["slope_per_year"],
        "trained_n_frontier": fc["n_frontier"],
        "small_sample_applied": fc["small_sample_applied"],
        "n_realized_frontier_points": len(points),
        "share_inside_p10_p90": float(np.mean(inside)) if inside else None,
        "mean_error": float(np.mean(errs)) if errs else None,
        "points": points,
    }


def backtest_capability_rolling(
    scores: pl.DataFrame,
    cfg: ForecastConfig,
    as_of: date,
    origins_months: Sequence[int] = (6, 12, 18, 24),
    holdout_months: int = 12,
) -> dict:
    """Liukuva backtest (A24): katkaisu as_of − k kk jokaiselle k:lle, ennuste arvioidaan
    katkaisun jälkeisille rintamapisteille enintään holdout_months eteenpäin (≤ as_of).

    Yhden katkaisun backtest on käytännössä yksi havainto: saman lähtöpäivän virheet ovat
    samanmerkkisiä (yhteinen trendivirhe), joten 9/9 osumaa voi syntyä sattumalta.
    Mittarit:
      pooled_share_inside_p10_p90 – osuus KAIKISTA arvioiduista pisteistä (piste voi esiintyä
        useammalla lähtöpäivällä; ei riippumattomia)
      mean_origin_share – lähtöpäivien osumaosuuksien keskiarvo (jokainen lähtöpäivä samalla
        painolla; robustimpi, kun pisteitä on lähtöpäivittäin eri määrä)
      origin_share_min_max – vaihteluväli: kertoo, onko kalibraatio lähtöpäivästä riippuva
    Odotus hyvin kalibroidulla mallilla: ≈ 0.8.
    """
    origins: list[dict] = []
    for k in sorted(set(origins_months)):
        cutoff = add_months(as_of, -k)
        horizon = min(k, holdout_months)
        # Loppu suoraan as_ofista: add_months(add_months(x, -k), k) ei palaa kuun lopun
        # päiviin (31.3. − 6 kk = 30.9. → + 6 kk = 30.3.) ja jättäisi as_of-päivän pois.
        end = as_of if k <= holdout_months else add_months(as_of, holdout_months - k)
        entry = {"origin_months": k, "cutoff": cutoff, "end": end}
        try:
            bt = backtest_capability(scores, cfg, end, horizon, cutoff=cutoff)
        except ValueError as exc:  # liian vähän rintamapisteitä ennen katkaisua
            origins.append(entry | {"status": "insufficient", "note": str(exc)})
            continue
        if not bt["n_realized_frontier_points"]:
            origins.append(entry | {"status": "no_points"})
            continue
        origins.append(entry | {
            "status": "ok",
            "trained_model": bt["trained_model"],
            "trained_slope_per_year": bt["trained_slope_per_year"],
            "trained_n_frontier": bt["trained_n_frontier"],
            "small_sample_applied": bt["small_sample_applied"],
            "n_points": bt["n_realized_frontier_points"],
            "share_inside_p10_p90": bt["share_inside_p10_p90"],
            "mean_error": bt["mean_error"],
            "points": bt["points"],
        })  # fmt: skip
    ok = [o for o in origins if o["status"] == "ok"]
    pts = [p for o in ok for p in o["points"]]
    inside = [p["prediction_p10_p50_p90"][0] <= p["theta"] <= p["prediction_p10_p50_p90"][2]
              for p in pts]  # fmt: skip
    errs = [p["theta"] - p["prediction_p10_p50_p90"][1] for p in pts]
    shares = [o["share_inside_p10_p90"] for o in ok]
    # Epävarmuus (katselmointi): lähtöpäivät ovat klustereita (yhteinen trendivirhe), joten
    # pistemäärä ei ole efektiivinen n. Lähtöpäivätason SE on alaraja epävarmuudelle, koska
    # lähtöpäivätkin korreloivat (päällekkäiset koulutusaineistot).
    share_se = (float(np.std(shares, ddof=1) / math.sqrt(len(shares)))
                if len(shares) > 1 else None)  # fmt: skip
    return {
        "as_of": as_of,
        "holdout_months": holdout_months,
        "n_origins_ok": len(ok),
        "n_points": len(pts),
        "n_unique_points": len({p["model_id"] for p in pts}),
        "mean_origin_share_se": share_se,
        "pooled_share_inside_p10_p90": float(np.mean(inside)) if inside else None,
        "mean_origin_share": float(np.mean(shares)) if shares else None,
        "origin_share_min_max": [float(min(shares)), float(max(shares))] if shares else None,
        "mean_error": float(np.mean(errs)) if errs else None,
        "origins": origins,
    }


def backtest_cost(
    scores: pl.DataFrame,
    prices: pl.DataFrame,
    target: float,
    cfg: ForecastConfig,
    as_of: date,
    holdout_months: int = 12,
) -> dict:
    cutoff = add_months(as_of, -holdout_months)
    train_prices = prices.filter(pl.col("at") <= cutoff)
    train_scores = scores.filter(pl.col("release_date") <= cutoff)
    bt_cfg = ForecastConfig(fit_start=cfg.fit_start, horizons_months=(holdout_months,),
                            n_boot=cfg.n_boot, seed=cfg.seed,
                            piecewise_window_months=cfg.piecewise_window_months,
                            piecewise_alpha=cfg.piecewise_alpha,
                            min_points=cfg.min_points,
                            level_floor=cfg.level_floor)  # fmt: skip
    # A29: lattian kehitysvauhti samasta kyvykkyysmallista, vain cutoffiin asti (ei vuotoa)
    train_slope = None
    if cfg.level_floor:
        try:
            train_slope = capability_forecast(
                train_scores, replace(cfg, horizons_months=(holdout_months,), cost_targets={}),
                cutoff,
            )["slope_per_year"]  # fmt: skip
        except ValueError:
            train_slope = None  # liian vähän rintamapisteitä → OLS-varakeino
    fc = cost_forecast(train_scores, train_prices, target, bt_cfg, cutoff, train_slope)
    full = cost_forecast(scores, prices, target, ForecastConfig(
        fit_start=cfg.fit_start, horizons_months=(0,), n_boot=0, seed=cfg.seed), as_of)  # fmt: skip
    realized = full["monthly_cheapest"][-1]["cost"] if full["monthly_cheapest"] else None
    if fc.get("status") == "insufficient" or realized is None:
        return {"cutoff": cutoff, "status": fc.get("status", "insufficient"),
                "realized_cost_at_as_of": realized}  # fmt: skip
    p10, p50, p90 = fc["horizons"][0]["cost_p10_p50_p90"]
    u50 = fc["horizons"][0]["unclipped_cost_p10_p50_p90"][1]
    h0 = fc["horizons"][0]
    floor = h0["floor"]  # A26: tämän horisontin alaraja (lähitaso tai markkina)
    return {
        "cutoff": cutoff,
        "status": "ok",
        "trained_status": fc["status"],
        "trained_model": fc["model"],
        "trained_halving_months": fc["halving_months"],
        "trained_frontier_slope": fc.get("frontier_slope_per_year"),
        "trained_frontier_slope_source": fc.get("frontier_slope_source"),
        "predicted_cost_at_as_of_p10_p50_p90": [p10, p50, p90],
        "realized_cost_at_as_of": realized,
        "log_error": math.log(realized / p50) if p50 else None,
        # Regression oma osumatarkkuus ilman lattia-/kattorajausta (katselmointi K5): jos
        # p50 osuu rajalle, log_error mittaa rajausta eikä trendimallia.
        "unclipped_log_error": math.log(realized / u50) if u50 else None,
        "p50_at_floor": p50 is not None and math.isclose(p50, floor, rel_tol=1e-9),
        "trained_floor": floor,
        "trained_floor_scope": h0["floor_scope"],
        # Katselmointi A26: alittiko toteuma koulutetun alarajan? (raja liian tiukka)
        "realized_below_floor": realized < floor * (1 - 1e-9),
        "inside_p10_p90": (p10 <= realized <= p90) if p10 is not None else None,
    }
