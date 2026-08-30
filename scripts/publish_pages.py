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

import evaluate_signals  # signal_performance-Status/Trefferquote (Fix 29.08.2026,
                          # siehe reports/signal_performance_stand_2026-08-29.md) -
                          # load_stored_results() liest nur die DB, KEIN live yfinance-
                          # Abruf; kein Zirkelimport, da evaluate_signals.py
                          # publish_pages nur lokal in main() importiert, nicht auf
                          # Modulebene.

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


def _status_badge(status: str | None, correct) -> str:
    """WIN/LOSS/OPEN/kein-Entry-Kennzeichnung fuer eine Ebene (sig_* oder
    entry_*), als farbiges <span> (Fix 30.08.2026 - Layout-Ueberarbeitung).
    status=None (noch keine signal_performance-Zeile fuer dieses Signal,
    z.B. frisch aus dem Freitagslauf, evaluate_signals noch nicht
    gelaufen) wird wie OPEN behandelt - faktisch korrekt: zu frueh fuer
    eine Aussage."""
    if status == "DONE":
        return ('<span class="badge win">✅ WIN</span>' if correct == 1
                else '<span class="badge loss">❌ LOSS</span>')
    if status == "NO_ENTRY":
        return '<span class="badge none">– kein Entry</span>'
    if status == "ERROR":
        return '<span class="badge error">⚠️ Fehler</span>'
    return '<span class="badge open">⏳ OPEN</span>'


def _perf_lookup(results: list[dict]) -> dict[int, dict]:
    return {res["signal"]["id"]: res["perf"] for res in results}


def _week_table(week: dict, perf_by_id: dict[int, dict]) -> str:
    """data-label je <td> (Fix 30.08.2026): traegt auf schmalen Bildschirmen
    die per CSS (td::before) angezeigte Spaltenbeschriftung, wenn die
    Tabelle unter 600px zu gestapelten Karten wird - siehe <style>."""
    rows_html = []
    for r in week["rows"]:
        top = "🏆 Top" if r["is_top_signal"] else "–"
        konflikt = f"⚠️ {html.escape(r['conflict_note'])}" if r["in_conflict"] else "–"
        fq = r["final_quality"]
        fq_str = f"{fq:.1f}" if fq is not None else "–"
        perf = perf_by_id.get(r["id"])
        sig_badge = _status_badge(perf["sig_status"] if perf else None,
                                   perf.get("sig_direction_correct") if perf else None)
        entry_badge = _status_badge(perf["entry_status"] if perf else None,
                                     perf.get("entry_direction_correct") if perf else None)
        rows_html.append(
            "        <tr>"
            f'<td data-label="Engine">{html.escape(r["engine"])}</td>'
            f'<td data-label="Paar">{html.escape(r["pair"])}</td>'
            f'<td data-label="Bias">{html.escape(r["bias"])}</td>'
            f'<td data-label="Final Quality">{fq_str}</td>'
            f'<td data-label="Top-Signal">{top}</td>'
            f'<td data-label="Konflikt">{konflikt}</td>'
            f'<td data-label="Signal-Status">{sig_badge}</td>'
            f'<td data-label="Entry-Status">{entry_badge}</td>'
            "</tr>"
        )
    return "\n".join(rows_html)


def _hitrate_html(n_correct: int, n_total: int) -> str:
    """HTML-Pendant zu evaluate_signals._fmt_pct() (Fix 30.08.2026) -
    dieselbe MIN_FUER_PROZENT/MIN_STICHPROBE-Logik/Zahlen, aber als
    Badge statt Klartext-Suffix."""
    if n_total < evaluate_signals.MIN_FUER_PROZENT:
        return (f'{n_correct} von {n_total} '
                f'<span class="badge muted">kein Prozent bei n&lt;{evaluate_signals.MIN_FUER_PROZENT}</span>')
    pct = 100 * n_correct / n_total
    out = f'{n_correct}/{n_total} ({pct:.1f}%)'
    if n_total < evaluate_signals.MIN_STICHPROBE:
        out += ' <span class="badge muted">NICHT BELASTBAR</span>'
    return out


