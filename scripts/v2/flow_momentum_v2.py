#!/usr/bin/env python3
"""
flow_momentum_v2.py — 5MG v2, isolierter Flow-Momentum-Tracker OHNE change_4w.

Testet den Backtest-Befund live und isoliert (siehe
reports/backtest_5mg_lauf_2026-09-07.md, Abschnitte 6-9: change_4w
schneidet einzeln unter der Gesamt-Engine-Baseline ab, eine Simulation
ohne change_4w verbessert die Trefferquote über alle getesteten
Zeiträume/Split-Punkte hinweg spürbar, Verbesserung hält auch
out-of-sample). v1 (cot_engines.py/weekly_engine_report.py) bleibt
UNVERÄNDERT und ist die Kontrollgruppe — dieses Skript rührt v1 nicht an.

NEUE GEWICHTUNG (WEIGHTS_FLOW_V2, gegenüber WEIGHTS_FLOW in
cot_engines.py — change_4w entfernt, Restgewicht proportional auf die
verbleibenden vier Komponenten verteilt, Faktor 1/(1-0.25)=1.3333,
identische Rechnung wie in der Backtest-Simulation Abschnitt 8):
    commercial_change:    0.30 -> 0.40000
    change_1w:             0.20 -> 0.26667
    flow_acceleration:     0.15 -> 0.20000
    open_interest_change:  0.10 -> 0.13333
    change_4w:              0.25 -> ENTFERNT

Schlanker, eigenständiger Tracker (Rücksprache 08.09.2026: NICHT ins
Market-Pulse-/Entry-Gate-/Quality-Tier-System eingehängt — eigener
Pick, eigene Tabelle, eigene Auswertung, analog zum bereits etablierten
mo_system_backtest.py/evaluate_signals.py-Muster). Wiederverwendet NUR
reine, zustandslose Bausteine aus cot_engines.py
(_flow_score/_acceleration_score/_oi_change_score/_weighted_average)
und cot_loader.py (load_history) — beide Dateien UNVERÄNDERT, NUR
LESEND. v1_pair/v1_bias/v1_score wird zum Vergleich aus
weekly_engine_signals (v1, NUR LESEND) für dieselbe Kalenderwoche
übernommen.

--write ist NICHT Standard (dry-run per Default, druckt nur) - Regel
aus HANDOVER.md "Gelernte Regeln" 12.08.2026: dry-run-Pruefung steht
GANZ AM ANFANG, vor jedem Schreibzugriff.

CLI:
    python3 flow_momentum_v2.py              # dry-run, nur print
    python3 flow_momentum_v2.py --write       # schreibt neuen Wochen-Pick
    python3 flow_momentum_v2.py --evaluate              # dry-run Auswertung
    python3 flow_momentum_v2.py --evaluate --write       # schreibt Auswertung
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

SCRIPTS_DIR = Path("/home/pi/hermes2/scripts")
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path.home() / "hermes2" / "scripts" / "5mg_analyzer_repo" / "scripts"))

import db          # v1, NUR LESEND (weekly_engine_signals)
import db_v2       # v2, Schreibzugriff (flow_momentum_v2_signals)
import cot_engines  # v1, NUR LESEND (reine Bausteine, keine eigene Schreiblogik)
import cot_loader   # v1, NUR LESEND
from watchlist_export import PAIR_WHITELIST, XXX_USD_STYLE

YF_TICKERS = {
    "USD/CHF": "USDCHF=X", "USD/CAD": "USDCAD=X", "NZD/USD": "NZDUSD=X",
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "AUD/USD": "AUDUSD=X", "USD/MXN": "USDMXN=X",
}

WEIGHTS_FLOW_V2 = {
    "commercial_change": 0.30 / 0.75,
    "change_1w": 0.20 / 0.75,
    "flow_acceleration": 0.15 / 0.75,
    "open_interest_change": 0.10 / 0.75,
}
assert abs(sum(WEIGHTS_FLOW_V2.values()) - 1.0) < 1e-9

EVAL_WINDOWS_DAYS = (5, 14, 28)


def flow_momentum_score_v2(ccy: str, rows: list[dict]) -> float | None:
    components = {
        "commercial_change": cot_engines._flow_score(rows, 1, "comm_long", "comm_short"),
        "change_1w": cot_engines._flow_score(rows, 1, "long", "short"),
        "flow_acceleration": cot_engines._acceleration_score(rows, 4, "long", "short"),
        "open_interest_change": cot_engines._oi_change_score(rows, 1),
    }
    return cot_engines._weighted_average(components, WEIGHTS_FLOW_V2)


def _pair_for_ccy(ccy: str, score: float) -> tuple[str, str]:
    ccy_richtung = "LONG" if score >= 50 else "SHORT"
    if ccy in XXX_USD_STYLE:
        return f"{ccy}/USD", ccy_richtung
    return f"USD/{ccy}", ("SHORT" if ccy_richtung == "LONG" else "LONG")


def top_pick_v2(years: list[int]) -> tuple[str, str, str, float, date] | None:
    """(ccy, pair, bias, score, stichtag) fuer die juengste verfuegbare
    COT-Report-Woche, mit dem neuen Gewicht (ohne change_4w)."""
    series = cot_loader.load_history(years)
    best = None
    stichtag = None
    for ccy in PAIR_WHITELIST:
        rows = series.get(ccy, [])
        if not rows:
            continue
        score = flow_momentum_score_v2(ccy, rows)
        if score is None:
            continue
        if best is None or abs(score - 50) > abs(best[1] - 50):
            best = (ccy, score)
            stichtag = rows[-1]["date"].date() if hasattr(rows[-1]["date"], "date") else rows[-1]["date"]
    if best is None:
        return None
    ccy, score = best
    pair, bias = _pair_for_ccy(ccy, score)
    return ccy, pair, bias, score, stichtag


def _v1_vergleich(stichtag: date) -> tuple[str | None, str | None, float | None]:
    """Liest v1s Flow-Momentum-Pick (altes Gewicht) fuer denselben
    Stichtag, falls schon in weekly_engine_signals vorhanden."""
    rows = db.query(
        "SELECT pair, bias, cot_score FROM weekly_engine_signals "
        "WHERE engine='Fluss-Signal' AND date(ts) >= date(?) "
        "ORDER BY ts ASC LIMIT 1",
        (stichtag.isoformat(),),
    )
    if not rows:
        return None, None, None
    return rows[0]["pair"], rows[0]["bias"], rows[0]["cot_score"]


def _kurs_jetzt(pair: str) -> float | None:
    ticker = YF_TICKERS.get(pair)
    if ticker is None:
        return None
    df = yf.download(ticker, period="5d", interval="1d", progress=False, auto_adjust=True, timeout=20)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df is None or df.empty:
        return None
    return float(df["Close"].iloc[-1])


def run_pick(write: bool) -> None:
    this_year = datetime.now().year
    result = top_pick_v2([this_year - 2, this_year - 1, this_year])
    if result is None:
        print("Kein Pick moeglich (keine COT-Daten).")
        return
    ccy, pair, bias, score, stichtag = result

    exists = db_v2.query("SELECT id FROM flow_momentum_v2_signals WHERE stichtag=?", (stichtag.isoformat(),))
    if exists:
        print(f"Stichtag {stichtag} bereits erfasst (id={exists[0]['id']}) - kein neuer Pick.")
        return

    v1_pair, v1_bias, v1_score = _v1_vergleich(stichtag)
    kurs = _kurs_jetzt(pair)

    print(f"Stichtag: {stichtag}")
    print(f"v2-Pick (ohne change_4w): {ccy} -> {pair} {bias}  Score={score:.2f}")
    print(f"v1-Pick (Vergleich, altes Gewicht): {v1_pair} {v1_bias}  Score={v1_score}")
    print(f"Kurs bei Signal: {kurs}")

    if not write:
        print("\n(dry-run, nicht geschrieben - fuer echten Lauf --write)")
        return

    db_v2.init_db_v2()
    db_v2.execute(
        """INSERT OR IGNORE INTO flow_momentum_v2_signals
           (stichtag, ccy, pair, bias, score, v1_pair, v1_bias, v1_score, kurs_bei_signal)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (stichtag.isoformat(), ccy, pair, bias, score, v1_pair, v1_bias, v1_score, kurs),
    )
    print("Geschrieben nach flow_momentum_v2_signals.")


