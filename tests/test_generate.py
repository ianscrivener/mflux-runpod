import pytest

import app.db as db_module
from app.db import init_db
from app.generate import (
    DispatchConfigError,
    UnknownModelError,
    cancel_run,
    dry_run_trigger,
    generate_one,
    resolve_generate_config,
)
from app.report import jobs_for_run, record_dispatched_job, run_detail


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "reports.sqlite"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    init_db(db_path)


@pytest.fixture(autouse=True)
def stub_models_hf(monkeypatch, tmp_path):
    """Isolate models_hf.json so tests don't depend on repo state."""
    data_path = tmp_path / "models_hf.json"
    monkeypatch.setattr("app.generate.load_models_hf", lambda: {"hf_models": []})


def test_resolve_generate_config_unknown_model():
    with pytest.raises(UnknownModelError):
        resolve_generate_config("Not-A-Real-Model")


def test_resolve_generate_config_applies_overrides():
    config = resolve_generate_config(
        "Fibo", quants=["q4"], mflux_branch="feature-branch"
    )
    assert config["quants"] == ["q4"]
    assert config["mflux_branch"] == "feature-branch"
    assert config["model_object"] == "FIBO"  # untouched fields still present


def test_resolve_generate_config_defaults_branch_to_main():
    config = resolve_generate_config("Fibo")
    assert config["mflux_branch"] == "main"


def test_dry_run_trigger_never_dispatches():
    result = dry_run_trigger("Fibo", run_id=1, plan={"x": 1})
    assert result["dispatched"] is False
    assert result["reason"] == "dry_run"


def test_generate_one_uses_dry_run_by_default():
    result = generate_one("Fibo")

    assert result["dispatch"]["dispatched"] is False
    assert result["plan"]["model_stem"] == "Fibo"
    assert set(result["plan"]["quants_to_build"]) == {"q4", "q6", "q8", "bf16"}

    detail = run_detail(result["run_id"])
    assert detail["model_series"] == "Fibo"
    assert detail["status"] == "running"


def test_generate_one_records_hf_model_name_from_config():
    result = generate_one("Fibo")
    assert result["plan"]["hf_model_name"] == "briaai/FIBO"


def test_generate_one_custom_trigger_fn_is_called():
    calls = []

    def spy_trigger(model_series, run_id, plan):
        calls.append((model_series, run_id, plan))
        return {"dispatched": True, "note": "test dispatch"}

    result = generate_one("Fibo", trigger_fn=spy_trigger)

    assert len(calls) == 1
    assert calls[0][0] == "Fibo"
    assert result["dispatch"]["dispatched"] is True


def test_generate_one_force_overwrite_includes_all_quants(monkeypatch):
    monkeypatch.setattr(
        "app.generate.load_models_hf",
        lambda: {
            "hf_models": [
                {"model_name": "mflux-community/fibo-mflux-q4"},
                {"model_name": "mflux-community/fibo-mflux-q6"},
                {"model_name": "mflux-community/fibo-mflux-q8"},
                {"model_name": "mflux-community/fibo-mflux-bf16"},
            ]
        },
    )

    result = generate_one("Fibo", force_hf_overwrite=False)
    assert result["plan"]["quants_to_build"] == []

    result = generate_one("Fibo", force_hf_overwrite=True)
    assert set(result["plan"]["quants_to_build"]) == {"q4", "q6", "q8", "bf16"}


def test_generate_one_makes_no_runpod_call(monkeypatch):
    """generate_one must never touch RunPod directly — volume creation
    belongs to a real trigger_fn, not the planning path. Fail loudly if
    that boundary is ever crossed again."""
    import app.runpod_volumes as runpod_volumes_module

    def poison(*args, **kwargs):
        raise AssertionError("generate_one must not call RunPod directly")

    monkeypatch.setattr(runpod_volumes_module, "create_volume", poison)
    monkeypatch.setattr(runpod_volumes_module, "list_volumes", poison)

    generate_one("Fibo")  # must not raise


def test_cancel_run_unknown_run_raises():
    with pytest.raises(UnknownModelError):
        cancel_run(999)


def test_cancel_run_requires_runpod_env(monkeypatch):
    monkeypatch.delenv("RUNNER_ENDPOINT_ID", raising=False)
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    result = generate_one("Fibo")
    with pytest.raises(DispatchConfigError):
        cancel_run(result["run_id"])


def test_cancel_run_cancels_every_dispatched_job(monkeypatch):
    monkeypatch.setenv("RUNNER_ENDPOINT_ID", "test-endpoint")
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")

    result = generate_one("Fibo")
    run_id = result["run_id"]
    record_dispatched_job(run_id, "q4", "job-1")
    record_dispatched_job(run_id, "q6", "job-2")

    cancelled_calls = []
    monkeypatch.setattr(
        "app.runpod_jobs.cancel_job",
        lambda endpoint_id, job_id: cancelled_calls.append((endpoint_id, job_id)),
    )

    outcome = cancel_run(run_id)

    assert set(outcome["cancelled"]) == {"job-1", "job-2"}
    assert outcome["errors"] == []
    assert {c[1] for c in cancelled_calls} == {"job-1", "job-2"}
    assert run_detail(run_id)["status"] == "cancelled"
    assert jobs_for_run(run_id) == []  # cancelled jobs excluded by default


def test_cancel_run_one_bad_cancel_does_not_stop_the_rest(monkeypatch):
    monkeypatch.setenv("RUNNER_ENDPOINT_ID", "test-endpoint")
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")

    result = generate_one("Fibo")
    run_id = result["run_id"]
    record_dispatched_job(run_id, "q4", "job-ok")
    record_dispatched_job(run_id, "q6", "job-fails")

    def flaky_cancel(endpoint_id, job_id):
        if job_id == "job-fails":
            raise RuntimeError("already finished")
        return {"status": "CANCELLED"}

    monkeypatch.setattr("app.runpod_jobs.cancel_job", flaky_cancel)

    outcome = cancel_run(run_id)

    assert outcome["cancelled"] == ["job-ok"]
    assert outcome["errors"] == [{"job_id": "job-fails", "error": "already finished"}]
    # Partial failure -- the run must NOT be marked terminally cancelled,
    # since job-fails might still genuinely be running. It stays in its
    # prior status so a caller can retry cancel_run() against it.
    assert run_detail(run_id)["status"] == "running"
    remaining = {j["quant"]: j["job_id"] for j in jobs_for_run(run_id)}
    assert remaining == {"q6": "job-fails"}  # still recorded, available to retry
