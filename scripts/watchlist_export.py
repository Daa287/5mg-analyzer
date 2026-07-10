#!/usr/bin/env python3
"""
watchlist_export.py — Hauptskript, orchestriert die 3-Stufen-Engine
und schreibt watchlist.json ins Dashboard-Repo (analog zu
segelwetter_html_export.py bei adriawetter).

Ablauf:
  1. COT-Scores laden (cot_loader)
  2. Top-3-Paare + 4 Zusatzmärkte bilden (Extremwerte, Paarbildung vs. USD)
  3. Seasonality je Setup (season_engine) + Re-Ranking
  4. Bond-Score (bond_loader) + Event-Check (calendar_check)
  5. Finale Formel: 0.5*COT + 0.2*Saison + 0.3*Bond - 0.67*Vola
  6. watchlist.json schreiben, git add/commit/push

Cron-Empfehlung (crontab -e auf hermes2):
  # CFTC published freitags ca. 15:30 ET (~21:30/22:30 MESZ je nach DST)
  0 23 * * 5   /home/pi/hermes2/venv/bin/python3 /home/pi/hermes2/scripts/5mg/watchlist_export.py >> /home/pi/hermes2/logs/5mg_export.log 2>&1

Aufruf manuell:
  /home/pi/hermes2/venv/bin/python3 watchlist_export.py [--no-push]
"""

from __future__ import annotations
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import cot_loader
import season_engine
import bond_loader
import calendar_check

# --- Konfiguration -----------------------------------------------------
REPO_DIR = Path.home() / "hermes2" / "scripts" / "5mg_analyzer_repo"  # git clone Ziel
OUTPUT_JSON = REPO_DIR / "watchlist.json"

WEIGHTS = {"cot": 0.50, "saison": 0.20, "bond": 0.30}
VOLA_FAKTOR = 0.67
SAISON_RERANK = 0.375
EXTREM_SCHWELLE = 80  # ab diesem |Score-50| gilt ein Markt als Top-Kandidat

PAIR_WHITELIST = ["CHF", "CAD", "NZD", "EUR", "GBP", "JPY", "AUD", "MXN"]
ZUSATZ_MAERKTE = ["GOLD", "SILVER", "NASDAQ100", "SP500"]
DISPLAY_NAME = {"GOLD": "Gold", "SILVER": "Silber", "NASDAQ100": "Nasdaq 100", "SP500": "S&P 500"}


# Waehrungen, die als XXX/USD notiert werden (Fremdwaehrung ist Basis).
# Alle anderen werden als USD/XXX notiert (USD ist Basis) - Marktkonvention.
XXX_USD_STYLE = {"EUR", "GBP", "AUD", "NZD"}


def build_pairs(cot_scores: dict) -> list[dict]:
    """Bildet USD-Paare aus den staerksten/schwaechsten COT-Scores,
    unter Beachtung der echten Marktnotation je Waehrung."""
    kandidaten = []
    for ccy in PAIR_WHITELIST:
        info = cot_scores.get(ccy)
        if not info or info["score"] is None:
            continue
        staerke = info["score"]
        fut = info["richtung"]

        if ccy in XXX_USD_STYLE:
            pair = f"{ccy}/USD"
            richtung = fut
        else:
            pair = f"USD/{ccy}"
            richtung = "SHORT" if fut == "LONG" else "LONG"

        kandidaten.append({
            "pair": pair, "dir": richtung, "cot": staerke, "staerke": staerke,
            "base": pair.split("/")[0], "quote": pair.split("/")[1], "stark": f"{ccy} Index {info['index']:.0f}",
        })
    kandidaten.sort(key=lambda x: x["staerke"], reverse=True)
    return kandidaten[:3]


def build_zusatz(cot_scores: dict) -> list[dict]:
    out = []
    for key in ZUSATZ_MAERKTE:
        info = cot_scores.get(key)
        if not info or info["score"] is None:
            continue
        out.append({"name": DISPLAY_NAME[key], "dir": info["richtung"],
                    "score": info["score"]})
    return out


def zwischen_score(cot: float, saison: float) -> float:
    return round(cot + SAISON_RERANK * (saison - 50), 1)


def finale_qualitaet(cot: float, saison: float, bond: float, vola: int) -> dict:
    roh = WEIGHTS["cot"] * cot + WEIGHTS["saison"] * saison + WEIGHTS["bond"] * bond
    final = round(roh - VOLA_FAKTOR * vola, 1)
    kat = "GUT" if final >= 60 else ("GEMISCHT" if final >= 40 else "SCHWACH")
    return {"roh": round(roh, 2), "final": final, "kategorie": kat}


def run() -> dict:
    this_year = datetime.now(timezone.utc).year
    cot_result = cot_loader.latest_scores([this_year - 1, this_year])
    scores = cot_result["scores"]

    top3 = build_pairs(scores)
    zusatz = build_zusatz(scores)
    yields = bond_loader.refresh_config()  # holt Live-Renditen, faellt sonst auf Config zurueck

    setups_out = []
    for s in top3:
        season = season_engine.season_score(s["pair"], s["dir"])
        zwischen = zwischen_score(s["cot"], season["score"])
        bond = bond_loader.bond_score(s["base"], s["quote"], s["dir"], yields)
        events = calendar_check.event_summary(s["base"], s["quote"])
        final = finale_qualitaet(s["cot"], season["score"], bond["score"], events["vola"])

        setups_out.append({
            "pair": s["pair"], "dir": s["dir"], "cot": s["cot"], "stark": s["stark"],
            "saison": {"score": season["score"], "label": season["label"], "hist": season["hist_text"]},
            "zwischen": zwischen,
            "bond": {"base": s["base"], "quote": s["quote"],
                     "yBase": yields.get(s["base"]), "yQuote": yields.get(s["quote"])},
            "_bond_score": bond["score"], "_bond_expl": bond["expl"], "_bond_spread": bond["spread"],
            "events": {"vola": events["vola"], "lage": events["lage"],
                       "termine": events["termine"], "weitere": events["weitere"]},
            "final": final,
        })

    payload = {
        "report": cot_result["report_date"],
        "maerkte": cot_result["markets"],
        "quelle": "CFTC historische Reports, lokal zwischengespeichert",
        "generiert": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "setups": setups_out,
        "zusatz": zusatz,
    }
    return payload


def write_and_push(payload: dict, push: bool = True) -> None:
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Geschrieben: {OUTPUT_JSON}")

    if not push:
        return
    cmds = [
        ["git", "-C", str(REPO_DIR), "add", "watchlist.json"],
        ["git", "-C", str(REPO_DIR), "commit", "-m",
         f"Watchlist Update {payload['report']} ({len(payload['setups'])} Setups)"],
        ["git", "-C", str(REPO_DIR), "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(" ".join(cmd), "->", result.returncode)
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.returncode != 0:
            print(result.stderr.strip())
            if "nothing to commit" in result.stderr or "nothing to commit" in result.stdout:
                continue
            break


if __name__ == "__main__":
    bond_loader.ensure_config_exists()
    payload = run()
    push = "--no-push" not in sys.argv
    write_and_push(payload, push=push)
