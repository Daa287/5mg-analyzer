#!/usr/bin/env python3
"""
backtest_engine.py — 5MG Backtest, Haupt-Orchestrierung.

NUR neue, eigenständige Dateien (backtest/-Verzeichnis) - die bestehende
Live-Pipeline (cot_loader.py, cot_engines.py, season_engine.py,
bond_loader.py, calendar_check.py, weekly_engine_report.py) wird NICHT
verändert. Wiederverwendet werden ausschließlich reine, zustandslose
Funktionen aus der Live-Pipeline (cot_engines.classic_score() etc.,
final_quality()-Formelfunktionen, weekly_engine_report._pair_for_ccy())
- diese bekommen im Backtest nur bereits gekappte/historische Daten als
Parameter übergeben, verhalten sich selbst unverändert.

Lookahead-Bias-Schutz (drei Ebenen, siehe Auftrag 07.09.2026):
  1. COT: cftc_release_calendar.veroeffentlichungsdatum() statt reinem
     Positions-Stichtag - ein Report zählt erst ab seinem ECHTEN
     Veröffentlichungsdatum als "bekannt".
  2. Zinsen: yield_history.wert_am(cutoff_date) - letzter bekannter Wert
     MIT Datum <= cutoff_date.
  3. Kurse: price_history.close_am(cutoff_date) für alles, was in die
     Signal-BILDUNG einfließt (Saison-Score). Für die ERGEBNIS-Auswertung
     (5/14/28 Tage NACH dem Signal) wird bewusst in die Zukunft geschaut
     (price_history.close_nach_tagen()) - das ist kein Bias, sondern der
     Zweck eines Backtests.

Event-Penalty: fest auf 0 (siehe Auftrag 07.09.2026) - keine historische
Wirtschaftskalender-Quelle verfügbar/geprüft. Macht den Backtest
STRUKTURELL LEICHT OPTIMISTISCHER als die Live-Version: echte Signale
hätten potenziell einen kleinen Abzug (max. -3,0 bei hoher Vola) erlitten,
den der Backtest nie sieht - siehe Bericht.

    python3 backtest_engine.py --start 2024-01-01 --end 2024-01-31   # Trockenlauf
    python3 backtest_engine.py --start 2023-09-07 --end 2026-09-07   # voller 3-Jahres-Lauf
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent  # .../5mg_analyzer_repo/scripts
sys.path.insert(0, str(SCRIPTS_DIR))
import cot_engines
import cot_loader
import final_quality
from watchlist_export import PAIR_WHITELIST, XXX_USD_STYLE

BACKTEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKTEST_DIR))
import yield_history
import price_history
import season_backtest
from cftc_release_calendar import veroeffentlichungsdatum

CURVE_MATURITY_BY_CCY_AVAILABLE = {"USD", "EUR", "CHF", "GBP", "JPY", "CAD", "AUD", "NZD"}  # MXN fehlt bewusst

EVAL_WINDOWS_DAYS = (5, 14, 28)

REPORT_DIR = Path.home() / "hermes2" / "reports"


def _pair_for_ccy(ccy: str, engine_score: float) -> tuple[str, str, str, str]:
    """1:1 wie weekly_engine_report._pair_for_ccy() - hier dupliziert
    statt importiert, weil weekly_engine_report.py beim Import
    Live-Abhängigkeiten (publish_pages, entry_signal) mitzieht, die im
    Backtest nicht gebraucht werden und zusätzliche Netzwerk-/DB-Importe
    auslösen würden."""
    ccy_richtung = "LONG" if engine_score >= 50 else "SHORT"
    if ccy in XXX_USD_STYLE:
        pair, pair_richtung = f"{ccy}/USD", ccy_richtung
    else:
        pair = f"USD/{ccy}"
        pair_richtung = "SHORT" if ccy_richtung == "LONG" else "LONG"
    base, quote = pair.split("/")
    return pair, pair_richtung, base, quote


def _yield_for(ccy: str, maturity: str, yield_cache: dict, cutoff: date) -> float | None:
    if ccy not in CURVE_MATURITY_BY_CCY_AVAILABLE:
        return None
    return yield_history.wert_am(yield_cache, ccy, maturity, cutoff)


def _macro_inputs_at(base: str, quote: str, yield_cache: dict, cutoff: date) -> dict:
    """1:1 dieselbe Formel wie weekly_engine_report._macro_inputs(), aber
    mit historischem wert_am(cutoff) statt dem aktuellen Live-Snapshot.
    Fehlende Zinsdaten (MXN) -> neutrale Teilscores, kein Crash - gleiche
    Fehlertoleranz wie das Original."""
    b2 = _yield_for(base, "2Y", yield_cache, cutoff)
    q2 = _yield_for(quote, "2Y", yield_cache, cutoff)
    b10 = _yield_for(base, "10Y", yield_cache, cutoff)
    q10 = _yield_for(quote, "10Y", yield_cache, cutoff)

    have_2y = b2 is not None and q2 is not None
    have_10y = b10 is not None and q10 is not None

    score_2y = final_quality.spread_to_score(b2 - q2) if have_2y else 50.0
    score_10y = final_quality.spread_to_score(b10 - q10) if have_10y else 50.0

    if have_2y and have_10y:
        score_curve = final_quality.spread_to_score((b10 - b2) - (q10 - q2))
        structure_confirmed = ((b2 - q2) > 0) == ((b10 - q10) > 0)
    else:
        score_curve = 50.0
        structure_confirmed = False

    # score_impulse (5-Tage-Aenderung des 2Y-Spreads): im Backtest ueber
    # denselben yield_cache berechnet (Wert vor 5 Kalendertagen vs. cutoff),
    # statt der Live-Version bond_yield_history.json (die erst seit
    # 12.08.2026 existiert und fuer historische Cutoffs nicht nutzbar ist).
    if have_2y:
        b2_past = _yield_for(base, "2Y", yield_cache, cutoff - timedelta(days=5))
        q2_past = _yield_for(quote, "2Y", yield_cache, cutoff - timedelta(days=5))
        if b2_past is not None and q2_past is not None:
            score_impulse = final_quality.spread_to_score((b2 - q2) - (b2_past - q2_past))
        else:
            score_impulse = 50.0
    else:
        score_impulse = 50.0

    return {
        "score_2y": score_2y, "score_10y": score_10y,
        "score_curve": score_curve, "score_impulse": score_impulse,
        "structure_confirmed": structure_confirmed,
        "data_complete": have_2y and have_10y,
    }


def _cot_rows_bis_cutoff(rows: list[dict], cutoff: date) -> list[dict]:
    """Lookahead-sicherer COT-Filter: ein Report zaehlt erst ab seinem
    ECHTEN Veroeffentlichungsdatum (nicht dem Positions-Stichtag) als
    bekannt."""
    out = []
    for r in rows:
        stichtag = r["date"].date() if hasattr(r["date"], "date") else r["date"]
        if veroeffentlichungsdatum(stichtag) <= cutoff:
            out.append(r)
    return out


def _run_engines_at(cot_series_full: dict, cutoff: date) -> dict:
    """Wie cot_engines.run_all_engines(), aber mit pro-Waehrung auf
    cutoff gekapptem COT-Verlauf (Lookahead-Schutz Ebene 1)."""
    truncated = {ccy: _cot_rows_bis_cutoff(rows, cutoff) for ccy, rows in cot_series_full.items()}

    pool_commercial_net = {}
    for ccy in PAIR_WHITELIST:
        rows = truncated.get(ccy, [])
        if rows and rows[-1].get("comm_long") is not None and rows[-1].get("comm_short") is not None:
            pool_commercial_net[ccy] = rows[-1]["comm_long"] - rows[-1]["comm_short"]
        else:
            pool_commercial_net[ccy] = None

    out = {}
    for ccy in PAIR_WHITELIST:
        rows = truncated.get(ccy, [])
        if not rows:
            continue
        out[ccy] = {
            "classic": cot_engines.classic_score(ccy, rows, pool_commercial_net),
            "flow_momentum": cot_engines.flow_momentum_score(ccy, rows),
            "hybrid": cot_engines.hybrid_score(ccy, rows),
        }
    return out


def _top_pick(results: dict, engine_key: str) -> tuple[str, float] | None:
    scored = [(ccy, r[engine_key]["score"]) for ccy, r in results.items() if r[engine_key]["score"] is not None]
    if not scored:
        return None
    scored.sort(key=lambda t: abs(t[1] - 50), reverse=True)
    return scored[0]


def build_report_at(engine_key: str, cot_series_full: dict, cutoff: date,
                     yield_cache: dict, price_cache: dict) -> dict | None:
    results = _run_engines_at(cot_series_full, cutoff)
    pick = _top_pick(results, engine_key)
    if pick is None:
        return None
    ccy, engine_score = pick
    pair, pair_richtung, base, quote = _pair_for_ccy(ccy, engine_score)

    season = season_backtest.season_score_at(pair, pair_richtung, cutoff, price_cache.get(pair, []))
    macro_in = _macro_inputs_at(base, quote, yield_cache, cutoff)
    macro = final_quality.macro_score(
        macro_in["score_2y"], macro_in["score_10y"], macro_in["score_curve"], macro_in["score_impulse"])

    result = final_quality.final_quality(
        cot_score=engine_score, season_score=season["score"], macro_score_value=macro,
        structure_confirmed=macro_in["structure_confirmed"], volatility=0.0)  # Event-Penalty fest 0, siehe Docstring

    entry_preis = price_history.close_am(price_cache.get(pair, []), cutoff)

    return {
        "engine_key": engine_key, "cutoff": cutoff.isoformat(), "ccy": ccy,
        "pair": pair, "pair_richtung": pair_richtung,
        "final_quality": result["final_quality"], "cot_score": engine_score,
        "season_score": season["score"], "macro_score": macro,
        "macro_data_complete": macro_in["data_complete"],
        "structure_confirmed": macro_in["structure_confirmed"],
        "entry_preis": entry_preis,
    }


def evaluate_outcome(report: dict, price_cache: dict) -> dict:
    pair, richtung = report["pair"], report["pair_richtung"]
    series = price_cache.get(pair, [])
    cutoff = date.fromisoformat(report["cutoff"])
    entry = report["entry_preis"]
    out = dict(report)
    if entry is None:
        out["outcome_error"] = "kein Entry-Kurs verfuegbar"
        return out
    bull = richtung == "LONG"
    for tage in EVAL_WINDOWS_DAYS:
        kurs = price_history.close_nach_tagen(series, cutoff, tage)
        if kurs is None:
            out[f"korrekt_{tage}d"] = None
            continue
        pct = (kurs - entry) / entry
        out[f"kurs_{tage}d"] = kurs
        out[f"korrekt_{tage}d"] = (pct > 0) == bull
    return out


def report_tuesdays_in_range(cot_series_full: dict, start: date, end: date) -> list[date]:
    all_dates = set()
    for rows in cot_series_full.values():
        for r in rows:
            d = r["date"].date() if hasattr(r["date"], "date") else r["date"]
            if start <= d <= end:
                all_dates.add(d)
    return sorted(all_dates)


def run_backtest(start: date, end: date, verbose: bool = True) -> list[dict]:
    t0 = time.time()
    if verbose:
        print(f"Lade COT-Historie (2022-{date.today().year})...")
    years = list(range(2022, date.today().year + 1))
    cot_series_full = cot_loader.load_history(years)

    if verbose:
        print("Lade Zins-/Kurs-Cache...")
    yield_cache = yield_history.load_cached(refresh=False)
    price_cache = price_history.load_cached(refresh=False)

    tuesdays = report_tuesdays_in_range(cot_series_full, start, end)
    if verbose:
        print(f"{len(tuesdays)} Report-Wochen im Zeitraum {start} bis {end}\n")

    all_results = []
    for stichtag in tuesdays:
        cutoff = veroeffentlichungsdatum(stichtag)
        for engine_key in ("classic", "flow_momentum", "hybrid"):
            report = build_report_at(engine_key, cot_series_full, cutoff, yield_cache, price_cache)
            if report is None:
                continue
            outcome = evaluate_outcome(report, price_cache)
            outcome["stichtag"] = stichtag.isoformat()
            all_results.append(outcome)
            if verbose:
                k5 = outcome.get("korrekt_5d")
                print(f"  {stichtag} ({engine_key:14s}) {report['pair']:8s} {report['pair_richtung']:5s} "
                      f"FQ={report['final_quality']:6.1f}  5d-korrekt={k5}")

    dt = time.time() - t0
    if verbose:
        n_weeks = len(tuesdays)
        print(f"\n{len(all_results)} Signale aus {n_weeks} Report-Wochen in {dt:.1f}s "
              f"({dt/max(n_weeks,1):.2f}s/Woche)")
    return all_results


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--out", help="Pfad fuer JSON-Ergebnis (optional)")
    args = p.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    results = run_backtest(start, end)

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nGeschrieben: {args.out}")


if __name__ == "__main__":
    main()
