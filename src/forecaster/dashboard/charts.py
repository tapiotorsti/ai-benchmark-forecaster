"""Plotly-kuvaajat. Värit dataviz-referenssipaletista roolin mukaan:
rintama/pääsarja = kategorinen 1 (sininen), muut mallit = vaimea harmaa,
ennustekaista = sininen läpikuultava, poikkeama = tilaväri "critical" (aina selitteen kanssa).
Teema (vaalea/tumma) tulee Streamlitista (`st.plotly_chart(theme="streamlit")`).
"""

from __future__ import annotations

from datetime import date

import plotly.graph_objects as go
import polars as pl

BLUE = "#2a78d6"
ORANGE = "#eb6834"
MUTED = "#898781"
CRITICAL = "#d03b3b"
BAND_TREND = "rgba(42,120,214,0.30)"
BAND_PRED = "rgba(42,120,214,0.12)"

_LAYOUT = dict(
    margin=dict(l=8, r=8, t=40, b=8),
    hovermode="closest",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
)


def _band(fig: go.Figure, x, lo, hi, color: str, name: str) -> None:
    fig.add_trace(
        go.Scatter(x=x, y=hi, mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip")
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=lo,
            mode="lines",
            line=dict(width=0),
            fill="tonexty",
            fillcolor=color,
            name=name,
            hoverinfo="skip",
        )
    )


def overview(scores: pl.DataFrame, band: pl.DataFrame | None, as_of: date) -> go.Figure:
    """θ ajan funktiona: kaikki mallit, Pareto-rintama korostettuna, ennustekaistat."""
    fig = go.Figure()
    if band is not None and band.height:
        x = band["date"].to_list()
        _band(fig, x, band["pred_p10"], band["pred_p90"], BAND_PRED, "Ennusteväli P10–P90")
        _band(fig, x, band["trend_p10"], band["trend_p90"], BAND_TREND, "Trendin väli P10–P90")
        fig.add_trace(
            go.Scatter(
                x=x,
                y=band["trend_p50"],
                mode="lines",
                name="Trendi (P50)",
                line=dict(color=BLUE, width=2, dash="dash"),
                hovertemplate="%{x|%Y-%m}: θ %{y:.2f}<extra>trendi</extra>",
            )
        )
    for is_f, name, color, size in (
        (False, "Muut mallit", MUTED, 7),
        (True, "Pareto-rintama", BLUE, 10),
    ):
        sub = scores.filter(pl.col("is_frontier") == is_f)
        if sub.is_empty():
            continue
        fig.add_trace(
            go.Scatter(
                x=sub["release_date"].to_list(),
                y=sub["theta"],
                mode="markers",
                name=name,
                marker=dict(
                    color=color,
                    size=size,
                    line=dict(width=1, color="white"),
                    opacity=1.0 if is_f else 0.6,
                ),
                error_y=dict(
                    type="data",
                    array=sub["theta_se"].fill_null(0).to_list(),
                    visible=is_f,
                    thickness=1,
                    color=color,
                ),
                customdata=sub.select("display_name", "theta_se").rows(),
                hovertemplate="<b>%{customdata[0]}</b><br>%{x|%Y-%m-%d}<br>"
                "θ %{y:.2f} ± %{customdata[1]:.2f}<extra></extra>",
            )
        )
    fig.add_vline(x=as_of, line=dict(color=MUTED, width=1, dash="dot"))
    fig.update_layout(**_LAYOUT, xaxis_title="Julkaisupäivä", yaxis_title="Kyvykkyys θ", height=520)
    return fig


def model_detail(detail: pl.DataFrame, title: str) -> go.Figure:
    """Havaittu vs. ennustettu normalisoitu tulos benchmarkeittain (vaikeusjärjestyksessä)."""
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=detail["name"],
            y=detail["y_pred"],
            mode="markers",
            name="Ennustettu (IRT)",
            marker=dict(symbol="line-ew-open", size=14, color=BLUE, line=dict(width=2, color=BLUE)),
            hovertemplate="%{x}<br>ennuste %{y:.2f}<extra></extra>",
        )
    )
    seen = detail.filter(pl.col("y_obs").is_not_null())
    for flag, name, color in (
        (False, "Havaittu", ORANGE),
        (True, "Havaittu – poikkeama, poistettu sovituksesta", CRITICAL),
    ):
        sub = seen.filter(pl.col("is_outlier") == flag)
        if sub.height:
            fig.add_trace(
                go.Scatter(
                    x=sub["name"],
                    y=sub["y_obs"],
                    mode="markers",
                    name=name,
                    marker=dict(size=9, color=color, symbol="x" if flag else "circle"),
                    hovertemplate="%{x}<br>havaittu %{y:.2f}<extra></extra>",
                )
            )
    fig.update_layout(
        **_LAYOUT,
        title=title,
        yaxis_title="Normalisoitu tulos",
        yaxis_range=[0, 1.02],
        height=460,
        xaxis_tickangle=-45,
    )
    return fig


