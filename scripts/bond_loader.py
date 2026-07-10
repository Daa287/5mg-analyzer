"""
bond_loader.py — 10-Jahres-Staatsanleihenrenditen

Start bewusst als manuell gepflegte Config statt Live-Scrape:
worldgovernmentbonds.com & Co. ändern häufig ihr HTML, das würde die
wöchentliche Auswertung unnötig fragil machen. bonds_config.json liegt
in ~/hermes2/data/ und wird 1x pro Woche in ~20 Sekunden von Hand
aktualisiert (Werte z.B. von finanzen.net, investing.com oder direkt
den Notenbank-Seiten).

Spätere Ausbaustufe (optional): FRED-API für US (DGS10, kostenlos, Key
nötig) plus gezielte Scrapes für CHF/CAD/NZD/AUD — dann bond_loader.py
einfach so erweitern, dass live_source() vor der Config gecheckt wird.

CONFIG-Format (bonds_config.json):
{
  "updated": "2026-07-10",
  "yields": { "USD": 4.57, "CHF": 0.38, "CAD": 3.57, "NZD": 4.60,
              "EUR": 2.61, "GBP": 4.42, "JPY": 1.15, "AUD": 4.31 }
}
"""

from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

DEFAULT_CONFIG = Path.home() / "hermes2" / "data" / "bonds_config.json"

# Startwerte = aus den Screenshots bekannte Referenzpunkte, damit das
# System sofort lauffähig ist, bevor die Config einmal gepflegt wurde.
FALLBACK_YIELDS = {
    "USD": 4.57, "CHF": 0.38, "CAD": 3.57, "NZD": 4.60,
    "EUR": 2.61, "GBP": 4.42, "JPY": 1.15, "AUD": 4.31,
}


def load_yields(config_path: Path = DEFAULT_CONFIG) -> dict:
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            return data.get("yields", FALLBACK_YIELDS)
        except (json.JSONDecodeError, OSError):
            pass
    return FALLBACK_YIELDS


def ensure_config_exists(config_path: Path = DEFAULT_CONFIG) -> None:
    """Legt beim ersten Lauf eine Startdatei an, falls noch keine existiert."""
    if config_path.exists():
        return
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "yields": FALLBACK_YIELDS,
        "hinweis": "Wöchentlich von Hand aktualisieren, z.B. via investing.com Rates & Bonds"
    }, indent=2, ensure_ascii=False), encoding="utf-8")


NEUTRAL_BAND = 0.25  # Prozentpunkte; |spread| darunter => kein Rueckenwind



# --- Live-Abruf über FRED (optional, automatisch mit Fallback) -----------
FRED_SERIES = {
    "USD": "DGS10", "CHF": "IRLTLT01CHM156N", "CAD": "IRLTLT01CAM156N",
    "NZD": "IRLTLT01NZM156N", "GBP": "IRLTLT01GBM156N", "JPY": "IRLTLT01JPM156N",
    "AUD": "IRLTLT01AUM156N", "EUR": "IRLTLT01EZM156N",
}

def _fred_latest(series_id, api_key):
    import urllib.request as _ur, json as _json
    url = (f"https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={series_id}&api_key={api_key}&file_type=json&sort_order=desc&limit=5")
    with _ur.urlopen(url, timeout=15) as resp:
        data = _json.loads(resp.read())
    for obs in data.get("observations", []):
        val = obs.get("value")
        if val not in (None, ".", ""):
            return float(val)
    return None

def fetch_live_yields(api_key=None):
    if api_key is None:
        import os
        api_key = os.environ.get("FRED_API_KEY")
    current = load_yields()
    if not api_key:
        return current
    updated = dict(current)
    for ccy, series in FRED_SERIES.items():
        try:
            val = _fred_latest(series, api_key)
            if val is not None:
                updated[ccy] = round(val, 2)
        except Exception:
            continue
    return updated

def refresh_config(config_path=DEFAULT_CONFIG):
    from datetime import datetime, timezone
    yields = fetch_live_yields()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "yields": yields,
        "hinweis": "Automatisch via FRED aktualisiert, sofern FRED_API_KEY gesetzt ist."
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return yields

def bond_score(base_ccy: str, quote_ccy: str, richtung: str, yields: dict) -> dict:
    """
    Spread = Basiswährung - Gegenwährung. Positiver Spread stützt die Basis.
    richtung: "LONG" heisst, die Basiswährung (erster Teil des Paars) soll steigen.
    """
    y_base = yields.get(base_ccy)
    y_quote = yields.get(quote_ccy)
    if y_base is None or y_quote is None:
        return {"spread": None, "score": 50.0, "expl": "Renditedaten unvollständig"}

    spread = round(y_base - y_quote, 2)
    stuetzt_basis = spread > NEUTRAL_BAND
    stuetzt_quote = spread < -NEUTRAL_BAND
    bias_will_basis_staerker = (richtung == "LONG")

    if not stuetzt_basis and not stuetzt_quote:
        score = 50.0
        expl = (f"Die 10-Jahres-Renditen von {base_ccy} und {quote_ccy} liegen "
                f"nahezu gleichauf. Aus Bond-Sicht entsteht aktuell kein klarer Rückenwind.")
    elif stuetzt_basis == bias_will_basis_staerker:
        score = 95.0
        expl = f"Der Renditevorteil stützt den {richtung}-Bias in {base_ccy}/{quote_ccy}."
    else:
        score = 5.0
        stuetzt = base_ccy if stuetzt_basis else quote_ccy
        expl = (f"Der Renditevorteil liegt aktuell auf Seiten von {stuetzt}. Das liefert "
                f"makroökonomischen Gegenwind zum {richtung}-Bias in {base_ccy}/{quote_ccy}.")

    return {"spread": spread, "score": score, "expl": expl,
            "y_base": y_base, "y_quote": y_quote}


if __name__ == "__main__":
    ensure_config_exists()
    y = load_yields()
    print("Aktuelle Renditen:", y)
    for base, quote, dirn in [("USD", "CHF", "SHORT"), ("USD", "CAD", "SHORT"), ("NZD", "USD", "LONG")]:
        print(f"{base}/{quote} {dirn} ->", bond_score(base, quote, dirn, y))
