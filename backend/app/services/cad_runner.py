"""Runs CAD generation in an isolated worker process with a timeout.

A crash or hang inside the CAD kernel cannot take the API down: the worker
pool is rebuilt and the request gets a clear error. Set
FLOWCAD_CAD_ISOLATION=inline to run in-process (used by tests).
"""

from __future__ import annotations

import multiprocessing
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

from app import config
from app.schemas.design import GenerationResult, Issue, ValidationReport

_pool: ProcessPoolExecutor | None = None
_lock = threading.Lock()


def _worker(params: dict[str, Any], out_dir: str) -> dict:
    from app.services.cad_engine import generate_bracket

    return generate_bracket(params, out_dir=Path(out_dir)).model_dump(mode="json")


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn"))
        return _pool


def _reset_pool() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


def _failure(params: dict[str, Any], code: str, message: str) -> GenerationResult:
    return GenerationResult(
        ok=False, design_id="", params=None,
        reports=[ValidationReport(stage="geometry", ok=False, checks_run=["build"], errors=[Issue(code=code, message=message)])],
    )


def run_generation(params: dict[str, Any]) -> GenerationResult:
    out_dir = config.output_dir()
    if config.cad_isolation() == "inline":
        from app.services.cad_engine import generate_bracket

        return generate_bracket(params, out_dir=out_dir)

    timeout = config.cad_timeout_s()
    try:
        future = _get_pool().submit(_worker, params, str(out_dir))
        return GenerationResult.model_validate(future.result(timeout=timeout))
    except FutureTimeout:
        _reset_pool()
        return _failure(params, "cad_timeout", f"CAD generation took longer than {timeout:g} s and was stopped.")
    except BrokenProcessPool:
        _reset_pool()
        return _failure(params, "cad_worker_crashed", "The CAD worker process crashed while building this design.")


def warm_up() -> None:
    """Start the worker and import CadQuery ahead of the first request."""
    if config.cad_isolation() != "inline":
        _get_pool().submit(_noop).result(timeout=120)


def _noop() -> bool:
    import cadquery  # noqa: F401

    return True


def shutdown() -> None:
    _reset_pool()
