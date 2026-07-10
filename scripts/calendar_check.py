"""
calendar_check.py — High Impact Events der nächsten 14 Tage

ANSCHLUSSSTELLE für euren bestehenden ForexFactory-Ansatz aus
morning_briefing.py. Wenn dort schon eine Funktion existiert, die
High-Impact-Termine pro Währung liefert (z.B. get_high_impact_events(ccy)),
hier einfach importieren und in high_impact_for() aufrufen statt der
DUMMY-Rückgabe.

Solange das nicht verdrahtet ist, liefert dieses Modul plausible
Platzhalter-Termine, damit der Rest der Pipeline (Vola-Score, Eventlage)
sofort lauffähig und testbar ist.

Erwartetes Rückgabeformat pro Währung: Liste von
    {"dt": "YYYY-MM-DD HH:MM", "txt": "CCY: Beschreibung", "impact": "high"}
"""

from __future__ import annotations
from datetime import datetime, timedelta

# --- Hier eure echte Quelle einhängen, sobald vorhanden -------------------
try:
    from morning_briefing import get_high_impact_events as _real_source  # type: ignore
except ImportError:
    _real_source = None
# ---------------------------------------------------------------------------

DUMMY_EVENTS = {
    "USD": [{"dt_offset_days": 0, "hh": 15, "mm": 0, "txt": "USD: Fed Monetary Policy Report"}],
    "CAD": [
        {"dt_offset_days": 0, "hh": 12, "mm": 30, "txt": "CAD: Arbeitslosenquote"},
        {"dt_offset_days": 0, "hh": 12, "mm": 30, "txt": "CAD: Employment Change"},
    ],
}


def _dummy_events_for(ccy: str) -> list[dict]:
    now = datetime.now()
    out = []
    for e in DUMMY_EVENTS.get(ccy, []):
        dt = now.replace(hour=e["hh"], minute=e["mm"], second=0, microsecond=0) \
            + timedelta(days=e["dt_offset_days"])
        out.append({"dt": dt.strftime("%Y-%m-%d %H:%M"), "txt": e["txt"], "impact": "high"})
    return out


def high_impact_for(ccy: str, tage: int = 14) -> list[dict]:
    if _real_source is not None:
        try:
            return _real_source(ccy, tage)
        except Exception:
            pass  # fällt auf Dummy zurück statt die ganze Pipeline zu blockieren
    return _dummy_events_for(ccy)


def event_summary(base_ccy: str, quote_ccy: str, tage: int = 14) -> dict:
    """
    Aggregiert Events beider Währungen eines Paars zu Vola-Score + Eventlage.
    Score-Logik (aus Screenshots abgeleitet):
        1 High-Impact-Termin  -> Vola 3, BEOBACHTEN
        2+ High-Impact-Termine-> Vola 6, ERHÖHT
        0 Termine             -> Vola 0, RUHIG
    """
    events = high_impact_for(base_ccy, tage) + high_impact_for(quote_ccy, tage)
    events.sort(key=lambda e: e["dt"])
    n = len(events)

    if n == 0:
        vola, lage = 0, "RUHIG"
    elif n == 1:
        vola, lage = 3, "BEOBACHTEN"
    else:
        vola, lage = 6, "ERHÖHT"

    gezeigt = events[:2]
    weitere = max(0, n - len(gezeigt))
    return {"vola": vola, "lage": lage, "termine": gezeigt, "weitere": weitere}


if __name__ == "__main__":
    for base, quote in [("USD", "CHF"), ("USD", "CAD"), ("NZD", "USD")]:
        print(f"{base}/{quote} ->", event_summary(base, quote))
