"""
season_engine.py — Saisonalitäts-Score je Setup

Nutzt historische Tagesschlusskurse (Yahoo Finance via yfinance) und prüft,
wie sich das Paar historisch in den nächsten N Wochen ab dem heutigen
Kalendertag entwickelt hat — gemittelt über mehrere Jahre.

Wichtig: Der Score misst NICHT die absolute historische Richtung, sondern
die Übereinstimmung mit dem COT-Bias (Stufe 1). Ein Paar mit SHORT-Bias,
das historisch in diesem Fenster meist fällt, bekommt einen hohen Score
("LEICHT_POSITIV" = die Saison stützt den Trade).

Muss auf hermes2 installiert werden:
    pip install yfinance --break-system-packages   (falls noch nicht vorhanden)

Für den Export ist das Fenster 4 Wochen (Default), History 12 Jahre.
"""

from __future__ import annotations
from datetime import datetime, timedelta

try:
    import yfinance as yf
except ImportError:  # Fallback, damit das Modul ohne yfinance importierbar bleibt
    yf = None

YF_TICKERS = {
    "USD/CHF": "USDCHF=X", "USD/CAD": "USDCAD=X", "NZD/USD": "NZDUSD=X",
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "AUD/USD": "AUDUSD=X", "USD/MXN": "USDMXN=X",
    "Gold": "GC=F", "Silber": "SI=F", "Nasdaq 100": "NQ=F", "S&P 500": "ES=F",
}

WINDOW_WEEKS = 4
HISTORY_YEARS = 12


def _fenster_rendite(close_series, start_idx: int, tage: int) -> float | None:
    if start_idx + tage >= len(close_series):
        return None
    start = close_series[start_idx]
    end = close_series[start_idx + tage]
    if start == 0:
        return None
    return (end - start) / start


def season_score(pair: str, richtung: str, window_weeks: int = WINDOW_WEEKS,
                  history_years: int = HISTORY_YEARS) -> dict:
    """
    Liefert {score, label, hist_text}. Score 0-100 relativ zum COT-Bias.
    Fällt bei fehlendem yfinance oder fehlenden Daten auf NEUTRAL (50.0) zurück.
    """
    if yf is None:
        return {"score": 50.0, "label": "NEUTRAL", "hist_text": "keine Saisondaten (yfinance fehlt)"}

    ticker = YF_TICKERS.get(pair)
    if ticker is None:
        return {"score": 50.0, "label": "NEUTRAL", "hist_text": "kein Ticker hinterlegt"}

    hist = yf.Ticker(ticker).history(period=f"{history_years}y", interval="1d")
    if hist.empty:
        return {"score": 50.0, "label": "NEUTRAL", "hist_text": "keine Kursdaten verfügbar"}

    closes = hist["Close"].tolist()
    dates = [d.to_pydatetime() for d in hist.index]
    today = datetime.now()
    tage = window_weeks * 7
    heute_monat_tag = (today.month, today.day)

    renditen = []
    for i, d in enumerate(dates[:-tage]):
        if abs((d.month, d.day) == heute_monat_tag) or (
            d.month == today.month and abs(d.day - today.day) <= 3
        ):
            r = _fenster_rendite(closes, i, tage)
            if r is not None:
                renditen.append(r)

    if not renditen:
        return {"score": 50.0, "label": "NEUTRAL", "hist_text": "historisch unklar"}

    mean_r = sum(renditen) / len(renditen)
    konsistenz = sum(1 for r in renditen if (r > 0) == (mean_r > 0)) / len(renditen)

    # Richtung des Trades: LONG will steigenden Kurs, SHORT will fallenden Kurs
    bias_positiv = mean_r > 0 if richtung == "LONG" else mean_r < 0

    staerke = min(abs(mean_r) * 8, 1.0) * konsistenz  # 0..1 grobe Normierung
    score = 50 + (staerke * 45 if bias_positiv else -staerke * 45)
    score = round(max(0, min(100, score)), 1)

    if score >= 55:
        label = "LEICHT_POSITIV"
    elif score <= 45:
        label = "LEICHT_NEGATIV"
    else:
        label = "NEUTRAL"

    hist_text = ("historisch fallend" if mean_r < 0 else "historisch steigend") \
        if konsistenz >= 0.55 else "historisch unklar"

    return {"score": score, "label": label, "hist_text": hist_text}


if __name__ == "__main__":
    for pair, dirn in [("USD/CHF", "SHORT"), ("USD/CAD", "SHORT"), ("NZD/USD", "LONG")]:
        print(pair, dirn, "->", season_score(pair, dirn))
