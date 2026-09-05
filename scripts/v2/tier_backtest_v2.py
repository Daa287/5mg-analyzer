#!/usr/bin/env python3
"""
tier_backtest_v2.py — 5MG v2, rückwirkende Tier-Simulation auf v1-Daten.

⚠️ KEIN echter v2-Test: v2s Market-Pulse-/Entry-Gate-Logik lief zu den
historischen Zeitpunkten dieser v1-Signale nicht mit. Dieses Skript wendet
NUR die reine Tier-Klassifikationslogik aus quality_tiers_v2.klassifiziere()
(>=85 A+, >=65 B, sonst C) nachträglich auf bereits vorhandene
final_quality-Werte aus weekly_engine_signals an und verknüpft das Ergebnis
mit den bereits vorhandenen v1-Auswertungen aus signal_performance
(WIN/LOSS je Ebene 1 = Signal-Zeitpunkt / Ebene 2 = Erster Entry).

Reine Lese-Auswertung. KEIN Import von evaluate_signals.py (das würde einen
neuen Auswertungslauf/Schreibzugriff ermöglichen, hier nicht gewollt) -
eigener, minimaler Read-Only-Join direkt gegen die DB. KEIN Schreibzugriff
auf v1- oder v2-Tabellen, KEINE Änderung an bestehenden Dateien/Cron-Jobs.

Die echte v2-Auswertung (evaluate_v2.py, erste Ebene-1-Ergebnisse ab ca.
11.09.2026) läuft unverändert parallel weiter - dieser Zwischenstand
ersetzt sie nicht, sondern ist nur eine grobe Tendenz-Einschätzung auf
Basis der bestehenden v1-Historie.

    python3 tier_backtest_v2.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path("/home/pi/hermes2/scripts")
sys.path.insert(0, str(SCRIPTS_DIR))
import db  # v1, NUR LESEND

V2_DIR = Path("/home/pi/hermes2/scripts/5mg_analyzer_repo/scripts/v2")
sys.path.insert(0, str(V2_DIR))
from quality_tiers_v2 import klassifiziere  # dieselben Schwellen wie im echten v2-System

MIN_STICHPROBE = 30    # Projektweite Konvention (siehe evaluate_signals.py)
MIN_FUER_PROZENT = 10

REPORT_PATH = Path.home() / "hermes2" / "reports" / f"tier_backtest_v2_{datetime.now().strftime('%Y-%m-%d')}.md"


def load_joined_rows() -> list[dict]:
    """Reiner Read-Only-Join, KEIN Import aus evaluate_signals.py (das
    würde einen echten Auswertungslauf ermöglichen)."""
    rows = db.query(
        """SELECT sp.sig_status, sp.sig_direction_correct,
                  sp.entry_status, sp.entry_direction_correct,
                  ws.final_quality, ws.pair, ws.bias, ws.engine
           FROM signal_performance sp
           JOIN weekly_engine_signals ws ON sp.weekly_signal_id = ws.id
           WHERE ws.final_quality IS NOT NULL"""
    )
    out = []
    for r in rows:
        d = dict(r)
        d["tier"] = klassifiziere(d["final_quality"])
        out.append(d)
    return out


def _fmt_pct(n_correct: int, n_total: int) -> str:
    """1:1 dieselbe Formatierung/Schwellenlogik wie evaluate_signals._fmt_pct()."""
    if n_total == 0:
        return "keine abgeschlossenen Fälle"
    if n_total < MIN_FUER_PROZENT:
        return f"{n_correct} von {n_total} (absolute Zahl, kein Prozent bei n unter {MIN_FUER_PROZENT})"
    pct = 100 * n_correct / n_total
    suffix = ", NICHT BELASTBAR" if n_total < MIN_STICHPROBE else ""
    return f"{n_correct}/{n_total} ({pct:.1f}%{suffix})"


def _tier_stats(rows: list[dict], tier: str) -> dict:
    tier_rows = [r for r in rows if r["tier"] == tier]

    l1_done = [r for r in tier_rows if r["sig_status"] == "DONE"]
    l1_correct = sum(1 for r in l1_done if r["sig_direction_correct"] == 1)

    l2_done = [r for r in tier_rows if r["entry_status"] == "DONE"]
    l2_correct = sum(1 for r in l2_done if r["entry_direction_correct"] == 1)

    return {
        "tier": tier,
        "n_gesamt": len(tier_rows),
        "l1_n": len(l1_done), "l1_correct": l1_correct,
        "l2_n": len(l2_done), "l2_correct": l2_correct,
    }


def main() -> None:
    rows = load_joined_rows()
    if not rows:
        print("Keine verwertbaren v1-Zeilen (final_quality + signal_performance) gefunden.")
        return

    stats = [_tier_stats(rows, t) for t in ("A+", "B", "C")]

    lines = [
        "# Rückwirkende Tier-Simulation auf v1-Daten (5MG v2)",
        "",
        f"Erstellt: {datetime.now().strftime('%Y-%m-%d %H:%M')} · Grundgesamtheit: {len(rows)} v1-Signale "
        "mit final_quality + signal_performance-Zeile",
        "",
        "⚠️ **Rückwirkende Tier-Simulation auf v1-Daten — KEIN echter v2-Test, nur Tendenz.** "
        "v2s Market-Pulse-/Entry-Gate-Logik lief zu diesen historischen Zeitpunkten nicht mit. "
        "Nur die reine Klassifikationsschwelle (>=85 A+, >=65 B, sonst C) wurde nachträglich auf "
        "bestehende final_quality-Werte angewendet.",
        "",
        "| Tier | n gesamt | Ebene 1 (Signal-Zeitpunkt) | Ebene 2 (Erster Entry) |",
        "|---|---|---|---|",
    ]
    for s in stats:
        l1 = _fmt_pct(s["l1_correct"], s["l1_n"])
        l2 = _fmt_pct(s["l2_correct"], s["l2_n"])
        lines.append(f"| {s['tier']} | {s['n_gesamt']} | {l1} | {l2} |")

    lines += [
        "",
        f"(Schwellen wie in quality_tiers_v2.py: >= 85 A+, >= 65 B, sonst C — eigene, transparent "
        f"gewählte Werte, kein Original-Nachbau.)",
        "",
        "## Einordnung",
        "",
        f"- Alle n-Werte liegen deutlich unter der Stichproben-Schwelle ({MIN_STICHPROBE}) — "
        "jede Aussage hier ist eine erste Tendenz, kein belastbares Ergebnis.",
        "- Ebene 1 und Ebene 2 sind wie im v1-System getrennt zu betrachten, nicht zu mischen "
        "(unterschiedliche Fallzahlen, unterschiedliche Bedeutung).",
        "- Die echte v2-Auswertung (evaluate_v2.py) läuft unverändert weiter — erste eigene "
        "Ebene-1-Ergebnisse ab ca. 11.09.2026. Dieser Zwischenstand ersetzt das nicht.",
    ]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    print(f"Report geschrieben: {REPORT_PATH}\n")
    print(f"{'Tier':<5}{'n':>5}  {'Ebene 1':<28} {'Ebene 2':<28}")
    for s in stats:
        l1 = _fmt_pct(s["l1_correct"], s["l1_n"])
        l2 = _fmt_pct(s["l2_correct"], s["l2_n"])
        print(f"{s['tier']:<5}{s['n_gesamt']:>5}  {l1:<28} {l2:<28}")


if __name__ == "__main__":
    main()
