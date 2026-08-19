"""Run/quant-build reporting (PRD: /report). Pure reads over db.py's SQLite tables."""

from datetime import datetime, timezone

from app.db import get_connection


def create_run(
    model_series: str,
    started_at: str,
    expected_quants: int = 0,
    hf_model_name: str | None = None,
    mflux_repo: str | None = None,
    mflux_branch: str | None = None,
    machine_type: str | None = None,
    force_hf_overwrite: bool = False,
) -> int:
    """expected_quants is how many quant_builds rows this run should end up
    with — one GPU job per quant means N jobs write children of one `runs`
    row, so the run's aggregate status must be derived from those children
    (see update_run_status_from_children) rather than trusted from any single
    job's callback, or last-writer-wins clobbers earlier jobs' outcomes."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO runs
                (model_series, hf_model_name, mflux_repo, mflux_branch, machine_type,
                 started_at, force_hf_overwrite, expected_quants)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                model_series,
                hf_model_name,
                mflux_repo,
                mflux_branch,
                machine_type,
                started_at,
                int(force_hf_overwrite),
                expected_quants,
            ),
        )
        conn.commit()
        return cur.lastrowid


def finish_run(
    run_id: int,
    finished_at: str,
    duration_s: float,
    status: str,
    error: str | None = None,
) -> None:
    """Explicit terminal write — use only for a single-job-per-run caller
    (e.g. /generate's own bookkeeping, tests). A multi-job-per-run Runner
    should use update_run_status_from_children instead, since N quant jobs
    calling this directly would make the last one to finish clobber the
    others' outcome."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE runs SET finished_at = ?, duration_s = ?, status = ?, error = ?
               WHERE id = ?""",
            (finished_at, duration_s, status, error, run_id),
        )
        conn.commit()


def update_run_status_from_children(run_id: int, finished_at: str, error: str | None = None) -> str:
    """Derive and write a run's aggregate status from its quant_builds rows,
    instead of trusting any single reporting job's opinion. Returns the
    derived status.

    Rule: "failed" if any child failed; "success" if every expected quant has
    reported and none failed; "partial" otherwise (some but not all expected
    quants have reported, none failed yet).
    """
    with get_connection() as conn:
        run = conn.execute(
            "SELECT expected_quants, started_at FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise ValueError(f"run {run_id} not found")

        rows = conn.execute(
            "SELECT status FROM quant_builds WHERE run_id = ?", (run_id,)
        ).fetchall()
        statuses = [r["status"] for r in rows]

        if any(s == "failed" for s in statuses):
            derived = "failed"
        elif run["expected_quants"] > 0 and len(statuses) >= run["expected_quants"]:
            derived = "success"
        else:
            derived = "partial"

        # Was never actually computed on this path (only the unused
        # finish_run() did) -- confirmed live, 2026-08-18: a finished run's
        # duration_s stayed null forever. Best-effort: a malformed timestamp
        # shouldn't fail the whole callback, just leave duration_s unset.
        duration_s = None
        try:
            duration_s = (
                datetime.fromisoformat(finished_at) - datetime.fromisoformat(run["started_at"])
            ).total_seconds()
        except (ValueError, TypeError):
            pass

        conn.execute(
            "UPDATE runs SET finished_at = ?, duration_s = ?, status = ?, error = ? WHERE id = ?",
            (finished_at, duration_s, derived, error, run_id),
        )
        conn.commit()
        return derived


def add_quant_build(
    run_id: int,
    quant: str,
    status: str = "building",
    total_size_bytes: int | None = None,
    text_encoder_bytes: int | None = None,
    transformer_bytes: int | None = None,
    vae_bytes: int | None = None,
    build_duration_s: float | None = None,
    upload_duration_s: float | None = None,
    hf_repo_id: str | None = None,
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO quant_builds
                (run_id, quant, status, total_size_bytes, text_encoder_bytes,
                 transformer_bytes, vae_bytes, build_duration_s, upload_duration_s, hf_repo_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                quant,
                status,
                total_size_bytes,
                text_encoder_bytes,
                transformer_bytes,
                vae_bytes,
                build_duration_s,
                upload_duration_s,
                hf_repo_id,
            ),
        )
        conn.commit()
        return cur.lastrowid


def recent_runs(limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def run_detail(run_id: int) -> dict | None:
    with get_connection() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            return None
        builds = conn.execute(
            "SELECT * FROM quant_builds WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        return {**dict(run), "quant_builds": [dict(b) for b in builds]}


def runs_for_series(model_series: str, limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM runs WHERE model_series = ? ORDER BY id DESC LIMIT ?",
            (model_series, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def dump_all() -> dict:
    """Full raw dump of every table -- runs (each with its quant_builds) and
    series_volumes -- for offline inspection/debugging. Unlike recent_runs()
    this is deliberately unlimited: it's a "give me everything" report, so
    expect it to grow with run history."""
    with get_connection() as conn:
        runs = [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY id").fetchall()]
        builds_by_run: dict[int, list[dict]] = {}
        for b in conn.execute("SELECT * FROM quant_builds ORDER BY id").fetchall():
            builds_by_run.setdefault(b["run_id"], []).append(dict(b))
        for r in runs:
            r["quant_builds"] = builds_by_run.get(r["id"], [])

        volumes = [
            dict(v)
            for v in conn.execute("SELECT * FROM series_volumes ORDER BY id").fetchall()
        ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "runs": len(runs),
            "quant_builds": sum(len(r["quant_builds"]) for r in runs),
            "series_volumes": len(volumes),
        },
        "runs": runs,
        "series_volumes": volumes,
        "summary": summary(),
    }


def clear_runs() -> dict:
    """Delete every runs + quant_builds row (schema untouched). Does NOT
    touch series_volumes -- those track real RunPod resources that still
    exist regardless of the generation log, and deleting the row without
    deleting the actual volume would just orphan it from tracking. Returns
    the counts deleted, for confirmation."""
    with get_connection() as conn:
        quant_builds_deleted = conn.execute("DELETE FROM quant_builds").rowcount
        runs_deleted = conn.execute("DELETE FROM runs").rowcount
        conn.commit()
    return {"runs_deleted": runs_deleted, "quant_builds_deleted": quant_builds_deleted}


def record_dispatched_job(run_id: int, quant: str, job_id: str) -> int:
    """One row per RunPod job actually submitted -- see cancel_run() for why."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO dispatched_jobs (run_id, quant, job_id, dispatched_at) VALUES (?, ?, ?, ?)",
            (run_id, quant, job_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid


def jobs_for_run(run_id: int, include_cancelled: bool = False) -> list[dict]:
    query = "SELECT * FROM dispatched_jobs WHERE run_id = ?"
    if not include_cancelled:
        query += " AND cancelled_at IS NULL"
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(query, (run_id,)).fetchall()]


def mark_jobs_cancelled(run_id: int, job_ids: list[str]) -> None:
    with get_connection() as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.executemany(
            "UPDATE dispatched_jobs SET cancelled_at = ? WHERE run_id = ? AND job_id = ?",
            [(now, run_id, job_id) for job_id in job_ids],
        )
        conn.commit()


def mark_run_cancelled(run_id: int) -> None:
    """Explicit override, not derived from quant_builds like
    update_run_status_from_children -- a cancel is a human decision, not
    something inferred from job outcomes."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE runs SET status = 'cancelled', finished_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), run_id),
        )
        conn.commit()


def summary() -> dict:
    """Aggregate stats: run counts by status, avg build/upload duration by quant."""
    with get_connection() as conn:
        run_counts = {
            row["status"]: row["n"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM runs GROUP BY status"
            ).fetchall()
        }
        quant_stats = [
            dict(row)
            for row in conn.execute(
                """SELECT quant,
                          COUNT(*) AS n,
                          AVG(build_duration_s) AS avg_build_duration_s,
                          AVG(upload_duration_s) AS avg_upload_duration_s,
                          AVG(total_size_bytes) AS avg_total_size_bytes
                   FROM quant_builds
                   GROUP BY quant"""
            ).fetchall()
        ]
        return {"run_counts": run_counts, "quant_stats": quant_stats}
