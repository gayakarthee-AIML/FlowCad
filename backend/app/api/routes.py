"""HTTP endpoints.

The language model only ever produces parameter proposals (see
services.interpreter). These routes validate them deterministically, run
trusted CAD code, and commit a revision only when every check passes.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from app import config
from app.config import AI, LIMITS
from app.schemas.ai import Interpretation
from app.schemas.api import (
    DesignResponse,
    DownloadFile,
    EditRequest,
    GenerateRequest,
    Health,
    InterpretRequest,
    Revision,
)
from app.schemas.design import DEFAULT_PARAMS, BracketParams, GenerationResult
from app.services.cad_runner import run_generation
from app.services.interpreter import interpret
from app.services.store import store
from app.services.validator import validate_parameters

router = APIRouter()

FILENAME_RE = re.compile(r"^[0-9a-f]{32}\.(stl|step)$")
MEDIA_TYPES = {"stl": "model/stl", "step": "model/step"}


def _pkg_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


@router.get("/health", response_model=Health)
def health() -> Health:
    cq_version = _pkg_version("cadquery") if find_spec("cadquery") else None
    return Health(
        status="ok",
        cad_engine={
            "available": cq_version is not None,
            "name": "CadQuery",
            "version": cq_version,
            "kernel": f"OCP {_pkg_version('cadquery-ocp')}" if cq_version else None,
            "isolation": config.cad_isolation(),
            "formats": ["stl", "step"],
        },
        ai={
            "provider": "DigitalOcean Serverless Inference",
            "configured": AI.configured,
            "model": AI.model,
            "fallback": "rule-based parser",
        },
    )


@router.get("/api/design/default")
def default_design() -> dict[str, Any]:
    return {
        "params": DEFAULT_PARAMS.model_dump(),
        "limits": {k: {"min": lo, "max": hi} for k, (lo, hi) in LIMITS.items()},
        "materials": list(config.DENSITY_G_CM3),
    }


@router.post("/api/design/interpret", response_model=Interpretation)
def interpret_design(req: InterpretRequest) -> Interpretation:
    current = (req.current_params or DEFAULT_PARAMS).model_dump()
    return interpret(req.text, current)


def _respond(resp: DesignResponse) -> JSONResponse:
    code = 422 if resp.status == "rejected" else 200
    return JSONResponse(status_code=code, content=resp.model_dump(mode="json"))


def _first_error(reports) -> str:
    for r in reports:
        if r.errors:
            return r.errors[0].message
    return "The design could not be generated."


def _preserved_note(session_id: str) -> str:
    cur = store.session(session_id).current
    return f" The last valid design (revision {cur.number}) is unchanged." if cur else ""


def _build(session_id: str, params: dict[str, Any], source: str, note: str | None,
           summary: str, interpretation: Interpretation | None = None) -> JSONResponse:
    session = store.session(session_id)

    # Cheap deterministic check first; only valid parameters reach the CAD worker.
    parsed, param_report = validate_parameters(params)
    if not param_report.ok:
        store.reject(session_id, params, summary)
        return _respond(DesignResponse(
            status="rejected", message=_first_error([param_report]) + _preserved_note(session_id),
            revision=session.current, attempt_reports=[param_report],
            interpretation=interpretation, history=session.history,
        ))

    result: GenerationResult = run_generation(parsed.model_dump())
    if not result.ok:
        store.reject(session_id, params, summary)
        return _respond(DesignResponse(
            status="rejected", message=_first_error(result.reports) + _preserved_note(session_id),
            revision=session.current, attempt_reports=result.reports,
            interpretation=interpretation, history=session.history,
        ))

    revision = Revision(
        number=store.next_number(session_id),
        params=result.params,
        design_id=result.design_id,
        files=[DownloadFile(**f.model_dump(), url=f"/api/files/{f.filename}") for f in result.files],
        metadata=result.metadata,
        reports=result.reports,
        source=source,  # type: ignore[arg-type]
        note=note,
        created_at=datetime.now(timezone.utc),
    )
    store.commit(session_id, revision, summary)
    warnings = [w.message for r in result.reports for w in r.warnings]
    message = f"Revision {revision.number} generated and validated."
    if warnings:
        message += " " + " ".join(warnings)
    return _respond(DesignResponse(status="committed", message=message, revision=revision,
                                   attempt_reports=result.reports, interpretation=interpretation,
                                   history=session.history))


@router.post("/api/design/generate", response_model=DesignResponse,
             responses={422: {"model": DesignResponse, "description": "Rejected; last valid revision preserved"}})
def generate_design(req: GenerateRequest) -> JSONResponse:
    return _build(req.session_id, req.params, source="controls", note=req.note,
                  summary=req.note or "Dimensions set from controls")


@router.post("/api/design/edit", response_model=DesignResponse,
             responses={422: {"model": DesignResponse, "description": "Rejected; last valid revision preserved"}})
def edit_design(req: EditRequest) -> JSONResponse:
    session = store.session(req.session_id)
    base: BracketParams = session.current.params if session.current else (req.current_params or DEFAULT_PARAMS)
    interp = interpret(req.text, base.model_dump())

    if interp.status != "ok":
        return _respond(DesignResponse(status=interp.status, message=interp.message,
                                       revision=session.current, interpretation=interp, history=session.history))

    return _build(req.session_id, interp.proposed_params or {}, source="prompt", note=req.text[:300],
                  summary=req.text[:120], interpretation=interp)


@router.get("/api/files/{filename}")
def get_file(filename: str, download: bool = Query(False)) -> FileResponse:
    m = FILENAME_RE.fullmatch(filename)
    if not m:
        raise HTTPException(status_code=404, detail="File not found.")
    root = config.output_dir().resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    ext = m.group(1)
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    return FileResponse(
        path, media_type=MEDIA_TYPES[ext], headers=headers,
        filename=f"flowcad-bracket-{filename[:8]}.{ext}" if download else None,
        content_disposition_type="attachment" if download else "inline",
    )
