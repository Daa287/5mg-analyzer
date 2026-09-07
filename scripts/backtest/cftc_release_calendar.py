#!/usr/bin/env python3
"""
cftc_release_calendar.py — 5MG Backtest, CFTC-Veröffentlichungsdatum je Report.

NUR für die geplante Backtest-Orchestrierung, NICHT für die bestehende
Live-Pipeline (cot_loader.py/cot_engines.py bleiben unverändert).

Problem: cot_loader.load_history() liefert pro Report nur den Positions-
Stichtag ("As of Date", stets ein Dienstag) — CFTC veröffentlicht diesen
Report aber erst ca. 3 Tage später (Freitag 15:30 ET). Ein Backtest, der
naiv `date <= cutoff_date` auf den Stichtag filtert, hätte an jedem
Report-Zeitpunkt einen ~3-Tage-Lookahead-Bias (der simulierte "Stand von
heute" würde COT-Daten sehen, die zu diesem Zeitpunkt real noch gar nicht
veröffentlicht waren).

Normalfall: Veröffentlichung = Stichtag + 3 Tage (Freitag).

Ausnahmen (Feiertage) — zwei verschiedene, von CFTC selbst so
dokumentierte Muster (offizieller Kalender, siehe CFTC_RELEASE_EXCEPTIONS
unten für die Quellenangaben):
  - "release_delayed": Veröffentlichung verschiebt sich vom Freitag auf
    den folgenden Montag (Stichtag + 6 Tage) - z.B. wenn der reguläre
    Freitag selbst ein Feiertag ist.
  - "monday_position_substitution": der Stichtag selbst ist AUSNAHMSWEISE
    ein Montag statt Dienstag (weil der reguläre Dienstag ein Feiertag
    war und CFTC stattdessen Montags-Positionsdaten verwendet hat) -
    Veröffentlichung bleibt dabei auf dem normalen Freitag (Stichtag +
    4 Tage). Betrifft nur 2 Wochen im gesamten geprüften Zeitraum
    (03.07.2023 vor Independence Day, 10.11.2025 vor Veterans Day).

Herleitung (07.09.2026): CFTC veröffentlicht online NUR den Kalender
für das laufende + kommende Jahr (cftc.gov/.../ReleaseSchedule/index.htm,
"?year="-Parameter wird ignoriert). Für 2021-2025 wurden archivierte
Snapshots derselben Seite über web.archive.org abgerufen (Timestamps:
2022-03-16, 2023-03-05, 2024-02-22, 2025-03-09) und die dort jeweils
angezeigten Jahres-/Vorjahres-Tabellen ausgewertet. Alle 23 Ausnahmen
wurden gegen eine durchgehende 7-Tage-Dienstags-Sequenz über den
gesamten Zeitraum 2021-12 bis 2026-12 validiert (keine Inkonsistenz
außer den zwei erwarteten, durch die Montags-Substitution verursachten
Einzelwochen-Verschiebungen) - siehe Chat-Verlauf 07.09.2026 für die
vollständige Herleitungs-Rechnung.

    python3 cftc_release_calendar.py   # Selbsttest: 3 Beispielwochen
"""
from __future__ import annotations

from datetime import date, timedelta

STANDARD_DELAY_DAYS = 3  # Dienstag -> Freitag

# Key: Positions-Stichtag (ISO), Value: (Veröffentlichungsdatum ISO, Verzug in Tagen, Typ)
CFTC_RELEASE_EXCEPTIONS: dict[str, dict] = {
    "2021-12-21": {"release": "2021-12-27", "delay_days": 6, "type": "release_delayed"},
    "2021-12-28": {"release": "2022-01-03", "delay_days": 6, "type": "release_delayed"},
    "2022-11-08": {"release": "2022-11-14", "delay_days": 6, "type": "release_delayed"},
    "2022-11-22": {"release": "2022-11-28", "delay_days": 6, "type": "release_delayed"},
    "2023-07-03": {"release": "2023-07-07", "delay_days": 4, "type": "monday_position_substitution"},
    "2023-11-07": {"release": "2023-11-13", "delay_days": 6, "type": "release_delayed"},
    "2023-11-21": {"release": "2023-11-27", "delay_days": 6, "type": "release_delayed"},
    "2024-06-18": {"release": "2024-06-24", "delay_days": 6, "type": "release_delayed"},
    "2024-07-02": {"release": "2024-07-08", "delay_days": 6, "type": "release_delayed"},
    "2024-11-26": {"release": "2024-12-02", "delay_days": 6, "type": "release_delayed"},
    "2024-12-24": {"release": "2024-12-30", "delay_days": 6, "type": "release_delayed"},
    "2024-12-31": {"release": "2025-01-06", "delay_days": 6, "type": "release_delayed"},
    "2025-06-17": {"release": "2025-06-23", "delay_days": 6, "type": "release_delayed"},
    "2025-07-01": {"release": "2025-07-07", "delay_days": 6, "type": "release_delayed"},
    "2025-11-10": {"release": "2025-11-14", "delay_days": 4, "type": "monday_position_substitution"},
    "2025-11-25": {"release": "2025-12-01", "delay_days": 6, "type": "release_delayed"},
    "2025-12-23": {"release": "2025-12-29", "delay_days": 6, "type": "release_delayed"},
    "2025-12-30": {"release": "2026-01-05", "delay_days": 6, "type": "release_delayed"},
    "2026-06-16": {"release": "2026-06-22", "delay_days": 6, "type": "release_delayed"},
    "2026-06-30": {"release": "2026-07-06", "delay_days": 6, "type": "release_delayed"},
    "2026-11-10": {"release": "2026-11-16", "delay_days": 6, "type": "release_delayed"},
    "2026-11-24": {"release": "2026-11-30", "delay_days": 6, "type": "release_delayed"},
    "2026-12-22": {"release": "2026-12-28", "delay_days": 6, "type": "release_delayed"},
}


def veroeffentlichungsdatum(positions_stichtag: date) -> date:
    """Reales Veröffentlichungsdatum eines COT-Reports für den gegebenen
    Positions-Stichtag (aus cot_loader-Zeilen: row['date'], i.d.R. ein
    Dienstag). Normalfall: +3 Tage (Freitag). Bekannte Ausnahmen (siehe
    CFTC_RELEASE_EXCEPTIONS) werden exakt nachgeschlagen, nicht
    approximiert."""
    key = positions_stichtag.isoformat()
    exc = CFTC_RELEASE_EXCEPTIONS.get(key)
    if exc:
        return date.fromisoformat(exc["release"])
    return positions_stichtag + timedelta(days=STANDARD_DELAY_DAYS)


if __name__ == "__main__":
    testfaelle = [
        (date(2026, 9, 1), "Normalfall (September 2026, keine Ausnahme)"),
        (date(2026, 11, 10), "Ausnahme release_delayed (Veterans-Day-Woche 2026)"),
        (date(2023, 7, 3), "Ausnahme monday_position_substitution (Independence Day 2023)"),
    ]
    print("Selbsttest cftc_release_calendar.py\n")
    for stichtag, label in testfaelle:
        veroeff = veroeffentlichungsdatum(stichtag)
        print(f"{label}")
        print(f"  Stichtag:        {stichtag} ({stichtag.strftime('%A')})")
        print(f"  Veröffentlicht:  {veroeff} ({veroeff.strftime('%A')})")
        print()