def run_evaluate(write: bool) -> None:
    rows = [dict(r) for r in db_v2.query(
        "SELECT * FROM flow_momentum_v2_signals "
        "WHERE korrekt_5d IS NULL OR korrekt_14d IS NULL OR korrekt_28d IS NULL"
    )]
    if not rows:
        print("Keine offenen Auswertungen.")
        return

    heute = date.today()
    for r in rows:
        stichtag = date.fromisoformat(r["stichtag"])
        ticker = YF_TICKERS.get(r["pair"])
        if ticker is None or r["kurs_bei_signal"] is None:
            continue
        updates = {}
        for tage in EVAL_WINDOWS_DAYS:
            feld_korrekt = f"korrekt_{tage}d"
            feld_kurs = f"kurs_{tage}d"
            if r[feld_korrekt] is not None:
                continue
            ziel_datum = stichtag + timedelta(days=tage)
            if heute < ziel_datum:
                continue  # noch zu frueh
            df = yf.download(ticker, start=ziel_datum.isoformat(),
                              end=(ziel_datum + timedelta(days=5)).isoformat(),
                              interval="1d", progress=False, auto_adjust=True, timeout=20)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df is None or df.empty:
                continue
            kurs = float(df["Close"].iloc[0])
            bull = r["bias"] == "LONG"
            pct = (kurs - r["kurs_bei_signal"]) / r["kurs_bei_signal"]
            korrekt = (pct > 0) == bull
            updates[feld_kurs] = kurs
            updates[feld_korrekt] = 1 if korrekt else 0
            print(f"  {r['stichtag']} {r['pair']} {r['bias']}: {tage}d korrekt={korrekt}")

        if updates and write:
            updates["ausgewertet_am"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            set_clause = ", ".join(f"{k}=?" for k in updates)
            db_v2.execute(
                f"UPDATE flow_momentum_v2_signals SET {set_clause} WHERE id=?",
                (*updates.values(), r["id"]),
            )

    if not write:
        print("\n(dry-run, nicht geschrieben - fuer echten Lauf --evaluate --write)")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--evaluate", action="store_true", help="5/14/28-Tage-Auswertung faelliger Signale")
    p.add_argument("--write", action="store_true", help="Schreibt wirklich (Standard: dry-run)")
    args = p.parse_args()

    if args.evaluate:
        run_evaluate(write=args.write)
    else:
        run_pick(write=args.write)


if __name__ == "__main__":
    main()
