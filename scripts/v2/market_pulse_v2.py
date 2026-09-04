#!/usr/bin/env python3
"""
market_pulse_v2.py — 5MG v2, Phase 1.

Erweiterung des bereits validierten Standalone-Tests market_pulse.py (im
übergeordneten scripts/-Ordner, NICHT verändert - dient hier als
Vorlage): liest ALLE aktuellen Signale des neuesten Wochenlaufs aus
weekly_engine_signals (v1-Tabelle, NUR LESEND — nicht nur
is_top_signal=1, sondern alle drei Engines: Basis-/Fluss-/Kombi-Signal;
live gegen die DB geprüft, aktuell konsistent 3 Zeilen pro Lauf-ts),
vergleicht Kurs bei Signal vs. aktuell (yfinance, wie in entry_signal.py
erprobt) und schreibt das Ergebnis nach market_pulse_checks_v2
(v2-Tabelle, additiv, siehe db_v2.py).

Einordnung unverändert aus dem validierten Test: "bestätigt sich" /
"läuft dagegen" / "neutral" bei ±0.1%-Schwelle — VORLÄUFIG, da bislang
nur an einem einzigen Datenpunkt beobachtet, nicht kalibriert.

--dry-run ist STANDARD (nur print, kein DB-Write) - Regel aus
HANDOVER.md "Gelernte Regeln" 12.08.2026: die dry-run-Prüfung steht
GANZ AM ANFANG von save_checks(), vor jedem einzelnen Write, nicht nur
vor dem "offensichtlichsten" Seiteneffekt. Für einen echten Schreib-
Lauf: --write explizit übergeben.

KEIN Telegram-Versand, KEINE Cron-Einbindung, KEINE Änderung an
entry_monitor.py/bot_commands.py.
    python3 market_pulse_v2.py           # dry-run (Standard), nur print
    python3 market_pulse_v2.py --write   # schreibt wirklich nach market_pulse_checks_v2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

SCRIPTS_DIR = Path("/home/pi/hermes2/scripts")
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(Path(__file__).parent))
import db       # v1, NUR LESEND (weekly_engine_signals)
import db_v2    # v2, Schreibzugriff (market_pulse_checks_v2)

# Lokale Zeitzone des Pi (Europe/Zurich, siehe timedatectl) - weekly_engine_
# signals.ts wird ueber datetime('now','localtime') geschrieben (db.py-
# Konvention), ist also lokale Pi-Zeit, NICHT UTC.
LOCAL_TZ = ZoneInfo("Europe/Zurich")

# Wie in entry_signal.py erprobt.
YF_PERIOD_1H = "60d"
YF_INTERVAL_1H = "1h"

# Mindest-Bewegung fuer eine Einordnung abseits von "neutral" - VORLAEUFIG,
# nur an einem Datenpunkt beobachtet (siehe Docstring).
MOVE_THRESHOLD_PCT = 0.1


def _pair_to_ticker(pair: str) -> str:
    """'USD/CAD' -> 'USDCAD=X' (Yahoo-FX-Notation) - 1:1 aus entry_signal.py."""
    return pair.replace("/", "") + "=X"


def _download(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, period=YF_PERIOD_1H, interval=YF_INTERVAL_1H,
                      progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _price_at(df: pd.DataFrame, ts_str: str) -> tuple[float, pd.Timestamp]:
    """Naechstgelegener 1h-Balken zum Signal-Zeitpunkt (ts_str ist lokale
    Pi-Zeit, wird auf die Zeitzone des yfinance-Index umgerechnet)."""
    local_ts = pd.Timestamp(ts_str).tz_localize(LOCAL_TZ)
    target = local_ts.tz_convert(df.index.tz)
    idx = df.index.get_indexer([target], method="nearest")[0]
    return float(df["Close"].iloc[idx]), df.index[idx]


def get_latest_signals() -> list[dict]:
    """ALLE Zeilen des neuesten Wochenlaufs (nicht nur is_top_signal=1) -
    live gegen die DB geprüft: konsistent 3 Zeilen (Basis-/Fluss-/
    Kombi-Signal) pro Lauf-ts."""
    row = db.query("SELECT MAX(ts) t FROM weekly_engine_signals")
    latest_ts = row[0]["t"] if row else None
    if not latest_ts:
        return []
    rows = db.query(
        "SELECT engine, pair, bias, final_quality, ts FROM weekly_engine_signals "
        "WHERE ts=? ORDER BY final_quality DESC",
        (latest_ts,),
    )
    return [dict(r) for r in rows]


def classify(bias: str, pct_move: float, threshold: float = MOVE_THRESHOLD_PCT) -> str:
    """LONG: Bewegung nach oben bestaetigt den Bias. SHORT: umgekehrt."""
    in_bias_direction = pct_move if bias == "LONG" else -pct_move
    if in_bias_direction >= threshold:
        return "bestätigt sich"
    if in_bias_direction <= -threshold:
        return "läuft dagegen"
    return "neutral"


def compute_checks(signals: list[dict]) -> list[dict]:
    """Reine Berechnung, KEIN DB-Zugriff (schreibend) - siehe save_checks()
    für den getrennten Schreibschritt."""
    results = []
    for s in signals:
        pair, bias, engine = s["pair"], s["bias"], s["engine"]
        ticker = _pair_to_ticker(pair)
        try:
            df = _download(ticker)
            if df.empty:
                results.append({"pair": pair, "bias": bias, "engine": engine,
                                 "kurs_bei_signal": None, "kurs_aktuell": None,
                                 "bewegung_pct": None, "einordnung": None,
                                 "fehler": "keine Kursdaten von yfinance"})
                continue
            price_at_signal, _matched_ts = _price_at(df, s["ts"])
            price_now = float(df["Close"].iloc[-1])
            pct_move = (price_now - price_at_signal) / price_at_signal * 100
            einordnung = classify(bias, pct_move)
            results.append({"pair": pair, "bias": bias, "engine": engine,
                             "kurs_bei_signal": price_at_signal, "kurs_aktuell": price_now,
                             "bewegung_pct": pct_move, "einordnung": einordnung, "fehler": None})
        except Exception as e:
            results.append({"pair": pair, "bias": bias, "engine": engine,
                             "kurs_bei_signal": None, "kurs_aktuell": None,
                             "bewegung_pct": None, "einordnung": None, "fehler": str(e)})
    return results


def save_checks(results: list[dict], dry_run: bool) -> int:
    """WICHTIG (Regel HANDOVER.md 12.08.2026): dry-run-Pruefung ganz am
    Anfang, VOR jedem Write - nicht nur vor dem 'offensichtlichsten'
    Seiteneffekt. Gibt Anzahl geschriebener Zeilen zurueck (0 bei
    dry_run=True)."""
    if dry_run:
        return 0
    n = 0
    for r in results:
        if r["fehler"] is not None:
            continue  # keine Fehler-Zeilen in die Tabelle schreiben
        db_v2.execute(
            "INSERT INTO market_pulse_checks_v2 "
            "(pair, bias, engine, kurs_bei_signal, kurs_aktuell, bewegung_pct, einordnung) "
            "VALUES (?,?,?,?,?,?,?)",
            (r["pair"], r["bias"], r["engine"], r["kurs_bei_signal"],
             r["kurs_aktuell"], r["bewegung_pct"], r["einordnung"]),
        )
        n += 1
    return n


def print_table(results: list[dict]) -> None:
    header = f"{'Pair':<10} {'Bias':<6} {'Engine':<14} {'Kurs@Signal':>12} {'Kurs jetzt':>12} {'Bewegung':>10}  Einordnung"
    print(header)
    print("-" * len(header))
    for r in results:
        if r["fehler"] is not None:
            print(f"{r['pair']:<10} {r['bias']:<6} {r['engine']:<14} {'—':>12} {'—':>12} {'—':>10}  FEHLER: {r['fehler']}")
        else:
            print(f"{r['pair']:<10} {r['bias']:<6} {r['engine']:<14} "
                  f"{r['kurs_bei_signal']:>12.5f} {r['kurs_aktuell']:>12.5f} "
                  f"{r['bewegung_pct']:>+9.2f}%  {r['einordnung']}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true",
                    help="Schreibt wirklich nach market_pulse_checks_v2 (Standard: dry-run, nur print)")
    args = p.parse_args()
    dry_run = not args.write

    signals = get_latest_signals()
    if not signals:
        print("Keine Signale in weekly_engine_signals gefunden.")
        return

    print(f"Market Pulse v2 — {len(signals)} Signal(e) aus weekly_engine_signals "
          f"(ts={signals[0]['ts']}) — {'DRY-RUN, kein DB-Write' if dry_run else 'SCHREIBT nach market_pulse_checks_v2'}\n")

    results = compute_checks(signals)
    print_table(results)

    n_written = save_checks(results, dry_run)
    print(f"\n{'(dry-run: 0 Zeilen geschrieben)' if dry_run else f'{n_written} Zeile(n) in market_pulse_checks_v2 geschrieben.'}")


if __name__ == "__main__":
    main()
