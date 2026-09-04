#!/usr/bin/env python3
"""
quality_tiers_v2.py — 5MG v2, Phase 3.

Reine Zusatz-Klassifikation, KEIN Filter, KEIN Formel-Nachbau. Liest
final_quality-Werte aus weekly_engine_signals (v1-Tabelle, CONTRACT.md
bestätigt: final_quality REAL) - NUR LESEND, kein Write auf v1- ODER
v2-Tabellen (diese Datei schreibt nirgendwo hin, sie ist eine reine
Klassifikationshilfe für report_v2.py, Schritt 6).

Tier-Schwellen (>=85 "A+", >=65 "B", sonst "C") sind EIGENE, transparent
gewählte Annahmen - KEIN Versuch, die Original-Schwellen aus dem
TradingWelt-Screenshot zurückzurechnen. Dort wurden nur 3 Beispielwerte
beobachtet (92.1/65.2/59.6) - das reicht nicht für eine belastbare
Schwellenableitung (analog zur bereits dokumentierten Lehre bei den
Original-Zonengrenzen 44/45-74/75 in entry_gate_v2.py: eigene, klar
gekennzeichnete Werte statt einer unbelegten Rekonstruktion).

    python3 quality_tiers_v2.py   # Testlauf, reine Konsolenausgabe
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path("/home/pi/hermes2/scripts")
sys.path.insert(0, str(SCRIPTS_DIR))
import db  # v1, NUR LESEND (weekly_engine_signals)

# Eigene, transparent gewählte Schwellen - siehe Docstring.
TIER_A_PLUS_MIN = 85.0
TIER_B_MIN = 65.0


def klassifiziere(final_quality: float) -> str:
    """Gibt den Tier-String für einen final_quality-Wert zurück.
    >=85 -> 'A+', >=65 -> 'B', sonst 'C'. Eigene Schwellen, kein
    Original-Nachbau (siehe Docstring)."""
    if final_quality >= TIER_A_PLUS_MIN:
        return "A+"
    if final_quality >= TIER_B_MIN:
        return "B"
    return "C"


def get_latest_signals_with_tier() -> list[dict]:
    """ALLE Zeilen des neuesten Wochenlaufs aus weekly_engine_signals
    (gleiche Auswahl wie market_pulse_v2.get_latest_signals() - live
    gegen die DB geprüft: konsistent 3 Zeilen pro Lauf-ts, nicht nur
    is_top_signal=1), je um den Tier ergänzt."""
    row = db.query("SELECT MAX(ts) t FROM weekly_engine_signals")
    latest_ts = row[0]["t"] if row else None
    if not latest_ts:
        return []
    rows = db.query(
        "SELECT engine, pair, bias, final_quality, ts FROM weekly_engine_signals "
        "WHERE ts=? ORDER BY final_quality DESC",
        (latest_ts,),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["tier"] = klassifiziere(d["final_quality"])
        out.append(d)
    return out


def main() -> None:
    signals = get_latest_signals_with_tier()
    if not signals:
        print("Keine Signale in weekly_engine_signals gefunden.")
        return

    print(f"Quality Tiers v2 — {len(signals)} Signal(e) aus weekly_engine_signals "
          f"(ts={signals[0]['ts']})\n")
    print(f"Schwellen (eigene Wahl, kein Original-Wert): "
          f">= {TIER_A_PLUS_MIN:.0f} -> A+, >= {TIER_B_MIN:.0f} -> B, sonst C\n")

    header = f"{'Pair':<10} {'Engine':<14} {'final_quality':>13}  Tier"
    print(header)
    print("-" * len(header))
    for s in signals:
        print(f"{s['pair']:<10} {s['engine']:<14} {s['final_quality']:>13.1f}  {s['tier']}")


if __name__ == "__main__":
    main()
