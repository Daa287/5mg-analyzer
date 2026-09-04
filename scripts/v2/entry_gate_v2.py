#!/usr/bin/env python3
"""
entry_gate_v2.py — 5MG v2, Phase 2.

Hard-Gate-Prinzip beobachtet aus TradingWelt 5MG Entry Engine UI
(angezeigter Erklärtext, keine Formel-Rekonstruktion). Eigene Skala/
Schwellen, keine Original-Werte übernommen. Entry Engine selbst erzeugt
laut eigener Beschreibung kein Kauf-/Verkaufssignal, keine
Positionsgröße, keinen SL - dieselbe Beschränkung gilt für
entry_gate_v2.py.

EIGENSTÄNDIG: ruft entry_monitor.py NICHT auf, wird von dort NICHT
aufgerufen, importiert entry_signal.py NICHT (eigener Codepfad, damit
v1 und v2 auch bei künftigen Änderungen komplett unabhängig bleiben) -
nutzt aber inhaltlich dieselbe Datenquelle/Methodik (yfinance, EMA/RSI,
Trend-mit-Hysterese), 1:1 aus entry_signal.py in diese Datei kopiert.

Zweistufiges Prinzip (Beobachtung des Original-Konzepts, NICHT dessen
Zahlen/Formel):
  1. Nur Paare mit market_pulse-Einordnung "bestätigt sich"
     (market_pulse_checks_v2, neuester Eintrag pro Paar) werden
     überhaupt geprüft - spart yfinance-Calls für den Rest.
  2. HARD-GATE: H4-Trend UND H1-Struktur müssen BEIDE mit dem Bias
     übereinstimmen. Nicht bestanden -> gate_passed=False, fertig,
     KEINE weitere Berechnung (kein M15-Abruf).
  3. Nur bei bestandenem Hard-Gate: eigene, simple Readiness-Zahl 0-4
     aus VIER Bausteinen - Pullback (M15/EMA20-Nähe) + Momentum
     (M15-RSI) + H4-Trend-STÄRKE (Abstand Kurs/EMA50, eigene Schwelle)
     + H1-Struktur-STÄRKE (analog). Die beiden Stärke-Checks sind
     bewusst NICHT dieselbe Prüfung wie der Hard-Gate-Richtungs-
     abgleich (der ist zu diesem Zeitpunkt bereits bestanden, sonst
     wäre man gar nicht hier) - sie messen zusätzlich die MAGNITUDE
     des Trends, nicht nur seine Richtung.
  4. Eigene Zonen: readiness 0-1 "beobachten", 2-3 "setup_möglich",
     4 "bestätigt" (NICHT die Original-Grenzen 44/45-74/75).

--dry-run ist STANDARD (nur print, kein DB-Write) - Regel aus
HANDOVER.md "Gelernte Regeln" 12.08.2026: die dry-run-Prüfung steht
GANZ AM ANFANG von save_results(), vor jedem einzelnen Write. Für einen
echten Schreib-Lauf: --write explizit übergeben.

    python3 entry_gate_v2.py           # dry-run (Standard), nur print
    python3 entry_gate_v2.py --write   # schreibt wirklich nach entry_readiness_checks_v2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

SCRIPTS_DIR = Path("/home/pi/hermes2/scripts")
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(Path(__file__).parent))
import db_v2  # v2 (market_pulse_checks_v2 lesend, entry_readiness_checks_v2 schreibend)

# =====================================================================
# Eigene, transparent gewählte Konstanten (1:1-Übernahmen aus
# entry_signal.py sind als solche markiert; NEUE v2-Konstanten separat)
# =====================================================================
# --- 1:1 aus entry_signal.py übernommen (gleiche Methodik, eigener Codepfad) ---
H4_EMA_PERIOD = 50
H1_EMA_PERIOD = 50
M15_EMA_PERIOD = 20
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
M15_PULLBACK_TOLERANCE_PCT = 0.0015
TREND_HYSTERESIS_PCT = 0.0005
YF_PERIOD_1H = "60d"
YF_PERIOD_15M = "10d"

# --- NEU in v2 (kein Original-Wert, kein v1-Pendant) ---
# Mindestabstand Preis/EMA50 in %, ab dem ein Trend als "stark" statt nur
# "vorhanden" gilt - eigene, unkalibrierte Startwerte (bislang kein
# Datenpunkt, der eine andere Schwelle für H4 vs. H1 nahelegt).
H4_STRENGTH_THRESHOLD_PCT = 0.003   # 0.3%
H1_STRENGTH_THRESHOLD_PCT = 0.003   # 0.3%

# Eigene Zonen-Grenzen für die Readiness-Zahl (NICHT 44/74 vom Original).
ZONE_BEOBACHTEN_MAX = 1       # readiness 0-1
ZONE_SETUP_MOEGLICH_MAX = 3   # readiness 2-3
# readiness 4 -> "bestätigt"


# --- Datenquelle/Indikatoren, 1:1-Logik aus entry_signal.py -----------------
def _pair_to_ticker(pair: str) -> str:
    return pair.replace("/", "") + "=X"


def _download(ticker: str, period: str, interval: str) -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval=interval,
                      progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _trend_with_hysteresis(closes: pd.Series, emas: pd.Series,
                            buffer_pct: float = TREND_HYSTERESIS_PCT) -> str | None:
    state = None
    for close, ema in zip(closes, emas):
        if pd.isna(ema):
            continue
        if state is None:
            state = "LONG" if close > ema else "SHORT"
            continue
        upper = ema * (1 + buffer_pct)
        lower = ema * (1 - buffer_pct)
        if state == "LONG" and close < lower:
            state = "SHORT"
        elif state == "SHORT" and close > upper:
            state = "LONG"
    return state


def _zone(readiness: int | None) -> str | None:
    if readiness is None:
        return None
    if readiness <= ZONE_BEOBACHTEN_MAX:
        return "beobachten"
    if readiness <= ZONE_SETUP_MOEGLICH_MAX:
        return "setup_möglich"
    return "bestätigt"


# --- v2-spezifische Logik ---------------------------------------------------
def latest_market_pulse() -> list[dict]:
    """Neuester market_pulse_checks_v2-Eintrag PRO PAAR (ein Paar kann
    über mehrere Test-Läufe hinweg mehrfach geloggt sein)."""
    rows = db_v2.query(
        "SELECT mp.pair, mp.bias, mp.einordnung, mp.ts FROM market_pulse_checks_v2 mp "
        "INNER JOIN (SELECT pair, MAX(ts) mts FROM market_pulse_checks_v2 GROUP BY pair) latest "
        "ON mp.pair = latest.pair AND mp.ts = latest.mts"
    )
    return [dict(r) for r in rows]


def check_pair(pair: str, bias: str, market_pulse_status: str) -> dict:
    """Ein Paar komplett durchprüfen: market_pulse-Filter -> Hard-Gate ->
    (nur bei bestandenem Gate) Readiness. Gibt ein dict mit allen
    entry_readiness_checks_v2-Spalten + 'grund'/'zone' (nur für die
    Konsolenausgabe, nicht Teil des DB-Schemas) zurück."""
    result = {
        "pair": pair, "bias": bias, "market_pulse_status": market_pulse_status,
        "gate_passed": False, "h4_confirmed": None, "h1_confirmed": None,
        "pullback_confirmed": None, "momentum_confirmed": None, "readiness": None,
        "grund": None, "zone": None,
    }

    # Schritt 1: market_pulse-Filter (spart yfinance-Calls für den Rest)
    if market_pulse_status != "bestätigt sich":
        result["grund"] = "market_pulse_nicht_bestaetigt"
        return result

    ticker = _pair_to_ticker(pair)
    df_1h = _download(ticker, YF_PERIOD_1H, "1h")
    if df_1h.empty:
        result["grund"] = "keine_kursdaten"
        return result

    # Schritt 2: HARD-GATE (H4-Trend + H1-Struktur je direkt gegen Bias)
    df_4h = df_1h.resample("4h").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last",
    }).dropna(subset=["Close"])
    h4_ema = _ema(df_4h["Close"], H4_EMA_PERIOD)
    h4_close, h4_ema_now = float(df_4h["Close"].iloc[-1]), float(h4_ema.iloc[-1])
    h4_trend = _trend_with_hysteresis(df_4h["Close"], h4_ema)

    h1_ema = _ema(df_1h["Close"], H1_EMA_PERIOD)
    h1_close, h1_ema_now = float(df_1h["Close"].iloc[-1]), float(h1_ema.iloc[-1])
    h1_trend = _trend_with_hysteresis(df_1h["Close"], h1_ema)

    hard_gate_ok = (h4_trend == bias) and (h1_trend == bias)
    if not hard_gate_ok:
        result["grund"] = "hard_gate_h4_h1_gegen_bias"
        return result

    # Schritt 3: Hard-Gate bestanden -> jetzt erst M15-Daten laden (Pullback/Momentum)
    df_15m = _download(ticker, YF_PERIOD_15M, "15m")
    if df_15m.empty:
        result["grund"] = "keine_m15_kursdaten"
        return result

    m15_ema = _ema(df_15m["Close"], M15_EMA_PERIOD)
    m15_close, m15_ema_now = float(df_15m["Close"].iloc[-1]), float(m15_ema.iloc[-1])
    abstand_pct = abs(m15_close - m15_ema_now) / m15_ema_now
    pullback_confirmed = abstand_pct <= M15_PULLBACK_TOLERANCE_PCT

    rsi = _rsi(df_15m["Close"])
    rsi_now = float(rsi.iloc[-1])
    ueberdehnt = (rsi_now > RSI_OVERBOUGHT) if bias == "LONG" else (rsi_now < RSI_OVERSOLD)
    momentum_confirmed = not ueberdehnt

    h4_strength_confirmed = (abs(h4_close - h4_ema_now) / h4_ema_now) >= H4_STRENGTH_THRESHOLD_PCT
    h1_strength_confirmed = (abs(h1_close - h1_ema_now) / h1_ema_now) >= H1_STRENGTH_THRESHOLD_PCT

    readiness = sum([pullback_confirmed, momentum_confirmed,
                      h4_strength_confirmed, h1_strength_confirmed])

    result.update({
        "gate_passed": True,
        "h4_confirmed": h4_strength_confirmed,
        "h1_confirmed": h1_strength_confirmed,
        "pullback_confirmed": pullback_confirmed,
        "momentum_confirmed": momentum_confirmed,
        "readiness": readiness,
        "zone": _zone(readiness),
    })
    return result


def save_results(results: list[dict], dry_run: bool) -> int:
    """WICHTIG (Regel HANDOVER.md 12.08.2026): dry-run-Pruefung ganz am
    Anfang, VOR jedem Write. Gibt Anzahl geschriebener Zeilen zurueck."""
    if dry_run:
        return 0
    n = 0
    for r in results:
        db_v2.execute(
            "INSERT INTO entry_readiness_checks_v2 "
            "(pair, bias, market_pulse_status, gate_passed, h4_confirmed, h1_confirmed, "
            "pullback_confirmed, momentum_confirmed, readiness) VALUES (?,?,?,?,?,?,?,?,?)",
            (r["pair"], r["bias"], r["market_pulse_status"], int(r["gate_passed"]),
             None if r["h4_confirmed"] is None else int(r["h4_confirmed"]),
             None if r["h1_confirmed"] is None else int(r["h1_confirmed"]),
             None if r["pullback_confirmed"] is None else int(r["pullback_confirmed"]),
             None if r["momentum_confirmed"] is None else int(r["momentum_confirmed"]),
             r["readiness"]),
        )
        n += 1
    return n


def print_table(results: list[dict]) -> None:
    header = (f"{'Pair':<10} {'Bias':<6} {'MarketPulse':<16} {'Gate':<6} "
              f"{'H4':<6} {'H1':<6} {'Pullb.':<7} {'Mom.':<6} {'Read.':<6} "
              f"{'Zone':<15} Grund")
    print(header)
    print("-" * len(header))
    for r in results:
        def b(v):
            return "-" if v is None else ("✅" if v else "❌")
        print(f"{r['pair']:<10} {r['bias']:<6} {r['market_pulse_status']:<16} "
              f"{('✅' if r['gate_passed'] else '❌'):<6} "
              f"{b(r['h4_confirmed']):<6} {b(r['h1_confirmed']):<6} "
              f"{b(r['pullback_confirmed']):<7} {b(r['momentum_confirmed']):<6} "
              f"{(str(r['readiness']) if r['readiness'] is not None else '-'):<6} "
              f"{(r['zone'] or '-'):<15} {r['grund'] or ''}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true",
                    help="Schreibt wirklich nach entry_readiness_checks_v2 (Standard: dry-run, nur print)")
    args = p.parse_args()
    dry_run = not args.write

    pulses = latest_market_pulse()
    if not pulses:
        print("Keine Einträge in market_pulse_checks_v2 - zuerst market_pulse_v2.py --write laufen lassen.")
        return

    print(f"Entry Gate v2 — {len(pulses)} Paar(e) aus market_pulse_checks_v2 geprüft — "
          f"{'DRY-RUN, kein DB-Write' if dry_run else 'SCHREIBT nach entry_readiness_checks_v2'}\n")

    results = [check_pair(p_["pair"], p_["bias"], p_["einordnung"]) for p_ in pulses]
    print_table(results)

    n_written = save_results(results, dry_run)
    print(f"\n{'(dry-run: 0 Zeilen geschrieben)' if dry_run else f'{n_written} Zeile(n) in entry_readiness_checks_v2 geschrieben.'}")


if __name__ == "__main__":
    main()
