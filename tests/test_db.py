import sqlite3

from app.db import SCHEMA, get_connection, init_db


def test_init_db_creates_tables(tmp_path):
    db_path = tmp_path / "reports.sqlite"
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"runs", "quant_builds"} <= tables


def test_insert_run_and_quant_build(tmp_path):
    db_path = tmp_path / "reports.sqlite"
    init_db(db_path)

    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO runs (model_series, started_at) VALUES (?, ?)",
            ("Qwen-Image", "2026-08-17T00:00:00"),
        )
        run_id = cur.lastrowid
        conn.execute(
            "INSERT INTO quant_builds (run_id, quant) VALUES (?, ?)",
            (run_id, "q4"),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        assert row["model_series"] == "Qwen-Image"
        assert row["status"] == "running"

        build = conn.execute(
            "SELECT * FROM quant_builds WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert build["quant"] == "q4"
        assert build["status"] == "building"


def test_schema_is_idempotent(tmp_path):
    db_path = tmp_path / "reports.sqlite"
    init_db(db_path)
    init_db(db_path)  # must not raise
