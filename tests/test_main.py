import pytest
from fastapi.testclient import TestClient

import app.db as db_module
from app.db import init_db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "reports.sqlite"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    init_db(db_path)


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_models_supported(client):
    resp = client.get("/models_supported")
    assert resp.status_code == 200
    assert "dev" in resp.json()


def test_models_missing(client):
    resp = client.get("/models_missing")
    assert resp.status_code == 200
    body = resp.json()
    assert "missing" in body
    assert "complete" in body


def test_generate_unknown_model_404(client):
    resp = client.post("/generate", json={"config_stem": "Not-A-Real-Model"})
    assert resp.status_code == 404


def test_generate_known_model_dry_runs(client):
    resp = client.post("/generate", json={"config_stem": "Fibo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["dispatch"]["dispatched"] is False
    assert body["plan"]["model_stem"] == "Fibo"
    assert "run_id" in body


def test_generate_all_dry_runs(client):
    resp = client.post("/generate_all")
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert len(runs) > 0
    assert all(r["dispatch"]["dispatched"] is False for r in runs)


def test_report_run_callback_one_quant_of_many_is_partial(client):
    """Fibo has 4 missing quants -> expected_quants=4. One quant job
    reporting in should leave the run 'partial', not flip it to 'success' —
    this is the one-job-per-quant contract, not the old one-job-per-run
    'status' field (which would have let a single job set the whole run's
    outcome)."""
    gen_resp = client.post("/generate", json={"config_stem": "Fibo"})
    run_id = gen_resp.json()["run_id"]
    assert gen_resp.json()["plan"]["quants_to_build"] == ["q4", "q6", "q8", "bf16"]

    callback_resp = client.post(
        f"/report/run/{run_id}",
        json={
            "data": {
                "finished_at": "2026-08-17T01:00:00",
                "quant_builds": [
                    {
                        "quant": "q4",
                        "status": "uploaded",
                        "total_size_bytes": 5000,
                        "hf_repo_id": "mflux-community/fibo-mflux-q4",
                    }
                ],
            },
        },
    )
    assert callback_resp.status_code == 200
    body = callback_resp.json()
    assert body["status"] == "partial"
    assert len(body["quant_builds"]) == 1
    assert body["quant_builds"][0]["hf_repo_id"] == "mflux-community/fibo-mflux-q4"


def test_report_run_callback_all_quants_reported_is_success(client):
    gen_resp = client.post("/generate", json={"config_stem": "Fibo"})
    run_id = gen_resp.json()["run_id"]
    quants = gen_resp.json()["plan"]["quants_to_build"]

    for quant in quants:
        resp = client.post(
            f"/report/run/{run_id}",
            json={
                "data": {
                    "finished_at": "2026-08-17T01:00:00",
                    "quant_builds": [{"quant": quant, "status": "uploaded"}],
                },
            },
        )
        assert resp.status_code == 200

    assert resp.json()["status"] == "success"


def test_report_run_callback_any_failure_marks_run_failed(client):
    gen_resp = client.post("/generate", json={"config_stem": "Fibo"})
    run_id = gen_resp.json()["run_id"]
    quants = gen_resp.json()["plan"]["quants_to_build"]

    for i, quant in enumerate(quants):
        status = "failed" if i == 1 else "uploaded"
        resp = client.post(
            f"/report/run/{run_id}",
            json={
                "data": {
                    "finished_at": "2026-08-17T01:00:00",
                    "quant_builds": [{"quant": quant, "status": status}],
                },
            },
        )

    # even though the last job posted "uploaded", the run stays "failed"
    # because an earlier quant job reported "failed" -- this is the
    # last-writer-wins bug the derive-from-children design fixes.
    assert resp.json()["status"] == "failed"


def test_report_run_callback_unknown_run_404(client):
    resp = client.post(
        f"/report/run/99999", json={"data": {"finished_at": "2026-08-17T01:00:00"}}
    )
    assert resp.status_code == 404


def test_report_endpoint_default(client):
    client.post("/generate", json={"config_stem": "Fibo"})
    resp = client.get("/report")
    assert resp.status_code == 200
    body = resp.json()
    assert "runs" in body
    assert "summary" in body


def test_report_endpoint_by_run_id(client):
    gen_resp = client.post("/generate", json={"config_stem": "Fibo"})
    run_id = gen_resp.json()["run_id"]

    resp = client.get(f"/report?run_id={run_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == run_id


def test_report_endpoint_unknown_run_id_404(client):
    resp = client.get("/report?run_id=99999")
    assert resp.status_code == 404
