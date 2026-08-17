"""SQLite persistence for run/quant-build reporting (PRD: Reporting section)."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("REPORT_DB_PATH", "data/reports.sqlite"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    model_series        TEXT NOT NULL,
    hf_model_name       TEXT,
    mflux_repo          TEXT,
    mflux_branch        TEXT,
    machine_type        TEXT,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    duration_s          REAL,
    status              TEXT NOT NULL DEFAULT 'running',
    force_hf_overwrite  INTEGER NOT NULL DEFAULT 0,
    expected_quants     INTEGER NOT NULL DEFAULT 0,
    error               TEXT
);

CREATE TABLE IF NOT EXISTS quant_builds (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES runs(id),
    quant               TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'building',
    total_size_bytes    INTEGER,
    text_encoder_bytes  INTEGER,
    transformer_bytes   INTEGER,
    vae_bytes           INTEGER,
    build_duration_s    REAL,
    upload_duration_s   REAL,
    hf_repo_id          TEXT
);

CREATE TABLE IF NOT EXISTS series_volumes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    model_series        TEXT NOT NULL,
    volume_id           TEXT NOT NULL,
    volume_name         TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    deleted_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_quant_builds_run_id ON quant_builds(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_model_series ON runs(model_series);
CREATE INDEX IF NOT EXISTS idx_series_volumes_model_series ON series_volumes(model_series);
"""


def init_db(db_path: Path | None = None) -> None:
    db_path = db_path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_connection(db_path: Path | None = None):
    db_path = db_path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
