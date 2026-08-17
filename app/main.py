"""Orchestrator API (PRD: (1) Orchestrator - CPU)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.models_hf import load_models_hf, update_models_hf
from app.models_missing import compute_missing, load_configs, load_overrides
from app.models_supported import load_models_supported


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="mflux-runpod orchestrator", lifespan=lifespan)


@app.get("/models_supported")
def models_supported():
    return load_models_supported()


@app.get("/models_hf")
def models_hf():
    return load_models_hf()


@app.post("/models_hf/update")
def models_hf_update():
    return update_models_hf()


@app.get("/models_missing")
def models_missing():
    configs = load_configs()
    hf_manifest = load_models_hf()
    overrides = load_overrides()
    return compute_missing(configs, hf_manifest, overrides)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ping")
def ping():
    return {"ping": "pong"}
