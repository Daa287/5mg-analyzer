#!/usr/bin/env python3
"""
5mg_shadow_log.py — Erfolgsmessung ohne Kapitalrisiko (Muster: shadow_log.py
aus dem SK-System).

Modus 1 (log, Default): Wird direkt nach watchlist_export aufgerufen.
    Schreibt jedes Setup mit aktuellem Kurs (yfinance) in
    ~/hermes2/data/5mg_shadow.jsonl (eine Zeile pro Setup pro Woche).

Modus 2 (eval): Wertet alle Eintraege aus, die aelter als 7 Tage sind:
    Kursentwicklung seit Log-Zeitpunkt, ob der Bias (LONG/SHORT) in die
    richtige Richtung lief, gruppiert nach finaler Qualitaet (GUT/GEMISCHT).
    Aufruf: python3 5mg_shadow_log.py eval

Nach 8-12 Wochen liefert eval die ehrliche Antwort, ob das System eine
Edge hat, BEVOR echtes Geld dahinter steht.
"""

from __future__ import annotations
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from season_engine import YF_TICKERS

try:
    import yfinance as yf
except ImportError:
    yf = None

LOG_FILE = Path.home() / "hermes2" / "data" / "5mg_shadow.jsonl"
WATCHLIST = Path.home() / "hermes2" / "scripts" / "5mg_analyzer_repo" / "watchlist.json"


def _price(pair: str) -> float | None:
    if yf is None:
        return None
    ticker = YF_TICKERS.get(pair)
    if not ticker:
        return None
    import time
    for versuch in range(3):
        try:
            hist = yf.Ticker(ticker).history(period="5d", interval="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass
        time.sleep(2)  # kurze Pause gegen Yahoo Rate-Limiting bei schnellen Folgeanfragen
    return None


def log_current():
    if not WATCHLIST.exists():
        print("Keine watchlist.json gefunden - erst watchlist_export.py laufen lassen.")
        return
    data = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    with LOG_FILE.open("a", encoding="utf-8") as f:
        for s in data.get("setups", []):
            entry = {
                "logged": now,
                "report": data.get("report"),
                "pair": s["pair"], "dir": s["dir"],
                "cot": s.get("cot"),
                "final": s.get("final", {}).get("final"),
                "kategorie": s.get("final", {}).get("kategorie"),
                "entry_price": _price(s["pair"]),
                # Teilwerte fuer spaetere Formel-Vergleiche (Ergaenzung, bestehende
                # Felder oben unveraendert - alte Zeilen bleiben lesbar, nur ohne diese)
                "saison": s.get("saison", {}).get("score"),
                "saison_label": s.get("saison", {}).get("label"),
                "zwischen": s.get("zwischen"),
                "bond_score": s.get("_bond_score"),
                "bond_spread": s.get("_bond_spread"),
                "vola": s.get("events", {}).get("vola"),
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            n += 1
    print(f"{n} Setups geloggt -> {LOG_FILE}")


def evaluate():
    if not LOG_FILE.exists():
        print("Noch keine Shadow-Daten vorhanden.")
        return
    now = datetime.now(timezone.utc)
    rows = [json.loads(l) for l in LOG_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    ergebnisse = []
    for r in rows:
        logged = datetime.fromisoformat(r["logged"])
        alter_tage = (now - logged).days
        if alter_tage < 7 or not r.get("entry_price"):
            continue
        aktuell = _price(r["pair"])
        if aktuell is None:
            continue
        move_pct = (aktuell - r["entry_price"]) / r["entry_price"] * 100
        richtig = move_pct > 0 if r["dir"] == "LONG" else move_pct < 0
        ergebnisse.append({**r, "tage": alter_tage,
                           "move_pct": round(move_pct, 2), "richtig": richtig})

    if not ergebnisse:
        print("Noch keine Eintraege aelter als 7 Tage mit Kursdaten.")
        return

    print(f"{'Pair':<10} {'Dir':<6} {'Kat':<9} {'Tage':>4} {'Move %':>8} {'Bias OK':>8}")
    for e in ergebnisse:
        print(f"{e['pair']:<10} {e['dir']:<6} {str(e.get('kategorie')):<9} "
              f"{e['tage']:>4} {e['move_pct']:>8.2f} {'JA' if e['richtig'] else 'nein':>8}")

    for kat in ("GUT", "GEMISCHT", "SCHWACH"):
        sub = [e for e in ergebnisse if e.get("kategorie") == kat]
        if sub:
            hitrate = sum(e["richtig"] for e in sub) / len(sub) * 100
            avg = sum(e["move_pct"] * (1 if e["dir"] == "LONG" else -1) for e in sub) / len(sub)
            print(f"\n{kat}: {len(sub)} Setups, Trefferquote {hitrate:.0f} %, "
                  f"mittlere Bewegung in Bias-Richtung {avg:+.2f} %")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "eval":
        evaluate()
    else:
        log_current()
