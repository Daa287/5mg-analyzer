#!/usr/bin/env python3
"""
season_backtest.py — 5MG Backtest, Saison-Score mit cutoff_date statt "jetzt".

NUR für die Backtest-Orchestrierung. season_engine.py bleibt unverändert
(dessen season_score() nutzt datetime.now() + einen frischen
yf.Ticker().history()-Live-Abruf - beides für einen historischen
Backtest ungeeignet). Diese Datei implementiert DIESELBE Formel
(Fenster-Rendite, Konsistenz, Score-Normierung - 1:1 aus season_engine.py
übernommen, keine eigene Interpretation), aber:
  - "heute" = uebergebenes cutoff_date statt datetime.now()
  - Kursdaten kommen aus dem bereits geladenen price_history-Cache
    (nur Eintraege <= cutoff_date werden verwendet - Lookahead-sicher)

⚠️ Bekannte Einschraenkung ggue. dem Live-System: season_engine.py nutzt
bis zu HISTORY_YEARS=12 Jahre Kurshistorie fuer die Saison-Fenster-Suche.
Der Backtest-Preis-Cache (price_history.py) startet erst 2022-01-01 -
fuer die fruehesten Signale des 3-Jahres-Backtests (~Sep 2023) stehen
dadurch nur ca. 1,5-2 Jahre Saison-Vergleichshistorie zur Verfuegung
statt bis zu 12 - schwaechere statistische Grundlage fuer die
Saison-Komponente bei fruehen Backtest-Wochen als im Live-Betrieb.

    python3 season_backtest.py   # Selbsttest gegen eine feste Stichprobe
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

WINDOW_WEEKS = 4  # identisch zu season_engine.WINDOW_WEEKS


def _fenster_rendite(closes: list[float], start_idx: int, tage: int) -> float | None:
    """1:1 aus season_engine._fenster_rendite() uebernommen."""
    if start_idx + tage >= len(closes):
        return None
    start = closes[start_idx]
    end = closes[start_idx + tage]
    if start == 0:
        return None
    return (end - start) / start


def season_score_at(pair: str, richtung: str, cutoff_date: date,
                     price_series: list[tuple[str, float]],
                     window_weeks: int = WINDOW_WEEKS) -> dict:
    """1:1 dieselbe Formel wie season_engine.season_score(), aber mit
    simuliertem "heute" (cutoff_date) statt datetime.now(), und auf
    bereits geladenen (<=cutoff_date gefilterten) Kursdaten statt einem
    frischen yf.Ticker()-Abruf."""
    if not price_series:
        return {"score": 50.0, "label": "NEUTRAL", "hist_text": "keine Kursdaten verfügbar"}

    cutoff_str = cutoff_date.isoformat()
    filtered = [(d, c) for d, c in price_series if d <= cutoff_str]
    if not filtered:
        return {"score": 50.0, "label": "NEUTRAL", "hist_text": "keine Kursdaten vor Cutoff"}

    dates = [datetime.strptime(d, "%Y-%m-%d") for d, _ in filtered]
    closes = [c for _, c in filtered]

    tage = window_weeks * 7
    heute_monat_tag = (cutoff_date.month, cutoff_date.day)

    renditen = []
    for i, d in enumerate(dates[:-tage] if tage < len(dates) else []):
        if (d.month, d.day) == heute_monat_tag or (
            d.month == cutoff_date.month and abs(d.day - cutoff_date.day) <= 3
        ):
            r = _fenster_rendite(closes, i, tage)
            if r is not None:
                renditen.append(r)

    if not renditen:
        return {"score": 50.0, "label": "NEUTRAL", "hist_text": "historisch unklar"}

    mean_r = sum(renditen) / len(renditen)
    konsistenz = sum(1 for r in renditen if (r > 0) == (mean_r > 0)) / len(renditen)

    bias_positiv = mean_r > 0 if richtung == "LONG" else mean_r < 0

    staerke = min(abs(mean_r) * 8, 1.0) * konsistenz
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

    return {"score": score, "label": label, "hist_text": hist_text, "n_fenster": len(renditen)}


if __name__ == "__main__":
    import json
    from pathlib import Path

    cache = json.loads((Path.home() / "hermes2" / "data" / "backtest_price_history_cache.json").read_text())
    series = cache.get("USD/CHF", [])
    r = season_score_at("USD/CHF", "SHORT", date(2024, 1, 15), series)
    print("USD/CHF SHORT @2024-01-15:", r)
