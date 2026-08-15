#!/usr/bin/env python3
"""
publish_pages.py — Übersichtsseite für GitHub Pages (5mg_analyzer_repo/index.html)

Ersetzt die alte watchlist_export.py/build_pairs()-Einzelmethode-Anzeige
komplett durch eine einfache Tabellenansicht der letzten (bis zu drei)
Kalenderwochen aus der echten weekly_engine_signals-Tabelle (drei Engines:
Basis-/Fluss-/Kombi-Signal, siehe weekly_engine_report.py).

Aufruf:
  - Als Modul aus weekly_engine_report.py: publish_pages.publish() —
    schreibt index.html + git add/commit/push (echter Freitags-Cron-Lauf,
    läuft NACH dem Telegram-Versand in main()).
  - Direkt/CLI mit --dry-run: rendert die Seite nur lokal (siehe
    PREVIEW_PATH unten), OHNE index.html im Repo zu verändern und OHNE
    git-Operationen — für die Vorschau vor der ersten echten Ausführung.
"""

from __future__ import annotations

import html
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path.home() / "hermes2" / "scripts"))
import db  # zentrales hermes.db (weekly_engine_signals)

REPO_DIR = Path.home() / "hermes2" / "scripts" / "5mg_analyzer_repo"
OUTPUT_HTML = REPO_DIR / "index.html"
PREVIEW_PATH = Path.home() / "hermes2" / "reports" / "index_preview.html"

MAX_WEEKS = 3
ENGINE_ORDER = ["Basis-Signal", "Fluss-Signal", "Kombi-Signal"]


def _iso_week_key(ts_str: str) -> tuple[int, int]:
    dt = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
    iso_year, iso_week, _ = dt.isocalendar()
    return iso_year, iso_week


def load_recent_weeks(limit: int = MAX_WEEKS) -> list[dict]:
    """Gruppiert weekly_engine_signals nach `ts` (ein Lauf = 3 Zeilen mit
    identischem ts) und ordnet jeden Lauf seiner ISO-Kalenderwoche zu.
    Pro Kalenderwoche zaehlt NUR der juengste Lauf (falls in derselben
    Woche mehrfach gelaufen wurde, z.B. Test-/Korrektur-Sends - sonst
    wuerden alte Testlaeufe die Ansicht der Woche verfaelschen). Liefert
    die `limit` juengsten unterschiedlichen Wochen, neueste zuerst."""
    rows = db.query("SELECT * FROM weekly_engine_signals ORDER BY ts DESC")
    runs: dict[str, list[dict]] = {}
    run_order: list[str] = []
    for r in rows:
        ts = r["ts"]
        if ts not in runs:
            runs[ts] = []
            run_order.append(ts)
        runs[ts].append(dict(r))

    weeks: dict[tuple[int, int], dict] = {}
    order: list[tuple[int, int]] = []
    for ts in run_order:  # bereits absteigend sortiert -> erster Treffer je Woche ist der juengste Lauf
        key = _iso_week_key(ts)
        if key in weeks:
            continue
        if len(order) >= limit:
            continue
        weeks[key] = {"iso_year": key[0], "iso_week": key[1], "date": ts[:10], "rows": runs[ts]}
        order.append(key)

    result = [weeks[k] for k in order]
    for w in result:
        w["rows"].sort(
            key=lambda r: ENGINE_ORDER.index(r["engine"]) if r["engine"] in ENGINE_ORDER else 99
        )
    return result


def _week_table(week: dict) -> str:
    rows_html = []
    for r in week["rows"]:
        top = "🏆 Top" if r["is_top_signal"] else "–"
        konflikt = f"⚠️ {html.escape(r['conflict_note'])}" if r["in_conflict"] else "–"
        fq = r["final_quality"]
        fq_str = f"{fq:.1f}" if fq is not None else "–"
        rows_html.append(
            "      <tr>"
            f"<td>{html.escape(r['engine'])}</td>"
            f"<td>{html.escape(r['pair'])}</td>"
            f"<td>{html.escape(r['bias'])}</td>"
            f"<td>{fq_str}</td>"
            f"<td>{top}</td>"
            f"<td>{konflikt}</td>"
            "</tr>"
        )
    return "\n".join(rows_html)


def render_html(weeks: list[dict]) -> str:
    if not weeks:
        body = "<p>Noch keine Wochen-Engine-Daten vorhanden.</p>"
    else:
        blocks = []
        for w in weeks:
            blocks.append(
                f"  <section>\n"
                f"    <h2>KW {w['iso_week']}/{w['iso_year']} — {html.escape(w['date'])}</h2>\n"
                f"    <table>\n"
                f"      <tr><th>Engine</th><th>Paar</th><th>Bias</th>"
                f"<th>Final Quality</th><th>Top-Signal</th><th>Konflikt</th></tr>\n"
                f"{_week_table(w)}\n"
                f"    </table>\n"
                f"  </section>"
            )
        body = "\n\n".join(blocks)

    generiert = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>5MG Analyzer — Wochen-Engines</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 900px;
          margin: 2rem auto; padding: 0 1rem; color: #222; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .sub {{ color: #666; margin-top: 0; margin-bottom: 2rem; font-size: 0.9rem; }}
  section {{ margin-bottom: 2.5rem; }}
  h2 {{ border-bottom: 2px solid #333; padding-bottom: 0.3rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: 0.5rem 0.7rem; border-bottom: 1px solid #ddd; }}
  th {{ background: #f2f2f2; }}
  tr:hover {{ background: #fafafa; }}
</style>
</head>
<body>
  <h1>5MG Analyzer — Wochen-Engines</h1>
  <p class="sub">Basis-/Fluss-/Kombi-Signal je Kalenderwoche · generiert {generiert}</p>
{body}
</body>
</html>
"""


def build_index_html() -> str:
    return render_html(load_recent_weeks(MAX_WEEKS))


def _git(*args: str) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(REPO_DIR), *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(" ".join(cmd), "->", result.returncode)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0 and result.stderr.strip():
        print(result.stderr.strip())
    return result


def publish(push: bool = True) -> Path:
    """Schreibt index.html im Repo-Root und committet/pusht (echter Lauf,
    aus weekly_engine_report.py nach dem Telegram-Versand aufgerufen)."""
    html_out = build_index_html()
    OUTPUT_HTML.write_text(html_out, encoding="utf-8")
    print(f"Geschrieben: {OUTPUT_HTML}")

    if not push:
        return OUTPUT_HTML

    _git("add", "index.html")
    commit = _git("commit", "-m", f"Wochen-Engines Update {datetime.now().strftime('%Y-%m-%d')}")
    if commit.returncode != 0 and "nothing to commit" in (commit.stdout + commit.stderr):
        return OUTPUT_HTML
    _git("push")
    return OUTPUT_HTML


def main() -> None:
    if "--dry-run" in sys.argv:
        html_out = build_index_html()
        PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREVIEW_PATH.write_text(html_out, encoding="utf-8")
        weeks = load_recent_weeks(MAX_WEEKS)
        print(f"Vorschau geschrieben: {PREVIEW_PATH}")
        print(f"{len(weeks)} Kalenderwoche(n) gefunden, KEIN Schreiben in index.html, KEIN git.")
        for w in weeks:
            print(f"  KW {w['iso_week']}/{w['iso_year']} ({w['date']}): {len(w['rows'])} Engine-Zeile(n)")
        return

    publish(push="--no-push" not in sys.argv)


if __name__ == "__main__":
    main()
