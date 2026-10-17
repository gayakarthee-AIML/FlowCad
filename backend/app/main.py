"""FlowCAD API.

    cd backend && uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.api.routes import router
from app.services import cad_runner


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Import the CAD kernel in the worker in the background so the first
    # request is fast, without delaying startup.
    threading.Thread(target=_safe_warm_up, daemon=True).start()
    yield
    cad_runner.shutdown()


def _safe_warm_up() -> None:
    try:
        cad_runner.warm_up()
    except Exception:  # surfaced on the first real request instead
        pass


app = FastAPI(
    title="FlowCAD API",
    description="Natural language to validated parametric CAD (L-shaped mounting bracket).",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(router)
