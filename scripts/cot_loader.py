"""
cot_loader.py — CFTC Legacy Report laden + COT-Index berechnen

Datenquelle: CFTC "Futures Only" Legacy Report (Non-Commercials).
  https://www.cftc.gov/files/dea/history/deacot{YEAR}.zip

Lokaler Cache unter ~/hermes2/data/cot_cache/ — ein CSV pro Jahr.
Nur neu laden, wenn der Report seit letztem Cache-Stand aktualisiert wurde
(CFTC published freitags ca. 15:30 ET für den Dienstag davor).

Getestet werden muss auf hermes2 selbst — von der Entwicklungsumgebung aus
war cftc.gov nicht erreichbar (Netzwerk-Whitelist). Vor Produktivbetrieb:
  python3 cot_loader.py --selftest
laufen lassen und die CHF/NZD/CAD-Werte gegen den Screenshot-Referenzwert
(Report 2026-06-30: CHF~89, NZD~84, CAD~84) prüfen.
"""

from __future__ import annotations
import csv
import io
import json
import zipfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# CFTC-Kontraktnamen -> unsere Kürzel (Legacy "Futures Only" Report)
CONTRACT_MAP = {
    "EURO FX - CHICAGO MERCANTILE EXCHANGE": "EUR",
    "BRITISH POUND STERLING - CHICAGO MERCANTILE EXCHANGE": "GBP",
    "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE": "JPY",
    "SWISS FRANC - CHICAGO MERCANTILE EXCHANGE": "CHF",
    "CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE": "CAD",
    "AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE": "AUD",
    "NZ DOLLAR - CHICAGO MERCANTILE EXCHANGE": "NZD",
    "MEXICAN PESO - CHICAGO MERCANTILE EXCHANGE": "MXN",
    "GOLD - COMMODITY EXCHANGE INC.": "GOLD",
    "SILVER - COMMODITY EXCHANGE INC.": "SILVER",
    "NASDAQ-100 STOCK INDEX (MINI) - CHICAGO MERCANTILE EXCHANGE": "NASDAQ100",
    "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE": "SP500",
}

DEFAULT_CACHE_DIR = Path.home() / "hermes2" / "data" / "cot_cache"
LOOKBACK_WEEKS = 26  # Standard COT-Index Fenster; bei Bedarf auf 156 (3J) testen


def _cache_dir() -> Path:
    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_CACHE_DIR


def download_year(year: int, force: bool = False) -> Path:
    """Lädt/aktualisiert den Jahres-Report als CSV im lokalen Cache."""
    dest = _cache_dir() / f"deacot{year}.csv"
    if dest.exists() and not force:
        return dest
    url = f"https://www.cftc.gov/files/dea/history/deacot{year}.zip"
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = zf.namelist()[0]
        data = zf.read(name)
    dest.write_bytes(data)
    return dest


def _read_rows(csv_path: Path):
    with csv_path.open("r", encoding="latin-1") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def load_history(years: list[int]) -> dict[str, list[dict]]:
    """
    Liefert {kürzel: [ {date, long, short, oi}, ... ]} sortiert nach Datum,
    über alle angegebenen Jahre zusammengeführt.
    """
    series: dict[str, list[dict]] = {v: [] for v in CONTRACT_MAP.values()}
    for year in years:
        path = download_year(year)
        for row in _read_rows(path):
            name = row.get("Market and Exchange Names", "").strip()
            key = CONTRACT_MAP.get(name)
            if not key:
                continue
            try:
                date = datetime.strptime(row["As of Date in Form YYYY-MM-DD"], "%Y-%m-%d")
                long_ = float(row["Noncommercial Positions-Long (All)"])
                short_ = float(row["Noncommercial Positions-Short (All)"])
                oi = float(row["Open Interest (All)"])
            except (KeyError, ValueError):
                continue
            series[key].append({"date": date, "long": long_, "short": short_, "oi": oi})
    for key in series:
        series[key].sort(key=lambda r: r["date"])
    return series


def cot_index(rows: list[dict], lookback_weeks: int = LOOKBACK_WEEKS) -> float | None:
    """
    Klassischer COT-Index: Perzentil der aktuellen Netto-Position
    (Long - Short) innerhalb der letzten N Wochen. 0-100.
    """
    if len(rows) < 2:
        return None
    window = rows[-lookback_weeks:] if len(rows) > lookback_weeks else rows
    nets = [r["long"] - r["short"] for r in window]
    current = nets[-1]
    lo, hi = min(nets), max(nets)
    if hi == lo:
        return 50.0
    return round((current - lo) / (hi - lo) * 100, 1)


def latest_scores(years: list[int], lookback_weeks: int = LOOKBACK_WEEKS) -> dict:
    """
    Hauptfunktion für den Export: liefert pro Kürzel Score + Report-Datum.
    """
    series = load_history(years)
    out = {}
    report_date = None
    for key, rows in series.items():
        if not rows:
            continue
        idx = cot_index(rows, lookback_weeks)
        out[key] = {"score": idx, "net": rows[-1]["long"] - rows[-1]["short"]}
        report_date = rows[-1]["date"].strftime("%Y-%m-%d")
    return {"report_date": report_date, "scores": out, "markets": len(out)}


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        this_year = datetime.now(timezone.utc).year
        result = latest_scores([this_year - 1, this_year])
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print("\nErwartung ggü. Screenshot (Report 2026-06-30):")
        print("  CHF ~ 89   NZD ~ 84   CAD ~ 84")
        for k in ("CHF", "NZD", "CAD"):
            got = result["scores"].get(k, {}).get("score")
            print(f"  {k}: erhalten = {got}")
    else:
        print("Nutzung: python3 cot_loader.py --selftest")