def benchmarks(params: pl.DataFrame) -> go.Figure:
    """Vaikeus b vs. erottelukyky a; pisteen koko = kattavuus, väri = saturaatio."""
    fig = go.Figure(
        go.Scatter(
            x=params["difficulty_b"],
            y=params["discrimination_a"],
            mode="markers",
            marker=dict(
                size=(params["n_models"].cast(float).sqrt() * 2.5 + 4).to_list(),
                color=params["saturation"],
                colorscale=[[0, "#cde2fb"], [1, "#104281"]],
                cmin=0,
                cmax=1,
                colorbar=dict(title="Saturaatio"),
                line=dict(width=1, color="white"),
            ),
            customdata=params.select("name", "domain", "n_models", "saturation").rows(),
            hovertemplate="<b>%{customdata[0]}</b> (%{customdata[1]})<br>b %{x:.2f}, a %{y:.2f}"
            "<br>malleja %{customdata[2]}, saturaatio %{customdata[3]:.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        **_LAYOUT, xaxis_title="Vaikeus b (θ-asteikko)", yaxis_title="Erottelukyky a", height=480
    )
    return fig


def cost(
    monthly: pl.DataFrame, horizons: pl.DataFrame, floor: float | None, title: str
) -> go.Figure:
    """Iso-kyvykkyyskäyrä: halvin hinta tason ylittäville malleille (log-asteikko) + ennuste."""
    fig = go.Figure()
    if monthly.height:
        fig.add_trace(
            go.Scatter(
                x=monthly["month"],
                y=monthly["cost"],
                mode="lines",
                line=dict(shape="hv", color=BLUE, width=2),
                name="Halvin tason ylittävä malli",
                customdata=monthly.select("model_id").rows(),
                hovertemplate="%{x|%Y-%m}: %{y:.3g} $/1M<br>%{customdata[0]}<extra></extra>",
            )
        )
    if horizons.height:
        x = (
            [monthly["month"][-1], *horizons["date"].to_list()]
            if monthly.height
            else horizons["date"].to_list()
        )
        last = [monthly["cost"][-1]] if monthly.height else []
        _band(
            fig,
            x,
            last + horizons["p10"].to_list(),
            last + horizons["p90"].to_list(),
            BAND_TREND,
            "Ennuste P10–P90",
        )
        fig.add_trace(
            go.Scatter(
                x=x,
                y=last + horizons["p50"].to_list(),
                mode="lines+markers",
                name="Ennuste (P50)",
                line=dict(color=BLUE, width=2, dash="dash"),
                hovertemplate="%{x|%Y-%m}: %{y:.3g} $/1M<extra>ennuste</extra>",
            )
        )
    if floor:
        fig.add_hline(
            y=floor,
            line=dict(color=MUTED, width=1, dash="dot"),
            annotation_text=f"markkinan lattia {floor:.3g} $",
            annotation_position="bottom right",
        )
    fig.update_layout(
        **_LAYOUT,
        title=title,
        yaxis_type="log",
        yaxis_title="USD / 1M tokenia (blended, log)",
        height=480,
    )
    return fig


def sim_delta(cmp: pl.DataFrame, top: int = 30) -> go.Figure:
    """Δθ (simuloitu − tallennettu) kärkimalleille, vaakapalkit."""
    sub = cmp.filter(pl.col("theta_sim").is_not_null()).head(top).reverse()
    colors = [BLUE if d >= 0 else ORANGE for d in sub["delta"]]
    fig = go.Figure(
        go.Bar(
            x=sub["delta"],
            y=sub["display_name"],
            orientation="h",
            marker=dict(color=colors),
            hovertemplate="%{y}: Δθ %{x:+.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        **_LAYOUT, xaxis_title="Δθ (simuloitu − tallennettu)", height=max(320, 18 * sub.height + 80)
    )
    return fig
