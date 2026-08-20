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
from app.report import run_detail


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


def test_generate_one_never_calls_dispatch_trigger_directly():
    """generate_one must never call dispatch_trigger itself — dispatch
    belongs to a real trigger_fn a caller supplies, not the planning path.
    dispatch_trigger currently always raises DispatchConfigError (see
    app/generate.py), so if generate_one's default dry_run_trigger path
    ever started calling it directly, this would fail loudly."""
    generate_one("Fibo")  # must not raise


def test_cancel_run_unknown_run_raises():
    with pytest.raises(UnknownModelError):
        cancel_run(999)


def test_cancel_run_not_implemented_for_known_run():
    """No dispatch mechanism exists to cancel against right now (RunPod's
    was removed; see app/generate.py) — a known run still always raises
    DispatchConfigError until a real worker is wired up."""
    result = generate_one("Fibo")
    with pytest.raises(DispatchConfigError):
        cancel_run(result["run_id"])
