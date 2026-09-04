#!/usr/bin/env python3
"""
evaluate_v2.py — 5MG v2, Erfolgsauswertung.

Gleiches Zwei-Ebenen-Prinzip wie evaluate_signals.py (v1), EIGENER
Codepfad (kein Import aus v1), eigenes, einfacheres Schema
(signal_performance_v2 - eine Zeile pro (pair, bias, engine), kein
separates OPEN/DONE-Statusfeld: ebeneN_ergebnis IS NULL = noch nicht
ausgewertet, sonst 'WIN'/'LOSS').

  Ebene 1 — Market-Pulse-Signal-Performance:
    Für jedes Signal in market_pulse_checks_v2 (neuester Eintrag pro
    Paar), das >=7 Tage alt ist und noch nicht ausgewertet wurde: Kurs
    JETZT holen, mit dem dort bereits gespeicherten kurs_bei_signal
    vergleichen. WIN wenn Bewegung in Bias-Richtung, sonst LOSS.

  Ebene 2 — Erste-Gate-Bestätigung-Performance:
    Für jedes Paar, das in entry_readiness_checks_v2 JEMALS
    gate_passed=1 hatte (frühester solcher Zeitpunkt), falls das
    >=7 Tage her ist und noch nicht ausgewertet: Kurs AM/NACH der
    Gate-Bestätigung (historischer Tages-Schlusskurs) vs. Kurs JETZT.

Idempotent: ein einmal gesetztes ebeneN_ergebnis wird nie erneut
berechnet/überschrieben (wie evaluate_signals.py).

Stichproben-Kennzeichnung: "n=X, NICHT BELASTBAR (<30)" wird IMMER
angezeigt, solange n<30 - schon ab n=1, nicht erst ab einer bestimmten
Größe (bewusst einfacher als v1s MIN_FUER_PROZENT/MIN_STICHPROBE-
Doppelschwelle).

--dry-run ist STANDARD (nur print, kein DB-Write). Die dry-run-Prüfung
sitzt an der Quelle: get_or_create_row()/…_update_row() werden nur bei
write=True überhaupt aufgerufen (siehe run()), nicht erst kurz vor dem
eigentlichen SQL-Call.

    python3 evaluate_v2.py            # dry-run (Standard), nur print
    python3 evaluate_v2.py --write    # schreibt wirklich nach signal_performance_v2
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

SCRIPTS_DIR = Path("/home/pi/hermes2/scripts")
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(Path(__file__).parent))
import db_v2  # v2, NUR v2-Tabellen (market_pulse_checks_v2, entry_readiness_checks_v2 lesend, signal_performance_v2 schreibend)

EVAL_WINDOW_DAYS = 7
MIN_STICHPROBE = 30  # Projektweite Konvention (siehe evaluate_signals.py)


# --- Kursquelle, eigener v2-Codepfad (kein Import aus entry_signal.py) -----
def _pair_to_ticker(pair: str) -> str:
    return pair.replace("/", "") + "=X"


def _current_price(ticker: str) -> float | None:
    """'Kurs jetzt' - letzter verfügbarer Tages-Schlusskurs."""
    try:
        df = yf.download(ticker, period="5d", interval="1d", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


def _price_on_or_after(ticker: str, target_dt: datetime) -> float | None:
    """Erster verfügbarer Tages-Schlusskurs AM ODER NACH target_dt - für
    Ebene 2 (Kurs zum historischen Gate-Bestätigungszeitpunkt), da dieser
    Preis nirgendwo gespeichert ist. Schlankere v2-Fassung von
    evaluate_signals._price_on_or_after() (eigener Codepfad, ohne die
    dortige Wochenend-/Feiertags-Abweichungs-Detailwarnung - v2 bewusst
    einfacher gehalten)."""
    start = target_dt.date()
    end = start + timedelta(days=6)
    try:
        df = yf.download(ticker, start=start.isoformat(), end=end.isoformat(),
                          interval="1d", progress=False, auto_adjust=True)
    except Exception:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.empty:
        return None
    return float(df["Close"].iloc[0])


def _direction_correct(pct_change: float, bias: str) -> bool:
    return (pct_change > 0) == (bias == "LONG")


# --- Datenzugriff v2 ---------------------------------------------------------
def latest_market_pulse_all() -> list[dict]:
    """Neuester market_pulse_checks_v2-Eintrag PRO PAAR."""
    rows = db_v2.query(
        "SELECT mp.pair, mp.bias, mp.engine, mp.kurs_bei_signal, mp.ts "
        "FROM market_pulse_checks_v2 mp "
        "INNER JOIN (SELECT pair, MAX(ts) mts FROM market_pulse_checks_v2 GROUP BY pair) latest "
        "ON mp.pair = latest.pair AND mp.ts = latest.mts"
    )
    return [dict(r) for r in rows]


def first_gate_passed_by_pair() -> dict[str, dict]:
    """Früheste gate_passed=1-Zeile PRO PAAR aus entry_readiness_checks_v2."""
    rows = db_v2.query(
        "SELECT e.pair, e.bias, e.ts FROM entry_readiness_checks_v2 e "
        "INNER JOIN (SELECT pair, MIN(ts) mts FROM entry_readiness_checks_v2 "
        "WHERE gate_passed=1 GROUP BY pair) first_gate "
        "ON e.pair = first_gate.pair AND e.ts = first_gate.mts AND e.gate_passed = 1"
    )
    return {r["pair"]: dict(r) for r in rows}


def _get_row_readonly(pair: str, bias: str, engine: str) -> dict | None:
    rows = db_v2.query(
        "SELECT * FROM signal_performance_v2 WHERE pair=? AND bias=? AND engine=?",
        (pair, bias, engine),
    )
    return dict(rows[0]) if rows else None


def get_or_create_row(pair: str, bias: str, engine: str) -> dict:
    existing = _get_row_readonly(pair, bias, engine)
    if existing:
        return existing
    new_id = db_v2.execute(
        "INSERT INTO signal_performance_v2 (pair, bias, engine) VALUES (?,?,?)",
        (pair, bias, engine),
    )
    return dict(db_v2.query("SELECT * FROM signal_performance_v2 WHERE id=?", (new_id,))[0])


def _update_row(row_id: int, fields: dict) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    db_v2.execute(f"UPDATE signal_performance_v2 SET {cols} WHERE id = ?",
                  (*fields.values(), row_id))


# --- Auswertung je Ebene -----------------------------------------------------
def evaluate_ebene1(mp: dict, existing: dict | None) -> dict | None:
    """Gibt zu schreibende Felder zurück, oder None (noch nicht fällig
    ODER bereits ausgewertet ODER kein Kurs verfügbar - jeweils beim
    nächsten Lauf erneut versucht)."""
    if existing and existing.get("ebene1_ergebnis") is not None:
        return None

    signal_ts = datetime.strptime(mp["ts"][:19], "%Y-%m-%d %H:%M:%S")
    if (datetime.now() - signal_ts).days < EVAL_WINDOW_DAYS:
        return None

    ticker = _pair_to_ticker(mp["pair"])
    kurs_jetzt = _current_price(ticker)
    if kurs_jetzt is None:
        return None

    kurs_signal = mp["kurs_bei_signal"]
    pct = (kurs_jetzt - kurs_signal) / kurs_signal * 100
    ergebnis = "WIN" if _direction_correct(pct, mp["bias"]) else "LOSS"

    return {
        "ebene1_kurs_signal": kurs_signal,
        "ebene1_kurs_plus7d": kurs_jetzt,
        "ebene1_ergebnis": ergebnis,
        "ebene1_ausgewertet_am": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def evaluate_ebene2(gate: dict | None, existing: dict | None) -> dict | None:
    if gate is None:
        return None
    if existing and existing.get("ebene2_ergebnis") is not None:
        return None

    gate_ts = datetime.strptime(gate["ts"][:19], "%Y-%m-%d %H:%M:%S")
    if (datetime.now() - gate_ts).days < EVAL_WINDOW_DAYS:
        return None

    ticker = _pair_to_ticker(gate["pair"])
    kurs_bei_gate = _price_on_or_after(ticker, gate_ts)
    if kurs_bei_gate is None:
        return None
    kurs_jetzt = _current_price(ticker)
    if kurs_jetzt is None:
        return None

    pct = (kurs_jetzt - kurs_bei_gate) / kurs_bei_gate * 100
    ergebnis = "WIN" if _direction_correct(pct, gate["bias"]) else "LOSS"

    return {
        "ebene2_kurs_gate_bestaetigt": kurs_bei_gate,
        "ebene2_kurs_plus7d": kurs_jetzt,
        "ebene2_ergebnis": ergebnis,
        "ebene2_ausgewertet_am": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def run(write: bool) -> tuple[list[dict], int | None]:
    """Gibt (results, aeltestes_signal_alter_tage) zurück. write=False:
    KEIN DB-Write (get_or_create_row/_update_row werden dafür gar nicht
    erst aufgerufen - siehe unten), reine Vorschau auf Basis des
    aktuellen DB-Stands (fehlende Zeilen -> existing=None)."""
    pulses = latest_market_pulse_all()
    gates = first_gate_passed_by_pair()

    results = []
    aeltestes_alter = None
    for mp in pulses:
        pair, bias, engine = mp["pair"], mp["bias"], mp["engine"]
        signal_ts = datetime.strptime(mp["ts"][:19], "%Y-%m-%d %H:%M:%S")
        alter_tage = (datetime.now() - signal_ts).days
        if aeltestes_alter is None or alter_tage > aeltestes_alter:
            aeltestes_alter = alter_tage

        existing = get_or_create_row(pair, bias, engine) if write else _get_row_readonly(pair, bias, engine)

        u1 = evaluate_ebene1(mp, existing)
        gate = gates.get(pair)
        u2 = evaluate_ebene2(gate, existing)

        if write and existing and (u1 or u2):
            _update_row(existing["id"], {**(u1 or {}), **(u2 or {})})

        merged = {**(existing or {}), **(u1 or {}), **(u2 or {})}
        results.append({"pair": pair, "bias": bias, "engine": engine, "perf": merged})

    return results, aeltestes_alter


def _fmt_result(n_win: int, n_total: int) -> str:
    """IMMER 'n=X, NICHT BELASTBAR (<30)' solange n<30 - schon ab n=1."""
    if n_total == 0:
        return "n=0, keine Auswertung vorhanden"
    pct = 100 * n_win / n_total
    hinweis = f", NICHT BELASTBAR (<{MIN_STICHPROBE})" if n_total < MIN_STICHPROBE else ""
    return f"{n_win}/{n_total} ({pct:.1f}%) — n={n_total}{hinweis}"


def print_summary(results: list[dict], aeltestes_alter: int | None) -> None:
    e1 = [r for r in results if r["perf"].get("ebene1_ergebnis") is not None]
    e2 = [r for r in results if r["perf"].get("ebene2_ergebnis") is not None]

    print("=" * 70)
    print(f"EBENE 1 — Market-Pulse-Signal vs. Kurs jetzt (>={EVAL_WINDOW_DAYS} Tage alt)")
    print("=" * 70)
    if not e1:
        alter_str = f"{aeltestes_alter} Tage" if aeltestes_alter is not None else "unbekannt (keine Signale)"
        print(f"  0 Signale auswertbar, ältestes Signal ist {alter_str} alt.")
    else:
        n_win = sum(1 for r in e1 if r["perf"]["ebene1_ergebnis"] == "WIN")
        print(f"  {_fmt_result(n_win, len(e1))}")
        for r in e1:
            p = r["perf"]
            ok = "✅" if p["ebene1_ergebnis"] == "WIN" else "❌"
            print(f"    {ok} {r['pair']:10} {r['bias']:5} {r['engine']:14} "
                  f"{p['ebene1_kurs_signal']:.5f} -> {p['ebene1_kurs_plus7d']:.5f} ({p['ebene1_ergebnis']})")

    print()
    print("=" * 70)
    print(f"EBENE 2 — Erste Gate-Bestätigung vs. Kurs jetzt (>={EVAL_WINDOW_DAYS} Tage her)")
    print("=" * 70)
    if not e2:
        print("  0 Signale auswertbar (kein Gate seit >=7 Tagen bestätigt, oder noch keins).")
    else:
        n_win = sum(1 for r in e2 if r["perf"]["ebene2_ergebnis"] == "WIN")
        print(f"  {_fmt_result(n_win, len(e2))}")
        for r in e2:
            p = r["perf"]
            ok = "✅" if p["ebene2_ergebnis"] == "WIN" else "❌"
            print(f"    {ok} {r['pair']:10} {r['bias']:5} {r['engine']:14} "
                  f"{p['ebene2_kurs_gate_bestaetigt']:.5f} -> {p['ebene2_kurs_plus7d']:.5f} ({p['ebene2_ergebnis']})")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true",
                    help="Schreibt wirklich nach signal_performance_v2 (Standard: dry-run, nur print)")
    args = p.parse_args()
    write = args.write  # dry-run ist STANDARD (write=False)

    results, aeltestes_alter = run(write=write)
    print(f"{'SCHREIBT nach signal_performance_v2' if write else 'DRY-RUN, kein DB-Write'}\n")
    print_summary(results, aeltestes_alter)


if __name__ == "__main__":
    main()
