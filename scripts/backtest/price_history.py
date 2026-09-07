#!/usr/bin/env python3
"""
price_history.py — 5MG Backtest, gecachte yfinance-Tageskurse aller 8 Paare.

NUR für die Backtest-Orchestrierung. Lädt jedes Paar EINMAL komplett
(2022-heute) und cached lokal als JSON - vermeidet wiederholte
yfinance-Abrufe bei mehreren Testläufen, dient sowohl als Input für
den Backtest-Saison-Score (season_backtest.py) als auch für die
5/14/28-Tage-Ergebnis-Auswertung nach jedem simulierten Signal.

    python3 price_history.py --refresh   # einmalig alle 8 Paare laden + cachen
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

CACHE_PATH = Path.home() / "hermes2" / "data" / "backtest_price_history_cache.json"

YF_TICKERS = {
    "USD/CHF": "USDCHF=X", "USD/CAD": "USDCAD=X", "NZD/USD": "NZDUSD=X",
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "AUD/USD": "AUDUSD=X", "USD/MXN": "USDMXN=X",
}

START_DATE = "2022-01-01"
END_DATE = date.today().isoformat()


def fetch_all() -> dict:
    out = {}
    for pair, ticker in YF_TICKERS.items():
        df = yf.download(ticker, start=START_DATE, end=END_DATE, interval="1d",
                          progress=False, auto_adjust=True, timeout=30)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df is None or df.empty:
            print(f"  {pair} ({ticker}): KEINE DATEN")
            out[pair] = []
            continue
        series = [(idx.strftime("%Y-%m-%d"), float(row["Close"])) for idx, row in df.iterrows()]
        out[pair] = series
        print(f"  {pair} ({ticker}): {len(series)} Zeilen, {series[0][0]} bis {series[-1][0]}")
    return out


def load_cached(refresh: bool = False) -> dict:
    if not refresh and CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    data = fetch_all()
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nGeschrieben: {CACHE_PATH}")
    return data


def close_am(series: list[tuple[str, float]], cutoff_date: date) -> float | None:
    """Letzter bekannter Schlusskurs MIT Datum <= cutoff_date."""
    cutoff_str = cutoff_date.isoformat()
    best = None
    for d_str, val in series:
        if d_str <= cutoff_str:
            if best is None or d_str > best[0]:
                best = (d_str, val)
    return best[1] if best else None


def close_nach_tagen(series: list[tuple[str, float]], signal_date: date, tage: int) -> float | None:
    """Erster verfuegbarer Schlusskurs AM/NACH signal_date + tage Kalendertage
    (fuer die 5/14/28-Tage-Ergebnisauswertung - bewusst NICHT cutoff-
    beschraenkt, hier wird absichtlich in die Zukunft geschaut)."""
    target = (signal_date.toordinal() + tage)
    target_str = date.fromordinal(target).isoformat()
    for d_str, val in series:
        if d_str >= target_str:
            return val
    return None


if __name__ == "__main__":
    refresh = "--refresh" in sys.argv
    data = load_cached(refresh=refresh)
    print("\nAbdeckung je Paar:")
    for pair, series in data.items():
        if series:
            print(f"  {pair}: {len(series)} Punkte, {series[0][0]} bis {series[-1][0]}")
        else:
            print(f"  {pair}: KEINE DATEN")
