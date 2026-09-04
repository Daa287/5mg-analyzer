#!/usr/bin/env python3
"""
report_v2.py — 5MG v2, fasst Phase 1-3 zusammen (market_pulse_v2,
entry_gate_v2, quality_tiers_v2) zu einer Telegram-Testnachricht.

Für jedes aktuelle Top-Signal der Woche (weekly_engine_signals, v1,
NUR LESEND):
  - Market-Pulse-Status (market_pulse_checks_v2, neuester Eintrag pro
    Paar - "noch nicht geprüft" falls kein Eintrag vorhanden)
  - Gate-Status + Readiness + Zone (entry_readiness_checks_v2, neuester
    Eintrag pro Paar - "noch nicht geprüft" falls kein Eintrag
    vorhanden, KEIN Fehler)
  - Quality Tier (quality_tiers_v2.klassifiziere(), live berechnet,
    kein DB-Zugriff nötig)

Versand über die BESTEHENDE telegram_lib.send()-Funktion (gleicher Bot
wie v1) an TRADING_CHAT_ID (.env geprüft, bereits als echter
Trading-Channel verifiziert). ERSTE Zeile der Nachricht immer
"🧪 [5MG v2 TEST]".

HTML-Escaping: alle dynamischen Textbausteine laufen vor dem
Zusammenbau durch _esc() (< > & escapen) - dieselbe Vorsichtsmaßnahme
wie beim bekannten Bug in evaluate_signals.py (rohes "<" bricht
Telegrams HTML-Parser und verwirft die GESAMTE Nachricht), hier
präventiv eingebaut, auch wenn aktuell kein "<"/">" in den Werten
vorkommt.

--dry-run ist STANDARD (nur print, KEIN Telegram-Versand). Nur bei
explizitem --send wird telegram_lib.send() aufgerufen. Die dry-run-
Prüfung steht ganz am Anfang von main(), vor dem Telegram-Call (Regel
HANDOVER.md "Gelernte Regeln" 12.08.2026).

    python3 report_v2.py            # dry-run (Standard), nur print
    python3 report_v2.py --send     # echter Telegram-Versand
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path("/home/pi/hermes2/scripts")
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(Path(__file__).parent))
import db        # v1, NUR LESEND (weekly_engine_signals)
import db_v2     # v2, NUR LESEND (market_pulse_checks_v2, entry_readiness_checks_v2, signal_performance_v2)
import quality_tiers_v2 as qt
import evaluate_v2 as ev  # nur _fmt_result() wiederverwendet - keine Logik-Duplikation der NICHT-BELASTBAR-Formatierung
from telegram_lib import send as telegram_send  # bestehende v1-Sendefunktion, gleicher Bot


def _esc(text) -> str:
    """HTML-Escaping fuer dynamische Textbausteine - IMMER vor dem
    Zusammenbau anwenden, nicht erst am fertigen String (siehe
    Docstring, bekannter Bug in evaluate_signals.py). & zuerst
    escapen, sonst werden die eigenen &lt;/&gt;-Entities doppelt
    escaped."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def get_latest_signals() -> list[dict]:
    """ALLE Zeilen des neuesten Wochenlaufs - gleiche Auswahl wie in
    market_pulse_v2.py/quality_tiers_v2.py (live geprüft, konsistent
    3 Zeilen pro Lauf-ts, nicht nur is_top_signal=1)."""
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


def latest_market_pulse_by_pair() -> dict[str, dict]:
    rows = db_v2.query(
        "SELECT mp.pair, mp.einordnung, mp.ts FROM market_pulse_checks_v2 mp "
        "INNER JOIN (SELECT pair, MAX(ts) mts FROM market_pulse_checks_v2 GROUP BY pair) latest "
        "ON mp.pair = latest.pair AND mp.ts = latest.mts"
    )
    return {r["pair"]: dict(r) for r in rows}


def latest_gate_by_pair() -> dict[str, dict]:
    rows = db_v2.query(
        "SELECT e.pair, e.gate_passed, e.readiness, e.market_pulse_status, e.ts "
        "FROM entry_readiness_checks_v2 e "
        "INNER JOIN (SELECT pair, MAX(ts) mts FROM entry_readiness_checks_v2 GROUP BY pair) latest "
        "ON e.pair = latest.pair AND e.ts = latest.mts"
    )
    return {r["pair"]: dict(r) for r in rows}


def _zone_from_readiness(readiness: int | None) -> str | None:
    """Gleiche Zonen-Grenzen wie entry_gate_v2._zone() - hier separat
    gehalten, da report_v2.py NICHT von entry_gate_v2.py importiert
    (jedes v2-Modul bleibt für sich lauffähig, siehe Ordnerprinzip)."""
    if readiness is None:
        return None
    if readiness <= 1:
        return "beobachten"
    if readiness <= 3:
        return "setup_möglich"
    return "bestätigt"


# Klartext-Übersetzung der rohen Gate-Ablehnungsgründe (Fix: mobile Ansicht
# zeigte bisher den internen Code statt Klartext). EINE gemeinsame Quelle
# für report_v2.py (Telegram) UND publish_pages_v2.py (Website, importiert
# report_v2 bereits für build_rows()) - keine Duplikation. Fallback für
# unbekannte Codes: roh anzeigen statt zu crashen (siehe format_gate_text()).
GATE_GRUND_TEXT = {
    "market_pulse_nicht_bestaetigt": "Wird noch nicht vom Markt bestätigt",
    "hard_gate_h4_h1_gegen_bias": "H4/H1-Trend läuft aktuell gegen den Bias",
}


