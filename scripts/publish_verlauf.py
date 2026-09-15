#!/usr/bin/env python3
"""
publish_verlauf.py — Verlaufs-Unterseite für GitHub Pages
(5mg_analyzer_repo/verlauf/index.html), Auftrag 15.09.2026.

Zeigt die bereits vorhandenen signal_performance-Daten (Ebene 1 —
Signal-Zeitpunkt-Performance aus evaluate_signals.py, siehe dortiger
Modul-Docstring) als Wochen-Zeitreihe pro Engine: für jede abgeschlossene
Woche Win/Loss/Open + prozentuale Bewegung, statt nur den aktuellen
Wochenstand wie index.html.

Wiederverwendet, UNVERAENDERT:
  - publish_pages.load_recent_weeks() (dieselbe Pro-ISO-Woche-Dedup-Logik
    wie die Hauptseite - mehrere Test-/Korrektur-Läufe am selben Tag
    zählen nicht als mehrere Wochen, siehe dortiger Docstring)
  - publish_pages._perf_lookup() / evaluate_signals.load_stored_results()
    (read-only, KEIN live yfinance-Abruf, KEIN DB-Write)
  - evaluate_signals.MIN_STICHPROBE/MIN_FUER_PROZENT (dieselbe Schwelle
    wie ueberall im Projekt)

Aggregation nutzt NUR den je ISO-Woche jüngsten Lauf (dieselbe Dedup-
Logik wie load_recent_weeks(), OHNE das MAX_WEEKS=3-Limit der Hauptseite -
hier sollen ALLE Wochen erscheinen), NICHT alle rohen weekly_engine_
signals-Zeilen - vermeidet Doppelzählung von Test-/Korrektur-Läufen
(z.B. mehrere Läufe am 12.08./15.08.2026, siehe DB-Stichprobe vor dieser
Änderung).

Importiert evaluate_signals.py NUR für seine reinen Lese-/Konstanten-
Funktionen (load_stored_results(), MIN_STICHPROBE, MIN_FUER_PROZENT) -
exakt derselbe, bereits etablierte Weg wie publish_pages.py selbst.
evaluate_signals.py bleibt weiterhin gitignored/uncommitted (7 sensible
Dateien, siehe .gitignore) - dieses Modul committet an keiner Stelle
dessen Datei oder Inhalt, nur die daraus berechneten Win/Loss/Open-
Kennzahlen als reines HTML.

KEIN eigenständiges git add/commit/push - das übernimmt publish_pages.py
zentral (ein gemeinsamer Commit für index.html + verlauf/index.html).

    python3 publish_verlauf.py --dry-run   # rendert nur lokal, siehe PREVIEW_PATH
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import publish_pages  # load_recent_weeks()/_perf_lookup(), siehe Modul-Docstring
import evaluate_signals  # NUR load_stored_results()/MIN_STICHPROBE/MIN_FUER_PROZENT, read-only

REPO_DIR = Path.home() / "hermes2" / "scripts" / "5mg_analyzer_repo"
OUTPUT_HTML = REPO_DIR / "verlauf" / "index.html"
PREVIEW_PATH = Path.home() / "hermes2" / "reports" / "verlauf_preview.html"

STATUS_BADGE = {
    "WIN": '<span class="badge win">✅ WIN</span>',
    "LOSS": '<span class="badge loss">❌ LOSS</span>',
    "OPEN": '<span class="badge open">⏳ OPEN</span>',
    "ERROR": '<span class="badge error">⚠️ Fehler</span>',
}


def _sig_status_label(perf: dict | None) -> str:
    """sig_status/sig_direction_correct -> WIN/LOSS/OPEN/ERROR. perf=None
    (kein signal_performance-Eintrag, kann bei ganz frischen Signalen vor
    dem ersten taeglichen evaluate_signals.py-Lauf kurz vorkommen) wird
    wie OPEN behandelt - faktisch korrekt, noch keine Aussage moeglich."""
    if perf is None:
        return "OPEN"
    status = perf["sig_status"]
    if status == "DONE":
        return "WIN" if perf["sig_direction_correct"] == 1 else "LOSS"
    if status == "ERROR":
        return "ERROR"
    return "OPEN"


def _week_row_html(row: dict, perf: dict | None) -> str:
    label = _sig_status_label(perf)
    badge = STATUS_BADGE[label]
    bewegung = f"{perf['sig_pct_change']:+.2f}%" if perf and perf.get("sig_pct_change") is not None else "–"
    return (
        "        <tr>"
        f'<td data-label="Engine">{html.escape(row["engine"])}</td>'
        f'<td data-label="Paar">{html.escape(row["pair"])}</td>'
        f'<td data-label="Bias">{html.escape(row["bias"])}</td>'
        f'<td data-label="Status">{badge}</td>'
        f'<td data-label="Bewegung (7 Tage)">{bewegung}</td>'
        "</tr>"
    )


def _hitrate_html(n_correct: int, n_total: int) -> str:
    """1:1 dieselbe MIN_FUER_PROZENT/MIN_STICHPROBE-Logik wie
    publish_pages._hitrate_html() / evaluate_signals._fmt_pct()."""
    if n_total < evaluate_signals.MIN_FUER_PROZENT:
        return (f'{n_correct} von {n_total} '
                f'<span class="badge muted">kein Prozent bei n&lt;{evaluate_signals.MIN_FUER_PROZENT}</span>')
    pct = 100 * n_correct / n_total
    out = f'{n_correct}/{n_total} ({pct:.1f}%)'
    if n_total < evaluate_signals.MIN_STICHPROBE:
        out += ' <span class="badge muted">NICHT BELASTBAR</span>'
    return out


def load_all_weeks_with_perf() -> tuple[list[dict], dict]:
    """Alle deduplizierten Wochen (kein MAX_WEEKS-Limit) + Perf-Lookup,
    dieselbe Quelle/Logik wie die Hauptseite (siehe Modul-Docstring)."""
    weeks = publish_pages.load_recent_weeks(limit=9999)
    results = evaluate_signals.load_stored_results()
    perf_by_id = publish_pages._perf_lookup(results)
    return weeks, perf_by_id


def _engine_summary_html(weeks: list[dict], perf_by_id: dict) -> str:
    """Gesamt-Trefferquote je Engine ueber ALLE deduplizierten Wochen
    (nur DONE-Faelle), mit MIN_STICHPROBE/MIN_FUER_PROZENT-Kennzeichnung -
    dieselbe Konvention wie in allen bisherigen Reports dieser Session."""
    by_engine: dict[str, list[str]] = {}
    for w in weeks:
        for row in w["rows"]:
            perf = perf_by_id.get(row["id"])
            label = _sig_status_label(perf)
            by_engine.setdefault(row["engine"], []).append(label)

    rows_html = []
    for engine in publish_pages.ENGINE_ORDER:
        labels = by_engine.get(engine, [])
        n_done = sum(1 for l in labels if l in ("WIN", "LOSS"))
        n_win = sum(1 for l in labels if l == "WIN")
        n_open = sum(1 for l in labels if l == "OPEN")
        n_error = sum(1 for l in labels if l == "ERROR")
        rows_html.append(
            "      <tr>"
            f"<td>{html.escape(engine)}</td>"
            f"<td>{_hitrate_html(n_win, n_done) if n_done else f'0 von 0 (noch keine abgeschlossenen Wochen)'}</td>"
            f"<td>{n_open}</td><td>{n_error}</td>"
            "</tr>"
        )
    return (
        '  <section class="status-footer">\n'
        '    <h2>Gesamt-Trefferquote je Engine (alle Wochen, Ebene 1 — Signal-Zeitpunkt)</h2>\n'
        '    <table class="pair-table" style="max-width:520px">\n'
        '      <tr><th>Engine</th><th>Trefferquote (DONE)</th><th>Open</th><th>Fehler</th></tr>\n'
        + "\n".join(rows_html) + "\n"
        '    </table>\n'
        f'    <p class="hinweis">Mindest-Stichprobengröße-Konvention: unter n={evaluate_signals.MIN_FUER_PROZENT} '
        f'keine Prozentangabe, unter n={evaluate_signals.MIN_STICHPROBE} als "NICHT BELASTBAR" gekennzeichnet '
        '(dieselbe Konvention wie in allen Backtest-Reports).</p>\n'
        '  </section>'
    )


def build_verlauf_html() -> str:
    weeks, perf_by_id = load_all_weeks_with_perf()

    if not weeks:
        body = "<p>Noch keine Wochen-Daten vorhanden.</p>"
    else:
        blocks = []
        for w in weeks:
            rows_html = "\n".join(_week_row_html(r, perf_by_id.get(r["id"])) for r in w["rows"])
            blocks.append(
                f"  <section>\n"
                f"    <h2>KW {w['iso_week']}/{w['iso_year']} — {html.escape(w['date'])}</h2>\n"
                f"    <table class=\"week-table\">\n"
                f"      <thead>\n"
                f"        <tr><th>Engine</th><th>Paar</th><th>Bias</th><th>Status</th><th>Bewegung (7 Tage)</th></tr>\n"
                f"      </thead>\n"
                f"      <tbody>\n{rows_html}\n      </tbody>\n"
                f"    </table>\n"
                f"  </section>"
            )
        body = "\n\n".join(blocks)

    footer = _engine_summary_html(weeks, perf_by_id)
    from datetime import datetime
    generiert = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>5MG Analyzer — Verlauf</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ --accent: #2563eb; --accent-dark: #1e3a8a; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 900px;
          margin: 2rem auto; padding: 0 1rem; color: #222; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .sub {{ color: #666; margin-top: 0; margin-bottom: 2rem; font-size: 0.9rem; }}
  .sub a {{ color: var(--accent); }}
  section {{ margin-bottom: 2.5rem; }}
  h2 {{ border-bottom: 2px solid var(--accent); padding-bottom: 0.3rem; color: var(--accent-dark); }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: 0.5rem 0.7rem; border-bottom: 1px solid #ddd; }}
  th {{ background: #eef2ff; color: var(--accent-dark); font-weight: 600; }}
  tr:hover {{ background: #fafafa; }}
  .badge {{ display: inline-block; font-weight: 600; padding: 0.1rem 0.55rem;
            border-radius: 4px; font-size: 0.92em; white-space: nowrap; }}
  .badge.win   {{ color: #1a7f37; background: #e6f4ea; }}
  .badge.loss  {{ color: #cf222e; background: #fde8e8; }}
  .badge.open  {{ color: #9a6700; background: #fff6e0; }}
  .badge.error {{ color: #cf222e; background: #fde8e8; }}
  .badge.muted {{ color: #666;    background: #f2f2f2; font-weight: 500; }}
  .hinweis {{ color: #666; font-size: 0.88rem; margin-top: 0.8rem; }}
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
  <h1>5MG Analyzer — Verlauf</h1>
  <p class="sub">Wochen-Zeitreihe je Engine (Signal-Zeitpunkt-Performance) · <a href="../">zurück zur Übersicht</a> · generiert {generiert}</p>
{body}

{footer}
</body>
</html>
"""


def main() -> None:
    html_out = build_verlauf_html()
    if "--dry-run" in sys.argv:
        PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREVIEW_PATH.write_text(html_out, encoding="utf-8")
        weeks, _ = load_all_weeks_with_perf()
        print(f"Vorschau geschrieben: {PREVIEW_PATH}")
        print(f"{len(weeks)} Kalenderwoche(n) gefunden, KEIN Schreiben in verlauf/index.html, KEIN git.")
        return

    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html_out, encoding="utf-8")
    print(f"Geschrieben: {OUTPUT_HTML} (KEIN git - siehe publish_pages.py fuer den zentralen Commit)")


if __name__ == "__main__":
    main()
