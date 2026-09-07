#!/usr/bin/env python3
"""
yield_history.py — 5MG Backtest, historische 2Y/10Y-Zinszeitreihen.

NUR für die Backtest-Orchestrierung, NICHT für die Live-Pipeline
(bond_loader.py bleibt unverändert - dessen _xxx_latest()-Funktionen
liefern bewusst nur den aktuellen Wert für den Live-Betrieb).

Alle acht Quellen wurden am 07.09.2026 einzeln live gegen echte
Datumsbereichs-Parameter getestet (siehe Chat-Verlauf):
  FRED (USD, + Fallback fuer GBP/JPY/AUD/NZD/EUR ueber Langfrist-Serien):
      observation_start/observation_end
  ECB (EUR):     startPeriod/endPeriod
  SNB (CHF):     fromDate/toDate
  BoC (CAD):     start_date/end_date (Live-Code nutzt recent=1 - hier NICHT)
  BoE (GBP):     volle Historie steckt bereits im ZIP, aber auf mehrere
                 Jahres-Bloecke verteilt (u.a. "2016 to 2024" + "2025 to
                 present") - Live-Code liest nur die "present"-Datei
  MOF (JPY):     eigener Archiv-Endpunkt .../historical/jgbcme_all.csv
                 (Live-Code nutzt jgbcme.csv = nur laufender Monat)
  RBA (AUD):     volle Historie (ab 2013) steckt bereits in der einen
                 abgerufenen CSV-Datei - Live-Code nimmt nur die letzte Zeile
  RBNZ (NZD):    volle Historie (ab 2018) steckt bereits in der einen
                 abgerufenen XLSX-Datei - Live-Code nimmt nur die letzte Zeile

MXN hat KEINE Zinsquelle (auch im Live-System nicht, siehe
weekly_engine_report.py-Docstring "FEHLENDE ZINSDATEN (z.B. MXN...)") -
im Backtest identisch: Macro-Komponente faellt fuer MXN-Wochen auf
neutral zurueck, wie im Live-Betrieb auch.

Lokaler Cache (JSON) unter ~/hermes2/data/backtest_yield_history_cache.json
- verhindert wiederholte grosse Downloads (v.a. BoE 37 MB, RBA/RBNZ) bei
  mehreren Testlaeufen. --refresh erzwingt Neuabruf.

    python3 yield_history.py --refresh   # einmalig alle 8 Quellen laden + cachen
    python3 yield_history.py             # nutzt Cache, zeigt Abdeckung je Waehrung
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

CACHE_PATH = Path.home() / "hermes2" / "data" / "backtest_yield_history_cache.json"

START_DATE = "2022-01-01"  # deckt den geplanten 3-Jahres-Backtest + Lookback-Puffer ab
END_DATE = date.today().isoformat()

# --- FRED --------------------------------------------------------------
CURVE_SERIES_FRED = {"2Y": "DGS2", "10Y": "DGS10"}


def _fred_history(series_id: str, api_key: str) -> list[tuple[str, float]]:
    url = (f"https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={series_id}&api_key={api_key}&file_type=json"
           f"&observation_start={START_DATE}&observation_end={END_DATE}")
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read())
    out = []
    for obs in data.get("observations", []):
        val = obs.get("value")
        if val not in (None, ".", ""):
            out.append((obs["date"], float(val)))
    return out


# --- ECB -----------------------------------------------------------------
CURVE_SERIES_ECB = {"2Y": "SR_2Y", "10Y": "SR_10Y"}
ECB_URL = ("https://data-api.ecb.europa.eu/service/data/YC/"
           "B.U2.EUR.4F.G_N_A.SV_C_YM.{maturity}")


def _ecb_history(maturity_series_id: str) -> list[tuple[str, float]]:
    url = (ECB_URL.format(maturity=maturity_series_id)
           + f"?startPeriod={START_DATE}&endPeriod={END_DATE}&format=csvdata")
    with urllib.request.urlopen(url, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        val = r.get("OBS_VALUE")
        if val not in (None, "", "."):
            out.append((r["TIME_PERIOD"], float(val)))
    return out


# --- SNB -------------------------------------------------------------------
CURVE_SERIES_SNB = {"2Y": "2J", "10Y": "10J"}
SNB_URL = "https://data.snb.ch/api/cube/rendeiduebd/data/csv/en"


def _snb_history(maturity_code: str) -> list[tuple[str, float]]:
    url = f"{SNB_URL}?fromDate={START_DATE}&toDate={END_DATE}&dimSel=D0(CHF),D1({maturity_code})"
    with urllib.request.urlopen(url, timeout=30) as resp:
        text = resp.read().decode("utf-8-sig")
    out = []
    for line in text.splitlines():
        m = re.match(r'"(\d{4}-\d{2}-\d{2})";"CHF";"[^"]+";"?(-?[\d.]+)"?', line)
        if m:
            out.append((m.group(1), float(m.group(2))))
    return out


# --- BoC ---------------------------------------------------------------
CURVE_SERIES_BOC = {"2Y": "BD.CDN.2YR.DQ.YLD", "10Y": "BD.CDN.10YR.DQ.YLD"}
BOC_URL = "https://www.bankofcanada.ca/valet/observations/{series}/json?start_date={start}&end_date={end}"


def _boc_history(series_id: str) -> list[tuple[str, float]]:
    url = BOC_URL.format(series=series_id, start=START_DATE, end=END_DATE)
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read())
    out = []
    for obs in data.get("observations", []):
        val = obs.get(series_id, {}).get("v")
        if val not in (None, "", "."):
            out.append((obs["d"], float(val)))
    return out


# --- BoE -------------------------------------------------------------------
CURVE_SERIES_BOE = {"2Y": 2, "10Y": 10}
BOE_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
BOE_CURVE_ZIP_URL = ("https://www.bankofengland.co.uk/-/media/boe/files/statistics/"
                     "yield-curves/glcnominalddata.zip")
BOE_CURVE_SHEET = "4. spot curve"
BOE_CURVE_HEADER_ROW = 4
BOE_FILES_NEEDED = ("2016 to 2024", "2025 to present")  # deckt START_DATE=2022 ab


def _boe_history(maturity_years: int) -> list[tuple[str, float]]:
    import zipfile
    import openpyxl

    req = urllib.request.Request(BOE_CURVE_ZIP_URL, headers=BOE_HEADERS)
    with urllib.request.urlopen(req, timeout=90) as resp:
        zip_bytes = resp.read()
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))

    out = []
    for name in zf.namelist():
        if not any(tag in name for tag in BOE_FILES_NEEDED):
            continue
        wb = openpyxl.load_workbook(io.BytesIO(zf.read(name)), read_only=True, data_only=True)
        if BOE_CURVE_SHEET not in wb.sheetnames:
            continue
        ws = wb[BOE_CURVE_SHEET]
        header = next(ws.iter_rows(min_row=BOE_CURVE_HEADER_ROW, max_row=BOE_CURVE_HEADER_ROW, values_only=True))
        col_idx = next((i for i, v in enumerate(header) if v == maturity_years), None)
        if col_idx is None:
            continue
        for row in ws.iter_rows(min_row=BOE_CURVE_HEADER_ROW + 2, values_only=True):
            if row[0] is None:
                continue
            d = row[0]
            d_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            if d_str < START_DATE:
                continue
            val = row[col_idx]
            if val is not None:
                out.append((d_str, float(val)))
    out.sort()
    return out


# --- MOF ---------------------------------------------------------------
CURVE_SERIES_MOF = {"2Y": "2Y", "10Y": "10Y"}
MOF_HISTORICAL_URL = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv"


def _mof_history(maturity_label: str) -> list[tuple[str, float]]:
    req = urllib.request.Request(MOF_HISTORICAL_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    text = raw.decode("shift_jis", errors="replace")
    rows = list(csv.reader(text.splitlines()))
    header = rows[1]
    if maturity_label not in header:
        return []
    col_idx = header.index(maturity_label)
    out = []
    for r in rows[2:]:
        if not r or not r[0].strip() or "/" not in r[0]:
            continue
        try:
            d = datetime.strptime(r[0].strip(), "%Y/%m/%d")
        except ValueError:
            continue
        d_str = d.strftime("%Y-%m-%d")
        if d_str < START_DATE:
            continue
        val = r[col_idx]
        if val not in (None, "", "."):
            out.append((d_str, float(val)))
    return out


# --- RBA -------------------------------------------------------------------
CURVE_SERIES_RBA = {"2Y": 1, "10Y": 4}
RBA_F2_URL = "https://www.rba.gov.au/statistics/tables/csv/f2-data.csv"


def _rba_history(maturity: str) -> list[tuple[str, float]]:
    from curl_cffi import requests as cf_requests
    resp = cf_requests.get(RBA_F2_URL, impersonate="chrome124", timeout=30)
    if resp.status_code != 200:
        return []
    col = CURVE_SERIES_RBA[maturity]
    rows = list(csv.reader(io.StringIO(resp.text)))
    out = []
    for r in rows:
        if len(r) <= col or not re.match(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$", r[0].strip()):
            continue
        try:
            d = datetime.strptime(r[0].strip(), "%d-%b-%Y")
        except ValueError:
            continue
        d_str = d.strftime("%Y-%m-%d")
        if d_str < START_DATE:
            continue
        val = r[col]
        if val not in (None, "", "."):
            out.append((d_str, float(val)))
    return out


# --- RBNZ ------------------------------------------------------------------
CURVE_SERIES_RBNZ = {"2Y": "2 year", "10Y": "10 year"}
RBNZ_B2_URL = ("https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/"
               "statistics/series/b/b2/hb2-daily-close.xlsx")
RBNZ_GROUP_LABEL = "Secondary market government bond closing yields"


def _rbnz_history(maturity: str) -> list[tuple[str, float]]:
    import pandas as pd
    from curl_cffi import requests as cf_requests
    resp = cf_requests.get(RBNZ_B2_URL, impersonate="chrome124", timeout=30)
    if resp.status_code != 200:
        return []
    df = pd.read_excel(io.BytesIO(resp.content), sheet_name="Data", header=None)
    target_label = CURVE_SERIES_RBNZ[maturity]
    col_idx = None
    for col in range(df.shape[1]):
        if df.iloc[0, col] == RBNZ_GROUP_LABEL and df.iloc[1, col] == target_label:
            col_idx = col
            break
    if col_idx is None:
        return []
    data = df.iloc[5:].dropna(subset=[0]).sort_values(0)
    out = []
    for _, row in data.iterrows():
        d = row[0]
        d_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
        if d_str < START_DATE:
            continue
        val = row[col_idx]
        if val is not None and str(val) != "nan":
            out.append((d_str, float(val)))
    return out


# --- Orchestrierung --------------------------------------------------------
def fetch_all() -> dict:
    """Laedt alle 8 Waehrungen x {2Y,10Y}. Liefert
    {ccy: {"2Y": [(date_iso, val), ...], "10Y": [...]}}. Einzelne
    Fehlschlaege werden pro Waehrung/Laufzeit geloggt, nicht die
    gesamte Funktion abgebrochen (gleiche Fehlertoleranz-Philosophie
    wie bond_loader.fetch_curve_yields())."""
    import os
    from dotenv import load_dotenv
    load_dotenv(Path.home() / "hermes2" / ".env")
    fred_key = os.environ.get("FRED_API_KEY")

    curve: dict[str, dict[str, list]] = {}

    def _try(ccy, maturity, fn):
        curve.setdefault(ccy, {})
        try:
            data = fn()
            curve[ccy][maturity] = data
            print(f"  {ccy}/{maturity}: {len(data)} Beobachtungen"
                  + (f", {data[0][0]} bis {data[-1][0]}" if data else ""))
        except Exception as e:
            curve[ccy][maturity] = []
            print(f"  {ccy}/{maturity}: FEHLER: {e}")

    print("USD (FRED):")
    for m, sid in CURVE_SERIES_FRED.items():
        _try("USD", m, lambda sid=sid: _fred_history(sid, fred_key))

    print("EUR (ECB):")
    for m, sid in CURVE_SERIES_ECB.items():
        _try("EUR", m, lambda sid=sid: _ecb_history(sid))

    print("CHF (SNB):")
    for m, sid in CURVE_SERIES_SNB.items():
        _try("CHF", m, lambda sid=sid: _snb_history(sid))

    print("CAD (BoC):")
    for m, sid in CURVE_SERIES_BOC.items():
        _try("CAD", m, lambda sid=sid: _boc_history(sid))

    print("GBP (BoE):")
    for m, years in CURVE_SERIES_BOE.items():
        _try("GBP", m, lambda years=years: _boe_history(years))

    print("JPY (MOF):")
    for m, label in CURVE_SERIES_MOF.items():
        _try("JPY", m, lambda label=label: _mof_history(label))

    print("AUD (RBA):")
    for m in CURVE_SERIES_RBA:
        _try("AUD", m, lambda m=m: _rba_history(m))

    print("NZD (RBNZ):")
    for m in CURVE_SERIES_RBNZ:
        _try("NZD", m, lambda m=m: _rbnz_history(m))

    return curve


def load_cached(refresh: bool = False) -> dict:
    if not refresh and CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    curve = fetch_all()
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(curve, indent=2), encoding="utf-8")
    print(f"\nGeschrieben: {CACHE_PATH}")
    return curve


def wert_am(curve: dict, ccy: str, maturity: str, cutoff_date: date) -> float | None:
    """Letzter bekannter Wert MIT Datum <= cutoff_date (Lookahead-sicher).
    None falls keine Quelle vorhanden (z.B. MXN) oder keine Daten vor
    cutoff_date."""
    series = curve.get(ccy, {}).get(maturity, [])
    if not series:
        return None
    cutoff_str = cutoff_date.isoformat()
    best = None
    for d_str, val in series:
        if d_str <= cutoff_str:
            if best is None or d_str > best[0]:
                best = (d_str, val)
    return best[1] if best else None


if __name__ == "__main__":
    refresh = "--refresh" in sys.argv
    curve = load_cached(refresh=refresh)
    print("\nAbdeckung je Waehrung/Laufzeit:")
    for ccy in ("USD", "EUR", "CHF", "GBP", "JPY", "CAD", "AUD", "NZD"):
        for m in ("2Y", "10Y"):
            series = curve.get(ccy, {}).get(m, [])
            if series:
                print(f"  {ccy}/{m}: {len(series)} Punkte, {series[0][0]} bis {series[-1][0]}")
            else:
                print(f"  {ccy}/{m}: KEINE DATEN")
