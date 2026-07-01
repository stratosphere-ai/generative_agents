"""FastAPI application entrypoint.

Wires the API routers and serves the static frontend at the root path.
Run with:  uvicorn app.main:app --port 8100
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import STATIC_DIR, settings
from .db import init_db
from .routers import admin, markets, policies, pool

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Prediction-Market Insurance Platform",
    description="一键投保 — insurance priced and backed by prediction markets.",
    version="0.1.0",
)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    logging.getLogger(__name__).info(
        "insurance platform ready (provider=%s, loading=%.2f, seed=%.0f)",
        settings.provider,
        settings.loading_factor,
        settings.seed_capital,
    )


app.include_router(markets.router)
app.include_router(policies.router)
app.include_router(pool.router)
app.include_router(admin.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": settings.provider}


# Serve the frontend (index.html, policies.html, admin.html, app.js, styles.css).
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