def _layer_section_html(title: str, counts_line: str, done_rows: list[dict], layer: str) -> str:
    """Ein <div class="layer-status"> je Ebene: DONE/OPEN(-Zahlen),
    Trefferquote, Pro-Paar-Tabelle (Fix 30.08.2026 - ersetzt den rohen
    <pre>-Telegram-Text durch echtes HTML, gleiche zugrundeliegende
    Berechnung wie build_weekly_report())."""
    n = len(done_rows)
    n_correct = sum(1 for r in done_rows if r["perf"][f"{layer}_direction_correct"] == 1)

    pair_rows = []
    for pair, (w, t) in sorted(evaluate_signals._pair_breakdown(done_rows, layer).items()):
        pair_rows.append(f"          <tr><td>{html.escape(pair)}</td><td>{w}/{t}</td></tr>")

    body = f'      <p class="counts">{counts_line}</p>\n'
    if n == 0:
        body += '      <p class="hitrate">Noch keine abgeschlossenen Fälle.</p>\n'
    else:
        body += f'      <p class="hitrate">Trefferquote: <strong>{_hitrate_html(n_correct, n)}</strong></p>\n'
        if pair_rows:
            body += (
                '      <table class="pair-table">\n'
                '        <tr><th>Paar</th><th>Win/Total</th></tr>\n'
                + "\n".join(pair_rows) + "\n"
                '      </table>\n'
            )

    return (
        '    <div class="layer-status">\n'
        f'      <h3>{html.escape(title)}</h3>\n'
        f'{body}'
        '    </div>\n'
    )


def _correlation_html(l1_done: list[dict], l2_done: list[dict]) -> str:
    """Klar strukturierter Block (Fix 30.08.2026, ueberarbeitet): eigene
    Ueberschriftszeile, Ebene 1/Ebene 2 als getrennte Listenpunkte statt
    Fliesstext, Merksatz als eigene hervorgehobene Schlusszeile darunter.
    Inhalt/Zahlen unveraendert identisch zu build_weekly_report()."""
    combos1 = evaluate_signals._distinct_combos(l1_done)
    combos2 = evaluate_signals._distinct_combos(l2_done)
    if not l1_done and not l2_done:
        return ""

    items = []
    if l1_done:
        combo_list = ", ".join(f"{html.escape(p)} {html.escape(b)}" for p, b in sorted(combos1))
        items.append(f"        <li>Ebene 1: {len(l1_done)} Fälle, aber nur {len(combos1)} "
                      f"unterschiedliche Paar/Bias-Kombination(en) ({combo_list}).</li>")
    if l2_done:
        combo_list = ", ".join(f"{html.escape(p)} {html.escape(b)}" for p, b in sorted(combos2))
        items.append(f"        <li>Ebene 2: {len(l2_done)} Fälle, aber nur {len(combos2)} "
                      f"unterschiedliche Paar/Bias-Kombination(en) ({combo_list}).</li>")

    return (
        '    <div class="correlation-note">\n'
        '      <p class="correlation-title">⚠️ <strong>Korrelations-Hinweis</strong></p>\n'
        '      <ul>\n' + "\n".join(items) + '\n      </ul>\n'
        '      <p class="correlation-conclusion">Fallzahl ≠ unabhängige Tests. Bei wenigen '
        'Kombinationen sind wiederholte Wochen-Messungen derselben Markt-These, keine '
        'unabhängigen Beweise.</p>\n'
        '    </div>\n'
    )


def _status_footer_html(results: list[dict]) -> str:
    """Gesamt-Status-Sektion als strukturiertes HTML (Fix 30.08.2026,
    ersetzt den rohen <pre>-Telegram-Text). Gleiche Zahlen/Berechnung wie
    evaluate_signals.build_weekly_report() (Telegram-Bericht) - nur die
    Darstellung unterscheidet sich."""
    if not results:
        return (
            '  <section class="status-footer">\n'
            '    <h2>Gesamt-Status (signal_performance)</h2>\n'
            '    <p>Noch keine signal_performance-Daten vorhanden.</p>\n'
            '  </section>'
        )

    l1_done = [r for r in results if r["perf"]["sig_status"] == "DONE"]
    l1_open = [r for r in results if r["perf"]["sig_status"] == "OPEN"]
    l2_done = [r for r in results if r["perf"]["entry_status"] == "DONE"]
    l2_open = [r for r in results if r["perf"]["entry_status"] == "OPEN"]
    l2_no_entry = [r for r in results if r["perf"]["entry_status"] == "NO_ENTRY"]

    sec1 = _layer_section_html(
        "Ebene 1 — Signal-Zeitpunkt",
        f"DONE: {len(l1_done)} &nbsp;|&nbsp; OPEN: {len(l1_open)}",
        l1_done, "sig",
    )
    sec2 = _layer_section_html(
        "Ebene 2 — Erster Entry",
        f"DONE: {len(l2_done)} &nbsp;|&nbsp; OPEN: {len(l2_open)} &nbsp;|&nbsp; NO_ENTRY: {len(l2_no_entry)}",
        l2_done, "entry",
    )
    correlation = _correlation_html(l1_done, l2_done)

    return (
        '  <section class="status-footer">\n'
        '    <h2>Gesamt-Status (signal_performance)</h2>\n'
        f'{sec1}{sec2}{correlation}'
        '  </section>'
    )


