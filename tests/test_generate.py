import httpx
import pytest

import app.db as db_module
from app.db import init_db
from app.generate import (
    DispatchConfigError,
    InvalidQuantsError,
    UnknownModelError,
    cancel_run,
    dispatch_trigger,
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


def test_resolve_generate_config_rejects_bad_quant_names():
    """Confirmed live 2026-08-20: a webapp form field with plain '4' (not
    'q4') sailed through to the worker and crashed deep inside
    build_and_upload_one_quant instead of failing fast here."""
    with pytest.raises(InvalidQuantsError, match="4"):
        resolve_generate_config("Fibo", quants=["4"])


def test_resolve_generate_config_accepts_real_quant_names():
    config = resolve_generate_config("Fibo", quants=["q4", "bf16"])
    assert config["quants"] == ["q4", "bf16"]


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


def test_generate_one_never_calls_dispatch_trigger_directly(monkeypatch):
    """generate_one must never call dispatch_trigger itself — dispatch
    belongs to a real trigger_fn a caller supplies, not the planning path.
    Poison dispatch_trigger so this fails loudly if that boundary is ever
    crossed (generate_one's default trigger_fn is dry_run_trigger, which
    should be the only thing that runs here)."""

    def poison(*args, **kwargs):
        raise AssertionError("generate_one must not call dispatch_trigger directly")

    monkeypatch.setattr("app.generate.dispatch_trigger", poison)
    generate_one("Fibo")  # must not raise


def test_dispatch_trigger_requires_worker_url(monkeypatch):
    monkeypatch.delenv("HF_WORKER_URL", raising=False)
    with pytest.raises(DispatchConfigError, match="HF_WORKER_URL"):
        dispatch_trigger("Fibo", run_id=1, plan={"quants_to_build": ["q4"], "force_hf_overwrite": False})


def test_dispatch_trigger_posts_one_build_per_quant(monkeypatch):
    monkeypatch.setenv("HF_WORKER_URL", "https://example-worker.hf.space")
    monkeypatch.setenv("HF_TOKEN", "hf-token-value")
    monkeypatch.setenv("WORKER_API_KEY", "secret-key")

    posted = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"queued": True, "queue_depth": 1}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            posted.append((url, headers, json))
            return FakeResponse()

    monkeypatch.setattr("httpx.Client", FakeClient)

    plan = {"quants_to_build": ["q4", "q8"], "force_hf_overwrite": False}
    result = dispatch_trigger("Fibo", run_id=42, plan=plan)

    assert result["dispatched"] is True
    assert result["run_id"] == 42
    assert [j["quant"] for j in result["jobs"]] == ["q4", "q8"]
    assert len(posted) == 2
    url, headers, body = posted[0]
    assert url == "https://example-worker.hf.space/build"
    assert headers == {"Authorization": "Bearer hf-token-value", "X-Worker-Api-Key": "secret-key"}
    assert body["config_stem"] == "Fibo"
    assert body["quant"] == "q4"
    assert body["run_id"] == 42
    assert body["already_published"] is False
    assert body["config"]["model_object"] == "FIBO"  # the real loaded config, not a stub


def test_dispatch_trigger_reports_partial_failure(monkeypatch):
    """A failure partway through the loop must not surface as a bare httpx
    exception -- the caller needs to know which quants (if any) already
    queued successfully before the one that failed."""
    monkeypatch.setenv("HF_WORKER_URL", "https://example-worker.hf.space")
    monkeypatch.setenv("HF_TOKEN", "hf-token-value")
    monkeypatch.delenv("WORKER_API_KEY", raising=False)

    calls = []

    class FakeResponse:
        def __init__(self, quant):
            self._quant = quant

        def raise_for_status(self):
            if self._quant == "q8":
                raise httpx.HTTPStatusError("boom", request=None, response=None)

        def json(self):
            return {"queued": True, "queue_depth": 1}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            calls.append(json["quant"])
            return FakeResponse(json["quant"])

    monkeypatch.setattr("httpx.Client", FakeClient)

    plan = {"quants_to_build": ["q4", "q8", "bf16"], "force_hf_overwrite": False}
    with pytest.raises(DispatchConfigError, match=r"q8.*q4.*bf16") as exc_info:
        dispatch_trigger("Fibo", run_id=1, plan=plan)
    assert "q4" in str(exc_info.value)  # successfully queued, mentioned
    assert "bf16" in str(exc_info.value)  # never attempted, mentioned

    # q4 was queued before the q8 failure; bf16 was never attempted.
    assert calls == ["q4", "q8"]


def test_cancel_run_unknown_run_raises():
    with pytest.raises(UnknownModelError):
        cancel_run(999)


def test_cancel_run_not_implemented_for_known_run():
    """The HF Spaces worker has no cancel endpoint yet (see app/generate.py)
    — a known run still always raises DispatchConfigError."""
    result = generate_one("Fibo")
    with pytest.raises(DispatchConfigError):
        cancel_run(result["run_id"])
