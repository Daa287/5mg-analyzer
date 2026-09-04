#!/usr/bin/env python3
"""
publish_pages_v2.py — 5MG v2, Website-Grundgerüst (NUR LOKAL).

Spiegelt die ECHTE Architektur von scripts/publish_pages.py (v1): die
Seite wird als fertiges, in sich geschlossenes statisches HTML direkt
serverseitig generiert (render_html_v2()) - KEIN client-seitiger
JS-Fetch einer JSON-Datei zur Laufzeit. index.html liest bei v1 bereits
heute kein watchlist.json mehr (Altlast eines ersetzten Mechanismus,
siehe publish_pages.py-Docstring) - v2 baut das von Anfang an konsistent
zur echten, aktuellen Architektur.

watchlist_v2.json wird TROTZDEM zusätzlich geschrieben (expliziter
Wunsch) - als reiner Daten-Snapshot nebenher, NICHT als das, wovon
v2/index.html tatsächlich abhängt.

Datenquelle: report_v2.build_rows() (kein Duplikat der Aggregations-
Logik - Market-Pulse-Status/Gate-Status/Quality-Tier werden 1:1 wie in
der Telegram-Testnachricht zusammengeführt). weekly_engine_signals (v1)
NUR LESEND, market_pulse_checks_v2/entry_readiness_checks_v2 (v2) NUR
LESEND - diese Datei schreibt NIRGENDWO in hermes.db.

--no-push ist STANDARD (schreibt nur lokal v2/index.html +
v2/watchlist_v2.json, KEIN git add/commit/push). Nur bei explizitem
--push würde gepusht - HEUTE NICHT verwendet, nur lokal testen.

    python3 publish_pages_v2.py            # --no-push (Standard), nur lokal
    python3 publish_pages_v2.py --push     # würde committen/pushen (heute NICHT nutzen)
"""
from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

V2_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(V2_SCRIPTS_DIR.parent.parent / "scripts" / "v2"))
import report_v2  # v2, build_rows() - liefert pair/bias/engine/tier/market_pulse/gate

REPO_DIR = Path.home() / "hermes2" / "scripts" / "5mg_analyzer_repo"
V2_DIR = REPO_DIR / "v2"
OUTPUT_HTML = V2_DIR / "index.html"
OUTPUT_JSON = V2_DIR / "watchlist_v2.json"


def _tier_badge(tier: str) -> str:
    cls = {"A+": "tier-a", "B": "tier-b", "C": "tier-c"}.get(tier, "tier-c")
    return f'<span class="badge {cls}">{html.escape(tier)}</span>'


def _gate_badge(gate_status_text: str) -> str:
    if gate_status_text.startswith("✅"):
        cls = "win"
    elif gate_status_text.startswith("❌"):
        cls = "loss"
    else:
        cls = "open"
    return f'<span class="badge {cls}">{html.escape(gate_status_text)}</span>'


