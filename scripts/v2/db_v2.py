#!/usr/bin/env python3
"""
db_v2.py — 5MG v2, eigene kleine DB-Helper.

Nutzt DIESELBE hermes.db wie das produktive v1-System (kein separates
File), aber ausschließlich die neuen v2-Tabellen (market_pulse_checks_v2,
entry_readiness_checks_v2). Reine additive Erweiterung über das
bestehende db.py-Muster — Verbindung/fcntl-Lock werden 1:1 von db.py
wiederverwendet (write_lock()/get_conn()), NICHT neu implementiert, um
kein zweites, konkurrierendes Lock-Verfahren auf derselbe Datei zu
riskieren.

Andere v2-Module importieren NUR db_v2 (nicht db.py direkt) für
Schreibzugriffe, damit alle v2-Schreibzugriffe an einer Stelle
gebündelt bleiben. Lesezugriffe auf v1-Tabellen (z.B.
weekly_engine_signals) laufen weiterhin über db.query() direkt, da
db_v2.query() ohnehin nur an db.query() delegiert - kein Unterschied,
aber semantisch klarer, wenn v2-Module ausschließlich db_v2 importieren.
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path("/home/pi/hermes2/scripts")
sys.path.insert(0, str(SCRIPTS_DIR))
import db  # zentrales hermes.db (Verbindung, fcntl-Lock, Selbsttest)

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS market_pulse_checks_v2 (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    pair              TEXT NOT NULL,
    bias              TEXT NOT NULL,
    engine            TEXT NOT NULL,
    kurs_bei_signal   REAL,
    kurs_aktuell      REAL,
    bewegung_pct      REAL,
    einordnung        TEXT   -- 'bestätigt sich' / 'läuft dagegen' / 'neutral'
);
CREATE INDEX IF NOT EXISTS idx_mpc_v2_ts   ON market_pulse_checks_v2(ts);
CREATE INDEX IF NOT EXISTS idx_mpc_v2_pair ON market_pulse_checks_v2(pair);

CREATE TABLE IF NOT EXISTS entry_readiness_checks_v2 (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    pair                  TEXT NOT NULL,
    bias                  TEXT NOT NULL,
    market_pulse_status   TEXT,
    gate_passed           INTEGER DEFAULT 0,  -- 0/1
    h4_confirmed          INTEGER,            -- 0/1
    h1_confirmed          INTEGER,            -- 0/1
    pullback_confirmed    INTEGER,            -- 0/1
    momentum_confirmed    INTEGER,            -- 0/1
    readiness             INTEGER             -- 0-4, Summe der vier confirmed-Flags
);
CREATE INDEX IF NOT EXISTS idx_erc_v2_ts   ON entry_readiness_checks_v2(ts);
CREATE INDEX IF NOT EXISTS idx_erc_v2_pair ON entry_readiness_checks_v2(pair);

-- Erfolgsauswertung (evaluate_v2.py), eigener Codepfad analog zu
-- evaluate_signals.py/signal_performance (v1), aber EIGENES, einfacheres
-- Schema - eine Zeile pro (pair, bias, engine)-Kombination statt pro
-- weekly_signal_id (v2 hat keine solche Fremd-ID). Kein separates
-- Status-Feld (OPEN/DONE) wie bei v1 - ebeneN_ergebnis IS NULL bedeutet
-- "noch nicht ausgewertet", ergebnis 'WIN'/'LOSS' bedeutet ausgewertet.
CREATE TABLE IF NOT EXISTS signal_performance_v2 (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    pair                        TEXT NOT NULL,
    bias                        TEXT NOT NULL,
    engine                      TEXT NOT NULL,
    ebene1_kurs_signal          REAL,
    ebene1_kurs_plus7d          REAL,
    ebene1_ergebnis             TEXT,   -- 'WIN' / 'LOSS', NULL = noch nicht ausgewertet
    ebene1_ausgewertet_am       TEXT,
    ebene2_kurs_gate_bestaetigt REAL,
    ebene2_kurs_plus7d          REAL,
    ebene2_ergebnis             TEXT,   -- 'WIN' / 'LOSS', NULL = noch nicht ausgewertet
    ebene2_ausgewertet_am       TEXT,
    UNIQUE(pair, bias, engine)
);
CREATE INDEX IF NOT EXISTS idx_spv2_pair ON signal_performance_v2(pair);
"""


def init_db_v2():
    """Idempotent, wie db.init_db() - legt die v2-Tabellen an, falls sie
    noch nicht existieren. Fasst KEINE v1-Tabelle an."""
    with db.write_lock():
        conn = db.get_conn()
        try:
            conn.executescript(SCHEMA_V2)
            conn.commit()
        finally:
            conn.close()


def execute(sql: str, params: tuple = ()) -> int:
    """Schreibzugriff - delegiert an db.execute() (gleicher Lock/gleiche
    Verbindung wie v1), NUR fuer v2-Tabellen zu verwenden."""
    return db.execute(sql, params)


def query(sql: str, params: tuple = ()) -> list:
    """Lesezugriff - delegiert an db.query()."""
    return db.query(sql, params)


# --- Selbsttest: python3 db_v2.py ---
if __name__ == "__main__":
    init_db_v2()
    rows = query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_v2'"
    )
    print("v2-Tabellen:", [r["name"] for r in rows])