def format_gate_text(row: dict) -> str:
    """Menschenlesbarer Gate-Text aus den rohen build_rows()-Feldern -
    bestätigt-Fall unverändert, Ablehnungsgründe jetzt über
    GATE_GRUND_TEXT übersetzt statt als Rohcode gezeigt."""
    if row["gate_status"] == "noch_nicht_geprueft":
        return "noch nicht geprüft"
    if row["gate_status"] == "bestaetigt":
        return f"✅ bestätigt (Readiness {row['gate_readiness']}/4, Zone: {row['gate_zone']})"
    grund_text = GATE_GRUND_TEXT.get(row["gate_grund_code"], row["gate_grund_code"])
    return f"❌ {grund_text}"


def build_rows() -> list[dict]:
    signals = get_latest_signals()
    pulses = latest_market_pulse_by_pair()
    gates = latest_gate_by_pair()

    out = []
    for s in signals:
        pair = s["pair"]
        tier = qt.klassifiziere(s["final_quality"])

        pulse = pulses.get(pair)
        market_pulse_status = pulse["einordnung"] if pulse else "noch nicht geprüft"

        gate = gates.get(pair)
        if gate is None:
            gate_status, gate_passed = "noch_nicht_geprueft", None
            gate_readiness = gate_zone = gate_grund_code = None
        elif gate["gate_passed"]:
            gate_status, gate_passed = "bestaetigt", True
            gate_readiness = gate["readiness"]
            gate_zone = _zone_from_readiness(gate["readiness"])
            gate_grund_code = None
        else:
            gate_status, gate_passed = "abgelehnt", False
            gate_readiness = gate_zone = None
            # entry_readiness_checks_v2 speichert keine eigene "grund"-Spalte
            # (siehe CONTRACT.md) - der Ablehnungsgrund ist aber aus dem zum
            # Zeitpunkt der Gate-Prüfung GESPEICHERTEN market_pulse_status
            # eindeutig rekonstruierbar: entry_gate_v2.check_pair() bricht
            # NUR entweder beim market_pulse-Filter oder beim Hard-Gate ab
            # (M15/Momentum wird bei "bestätigt sich" + Hard-Gate-Fail nie
            # erreicht, also gibt es keinen dritten Fehlerfall hier).
            if gate["market_pulse_status"] != "bestätigt sich":
                gate_grund_code = "market_pulse_nicht_bestaetigt"
            else:
                gate_grund_code = "hard_gate_h4_h1_gegen_bias"

        out.append({
            "pair": pair, "bias": s["bias"], "engine": s["engine"],
            "final_quality": s["final_quality"], "tier": tier,
            "market_pulse_status": market_pulse_status,
            "gate_status": gate_status, "gate_passed": gate_passed,
            "gate_readiness": gate_readiness, "gate_zone": gate_zone,
            "gate_grund_code": gate_grund_code,
        })
    return out


def performance_summary_lines() -> list[str]:
    """Aktuelle Trefferquote Ebene 1/2 aus signal_performance_v2 (v2, NUR
    LESEND) - IMMER mit 'n=X, NICHT BELASTBAR (<30)'-Hinweis, solange
    n<30, auch bei n=1 (siehe evaluate_v2._fmt_result())."""
    rows = db_v2.query(
        "SELECT ebene1_ergebnis, ebene2_ergebnis FROM signal_performance_v2"
    )
    e1 = [r["ebene1_ergebnis"] for r in rows if r["ebene1_ergebnis"] is not None]
    e2 = [r["ebene2_ergebnis"] for r in rows if r["ebene2_ergebnis"] is not None]

    lines = ["", "<b>📈 Erfolgsauswertung (laufend)</b>"]
    if not e1:
        lines.append("  Ebene 1: noch keine ausgewerteten Fälle.")
    else:
        n1_win = sum(1 for x in e1 if x == "WIN")
        lines.append(f"  Ebene 1: {_esc(ev._fmt_result(n1_win, len(e1)))}")
    if not e2:
        lines.append("  Ebene 2: noch keine ausgewerteten Fälle.")
    else:
        n2_win = sum(1 for x in e2 if x == "WIN")
        lines.append(f"  Ebene 2: {_esc(ev._fmt_result(n2_win, len(e2)))}")
    return lines


def build_message(rows: list[dict]) -> str:
    lines = ["🧪 [5MG v2 TEST]", ""]
    for r in rows:
        lines.append(f"<b>{_esc(r['pair'])}</b> {_esc(r['bias'])} — {_esc(r['engine'])}")
        lines.append(f"  Tier: {_esc(r['tier'])} (final_quality {r['final_quality']:.1f})")
        lines.append(f"  Market Pulse: {_esc(r['market_pulse_status'])}")
        lines.append(f"  Gate: {_esc(format_gate_text(r))}")
        lines.append("")
    lines.extend(performance_summary_lines())
    return "\n".join(lines).rstrip()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--send", action="store_true",
                    help="Echter Telegram-Versand (Standard: dry-run, nur print)")
    args = p.parse_args()
    dry_run = not args.send  # dry-run ist STANDARD

    rows = build_rows()
    if not rows:
        print("Keine Signale in weekly_engine_signals gefunden.")
        return

    text = build_message(rows)

    print(f"{'DRY-RUN — kein Telegram-Versand' if dry_run else 'SENDET an TRADING_CHAT_ID'}\n")
    print("--- Nachrichtentext (roh, wie an Telegram gesendet würde) ---")
    print(text)
    print("--- Ende Nachrichtentext ---")

    if dry_run:
        return  # WICHTIG: dry-run-Check vor dem Telegram-Call, nicht danach

    import os
    chat_id = os.environ.get("TRADING_CHAT_ID")
    ok = telegram_send(text, chat_id=chat_id, parse_mode="HTML")
    print(f"\n{'✅ Gesendet.' if ok else '❌ Versand fehlgeschlagen — siehe Log oben.'}")


if __name__ == "__main__":
    main()