def render_html_v2(rows: list[dict]) -> str:
    if not rows:
        body = "<p>Noch keine Signale in weekly_engine_signals gefunden.</p>"
    else:
        trs = []
        for r in rows:
            trs.append(
                "        <tr>"
                f'<td data-label="Paar">{html.escape(r["pair"])}</td>'
                f'<td data-label="Bias">{html.escape(r["bias"])}</td>'
                f'<td data-label="Engine">{html.escape(r["engine"])}</td>'
                f'<td data-label="Final Quality">{r["final_quality"]:.1f}</td>'
                f'<td data-label="Tier">{_tier_badge(r["tier"])}</td>'
                f'<td data-label="Market Pulse">{html.escape(r["market_pulse_status"])}</td>'
                f'<td data-label="Gate">{_gate_badge(r["gate_status_text"])}</td>'
                "</tr>"
            )
        body = (
            '  <table class="week-table">\n'
            '    <thead>\n'
            '      <tr><th>Paar</th><th>Bias</th><th>Engine</th><th>Final Quality</th>'
            '<th>Tier</th><th>Market Pulse</th><th>Gate</th></tr>\n'
            '    </thead>\n'
            '    <tbody>\n' + "\n".join(trs) + '\n    </tbody>\n'
            '  </table>'
        )

    generiert = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>5MG v2 TEST — Market Pulse / Entry Gate / Quality Tiers</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ --accent: #b45309; --accent-dark: #7c2d12; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 900px;
          margin: 2rem auto; padding: 0 1rem; color: #222; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .sub {{ color: #666; margin-top: 0; margin-bottom: 1rem; font-size: 0.9rem; }}
  .test-banner {{ background: #fff3cd; border: 2px solid #b45309; border-radius: 8px;
                   padding: 0.9rem 1.2rem; margin-bottom: 2rem; font-weight: 600; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: 0.5rem 0.7rem; border-bottom: 1px solid #ddd; }}
  th {{ background: #fef3e2; color: var(--accent-dark); font-weight: 600; }}
  tr:hover {{ background: #fafafa; }}
  .badge {{ display: inline-block; font-weight: 600; padding: 0.1rem 0.55rem;
            border-radius: 4px; font-size: 0.92em; white-space: nowrap; }}
  .badge.win  {{ color: #1a7f37; background: #e6f4ea; }}
  .badge.loss {{ color: #cf222e; background: #fde8e8; }}
  .badge.open {{ color: #9a6700; background: #fff6e0; }}
  .badge.tier-a {{ color: #1a7f37; background: #e6f4ea; }}
  .badge.tier-b {{ color: #9a6700; background: #fff6e0; }}
  .badge.tier-c {{ color: #666;    background: #f2f2f2; }}

  @media (max-width: 600px) {{
    .week-table thead {{ display: none; }}
    .week-table, .week-table tbody, .week-table tr, .week-table td {{ display: block; width: 100%; }}
    .week-table {{ border: none; }}
    .week-table tr {{
      margin-bottom: 1rem; border: 1px solid #ddd; border-radius: 8px;
      padding: 0.4rem 0.8rem; box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }}
    .week-table td {{
      display: flex; justify-content: space-between; align-items: center;
      gap: 1rem; text-align: right; padding: 0.4rem 0; border-bottom: 1px solid #eee;
    }}
    .week-table td:last-child {{ border-bottom: none; }}
    .week-table td::before {{
      content: attr(data-label); font-weight: 600; color: #555;
      text-align: left; flex-shrink: 0;
    }}
  }}
</style>
</head>
<body>
  <h1>5MG v2 TEST</h1>
  <p class="sub">Market Pulse + Entry Gate + Quality Tiers · generiert {generiert}</p>
  <div class="test-banner">
    🧪 TESTVERSION — komplett parallel zum produktiven 5MG-System, KEINE
    Kauf-/Verkaufssignale, KEINE Positionsgröße, KEIN SL. Eigene, transparent
    gewählte Schwellen/Skalen, KEIN Nachbau der Original-Formel.
  </div>
{body}
</body>
</html>
"""


def build_watchlist_json(rows: list[dict]) -> dict:
    """Reiner Daten-Snapshot (nicht das, wovon index.html abhängt - siehe
    Docstring)."""
    return {
        "generated_ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "hinweis": "5MG v2 TESTVERSION - kein Nachbau der Original-Formel, keine Kauf-/Verkaufssignale.",
        "signals": rows,
    }


def _git(*args: str) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(REPO_DIR), *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(" ".join(cmd), "->", result.returncode)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0 and result.stderr.strip():
        print(result.stderr.strip())
    return result


def publish(push: bool) -> tuple[Path, Path]:
    V2_DIR.mkdir(parents=True, exist_ok=True)
    rows = report_v2.build_rows()

    html_out = render_html_v2(rows)
    OUTPUT_HTML.write_text(html_out, encoding="utf-8")
    print(f"Geschrieben: {OUTPUT_HTML}")

    json_out = build_watchlist_json(rows)
    OUTPUT_JSON.write_text(json.dumps(json_out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Geschrieben: {OUTPUT_JSON}")

    if not push:
        return OUTPUT_HTML, OUTPUT_JSON

    _git("add", "v2/index.html", "v2/watchlist_v2.json")
    commit = _git("commit", "-m", f"5MG v2 TEST Update {datetime.now().strftime('%Y-%m-%d')}")
    if commit.returncode != 0 and "nothing to commit" in (commit.stdout + commit.stderr):
        return OUTPUT_HTML, OUTPUT_JSON
    _git("push")
    return OUTPUT_HTML, OUTPUT_JSON


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--push", action="store_true",
                    help="Committet/pusht wirklich (Standard: --no-push, nur lokal)")
    args = p.parse_args()
    push = args.push  # Standard: push=False (--no-push-Verhalten)

    html_path, json_path = publish(push=push)
    print(f"\n{'GEPUSHT' if push else 'NUR LOKAL geschrieben, KEIN git (Standard --no-push)'}")


if __name__ == "__main__":
    main()
