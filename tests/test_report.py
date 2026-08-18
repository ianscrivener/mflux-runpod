import pytest

import app.db as db_module
from app.db import init_db
from app.report import (
    add_quant_build,
    create_run,
    finish_run,
    recent_runs,
    run_detail,
    runs_for_series,
    summary,
    update_run_status_from_children,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "reports.sqlite"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    init_db(db_path)
    return db_path


def test_create_and_finish_run():
    run_id = create_run("Fibo", "2026-08-17T00:00:00", hf_model_name="briaai/FIBO")
    finish_run(run_id, "2026-08-17T00:05:00", duration_s=300.0, status="success")

    detail = run_detail(run_id)
    assert detail["model_series"] == "Fibo"
    assert detail["status"] == "success"
    assert detail["duration_s"] == 300.0
    assert detail["quant_builds"] == []


def test_run_detail_missing_returns_none():
    assert run_detail(9999) is None


def test_add_quant_build_appears_in_run_detail():
    run_id = create_run("Fibo", "2026-08-17T00:00:00")
    add_quant_build(
        run_id,
        "q4",
        status="uploaded",
        total_size_bytes=1000,
        hf_repo_id="mflux-community/fibo-mflux-q4",
    )

    detail = run_detail(run_id)
    assert len(detail["quant_builds"]) == 1
    assert detail["quant_builds"][0]["quant"] == "q4"
    assert detail["quant_builds"][0]["hf_repo_id"] == "mflux-community/fibo-mflux-q4"


def test_recent_runs_orders_newest_first():
    id1 = create_run("Fibo", "2026-08-17T00:00:00")
    id2 = create_run("Qwen-Image", "2026-08-17T00:01:00")

    runs = recent_runs()
    assert [r["id"] for r in runs] == [id2, id1]


def test_recent_runs_respects_limit():
    for i in range(5):
        create_run(f"Series-{i}", "2026-08-17T00:00:00")

    runs = recent_runs(limit=2)
    assert len(runs) == 2


def test_runs_for_series_filters():
    create_run("Fibo", "2026-08-17T00:00:00")
    create_run("Qwen-Image", "2026-08-17T00:01:00")
    create_run("Fibo", "2026-08-17T00:02:00")

    runs = runs_for_series("Fibo")
    assert len(runs) == 2
    assert all(r["model_series"] == "Fibo" for r in runs)


def test_summary_run_counts_and_quant_stats():
    id1 = create_run("Fibo", "2026-08-17T00:00:00")
    finish_run(id1, "2026-08-17T00:05:00", 300.0, "success")
    id2 = create_run("Qwen-Image", "2026-08-17T00:10:00")
    finish_run(id2, "2026-08-17T00:12:00", 120.0, "failed", error="boom")

    add_quant_build(id1, "q4", build_duration_s=60.0, upload_duration_s=10.0, total_size_bytes=2000)
    add_quant_build(id1, "q4", build_duration_s=80.0, upload_duration_s=20.0, total_size_bytes=4000)

    result = summary()
    assert result["run_counts"] == {"success": 1, "failed": 1}

    q4_stats = next(s for s in result["quant_stats"] if s["quant"] == "q4")
    assert q4_stats["n"] == 2
    assert q4_stats["avg_build_duration_s"] == 70.0
    assert q4_stats["avg_total_size_bytes"] == 3000


# ---- update_run_status_from_children: one-job-per-quant status derivation ----


def test_update_run_status_from_children_partial_when_not_all_reported():
    run_id = create_run("Fibo", "2026-08-17T00:00:00", expected_quants=3)
    add_quant_build(run_id, "q4", status="uploaded")

    status = update_run_status_from_children(run_id, "2026-08-17T00:05:00")

    assert status == "partial"
    assert run_detail(run_id)["status"] == "partial"


def test_update_run_status_from_children_success_when_all_reported():
    run_id = create_run("Fibo", "2026-08-17T00:00:00", expected_quants=2)
    add_quant_build(run_id, "q4", status="uploaded")
    status = update_run_status_from_children(run_id, "2026-08-17T00:03:00")
    assert status == "partial"

    add_quant_build(run_id, "q6", status="uploaded")
    status = update_run_status_from_children(run_id, "2026-08-17T00:05:00")
    assert status == "success"


def test_update_run_status_from_children_failed_wins_even_if_reported_first():
    """Regression: one job's 'failed' must not be overwritten by a later
    job's 'uploaded' for the same run — the old finish_run(status=...) design
    let whichever callback landed last silently win."""
    run_id = create_run("Fibo", "2026-08-17T00:00:00", expected_quants=2)
    add_quant_build(run_id, "q4", status="failed")
    update_run_status_from_children(run_id, "2026-08-17T00:03:00")

    add_quant_build(run_id, "q6", status="uploaded")
    status = update_run_status_from_children(run_id, "2026-08-17T00:05:00")

    assert status == "failed"


def test_update_run_status_from_children_unknown_run_raises():
    with pytest.raises(ValueError, match="not found"):
        update_run_status_from_children(9999, "2026-08-17T00:00:00")


def test_create_run_defaults_expected_quants_to_zero():
    run_id = create_run("Fibo", "2026-08-17T00:00:00")
    assert run_detail(run_id)["expected_quants"] == 0


def test_dump_all_returns_runs_with_builds_and_counts(tmp_path, monkeypatch):
    """/report/dump's backing function must return every run with its nested
    quant_builds, plus counts and summary -- it's the "give me everything"
    report, unlike recent_runs() which is limited."""
    from app.report import add_quant_build, create_run, dump_all

    rid = create_run(model_series="Fibo", started_at="2026-08-18T00:00:00Z", expected_quants=2)
    add_quant_build(rid, "q8", status="uploaded", build_duration_s=12.5)
    add_quant_build(rid, "bf16", status="failed")

    d = dump_all()

    assert d["counts"] == {"runs": 1, "quant_builds": 2, "series_volumes": 0}
    assert [b["quant"] for b in d["runs"][0]["quant_builds"]] == ["q8", "bf16"]
    assert "summary" in d and d["generated_at"]
