#!/usr/bin/env python3
"""
systemcheck_5mg.py — Beweis, was in der 5MG-Analyzer-Pipeline gerade
wirklich funktioniert. Analog zu ~/hermes2/scripts/systemcheck.py.

Ruft jede Stufe einzeln mit echten (oder Fallback-)Daten auf und zeigt
✅ / ❌ statt Vermutung.
"""

from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def check(name: str, fn):
    try:
        result = fn()
        print(f"✅ {name}: {result}")
        return True
    except Exception as e:
        print(f"❌ {name}: {type(e).__name__}: {e}")
        return False


def main():
    results = []

    def cot_check():
        import cot_loader
        from datetime import datetime, timezone
        y = datetime.now(timezone.utc).year
        r = cot_loader.latest_scores([y - 1, y])
        assert r["markets"] > 0, "keine Märkte geladen"
        return f"{r['markets']} Märkte, Report {r['report_date']}"

    def bond_check():
        import bond_loader
        bond_loader.ensure_config_exists()
        y = bond_loader.load_yields()
        assert "USD" in y and "CHF" in y
        return f"USD={y['USD']} CHF={y['CHF']}"

    def season_check():
        import season_engine
        r = season_engine.season_score("USD/CHF", "SHORT")
        assert "score" in r
        return r

    def calendar_check_fn():
        import calendar_check
        r = calendar_check.event_summary("USD", "CHF")
        assert "vola" in r
        return r

    def export_check():
        import watchlist_export
        payload = watchlist_export.run()
        assert len(payload["setups"]) > 0, "keine Setups erzeugt"
        return f"{len(payload['setups'])} Setups, {len(payload['zusatz'])} Zusatzmärkte"

    results.append(check("COT-Loader (CFTC live)", cot_check))
    results.append(check("Bond-Loader (Config)", bond_check))
    results.append(check("Season-Engine (yfinance)", season_check))
    results.append(check("Calendar-Check (Events)", calendar_check_fn))
    results.append(check("Watchlist-Export (Gesamtpipeline)", export_check))

    print()
    ok = sum(results)
    print(f"{ok}/{len(results)} Checks OK")
    if ok < len(results):
        print("Hinweis: Einzelne Fehler blockieren NICHT zwingend den Export "
              "(Fallbacks in bond_loader/calendar_check greifen), aber COT- und "
              "Season-Fehler sollten vor dem produktiven Cron-Lauf behoben werden.")


if __name__ == "__main__":
    main()