def render_html(weeks: list[dict], results: list[dict]) -> str:
    perf_by_id = _perf_lookup(results)

    if not weeks:
        body = "<p>Noch keine Wochen-Engine-Daten vorhanden.</p>"
    else:
        blocks = []
        for w in weeks:
            blocks.append(
                f"  <section>\n"
                f"    <h2>KW {w['iso_week']}/{w['iso_year']} — {html.escape(w['date'])}</h2>\n"
                f"    <table class=\"week-table\">\n"
                f"      <thead>\n"
                f"        <tr><th>Engine</th><th>Paar</th><th>Bias</th>"
                f"<th>Final Quality</th><th>Top-Signal</th><th>Konflikt</th>"
                f"<th>Signal-Status</th><th>Entry-Status</th></tr>\n"
                f"      </thead>\n"
                f"      <tbody>\n"
                f"{_week_table(w, perf_by_id)}\n"
                f"      </tbody>\n"
                f"    </table>\n"
                f"  </section>"
            )
        body = "\n\n".join(blocks)

    # Gesamt-Status ueber ALLE bisher ausgewerteten Faelle - dieselbe
    # Berechnung wie im woechentlichen Telegram-Bericht
    # (evaluate_signals.build_weekly_report), nicht nur die letzten
    # MAX_WEEKS Karten oben. Seit Fix 30.08.2026 als strukturiertes HTML
    # statt rohem <pre>-Telegram-Text (siehe _status_footer_html()).
    footer = _status_footer_html(results)

    generiert = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>5MG Analyzer — Wochen-Engines</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ --accent: #2563eb; --accent-dark: #1e3a8a; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 900px;
          margin: 2rem auto; padding: 0 1rem; color: #222; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .sub {{ color: #666; margin-top: 0; margin-bottom: 2rem; font-size: 0.9rem; }}
  section {{ margin-bottom: 2.5rem; }}
  h2 {{ border-bottom: 2px solid var(--accent); padding-bottom: 0.3rem; color: var(--accent-dark); }}
  h3 {{ color: var(--accent-dark); font-size: 1.05rem; margin-bottom: 0.4rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: 0.5rem 0.7rem; border-bottom: 1px solid #ddd; }}
  th {{ background: #eef2ff; color: var(--accent-dark); font-weight: 600; }}
  tr:hover {{ background: #fafafa; }}

  /* WIN/LOSS/OPEN-Badges (Fix 30.08.2026) - dezente Akzentfarben */
  .badge {{ display: inline-block; font-weight: 600; padding: 0.1rem 0.55rem;
            border-radius: 4px; font-size: 0.92em; white-space: nowrap; }}
  .badge.win   {{ color: #1a7f37; background: #e6f4ea; }}
  .badge.loss  {{ color: #cf222e; background: #fde8e8; }}
  .badge.open  {{ color: #9a6700; background: #fff6e0; }}
  .badge.none  {{ color: #666;    background: #f2f2f2; }}
  .badge.error {{ color: #cf222e; background: #fde8e8; }}
  .badge.muted {{ color: #666;    background: #f2f2f2; font-weight: 500; }}

  /* Gesamt-Status-Block als strukturiertes HTML (Fix 30.08.2026) */
  .layer-status {{ margin-bottom: 1.5rem; }}
  .layer-status:last-of-type {{ margin-bottom: 1rem; }}
  .counts {{ margin: 0.2rem 0; }}
  .hitrate {{ margin: 0.2rem 0 0.6rem 0; }}
  .pair-table {{ width: 100%; max-width: 320px; }}
  .pair-table th, .pair-table td {{ padding: 0.3rem 0.6rem; }}
  .correlation-note {{ background: #fff8e6; border-left: 4px solid #b58105;
                        padding: 0.75rem 1rem; border-radius: 4px; margin-top: 1rem; }}
  .correlation-title {{ margin: 0 0 0.5rem 0; }}
  .correlation-note ul {{ margin: 0 0 0.6rem 0; padding-left: 1.3rem; }}
  .correlation-note li {{ margin: 0.25rem 0; }}
  .correlation-conclusion {{ margin: 0.6rem 0 0 0; font-weight: 600;
                              border-top: 1px solid rgba(0,0,0,0.1); padding-top: 0.6rem; }}

  /* Gestapelte Karten statt gequetschter Tabelle unter 600px (Fix 30.08.2026) */
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
    .pair-table {{ max-width: 100%; }}
  }}
</style>
</head>
<body>
  <h1>5MG Analyzer — Wochen-Engines</h1>
  <p class="sub">Basis-/Fluss-/Kombi-Signal je Kalenderwoche · generiert {generiert}</p>
{body}

{footer}
</body>
</html>
"""


def build_index_html() -> str:
    return render_html(load_recent_weeks(MAX_WEEKS), evaluate_signals.load_stored_results())


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
