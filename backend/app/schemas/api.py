"""Request and response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ai import Interpretation
from app.schemas.design import BracketParams, ExportedFile, ModelMetadata, ValidationReport

SessionId = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$", description="Client-generated id that groups one user's revisions.")


class InterpretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    current_params: BracketParams | None = None


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = SessionId
    # Raw dict on purpose: the deterministic validator turns bad values into
    # engineering-language messages instead of generic schema errors.
    params: dict[str, Any]
    note: str | None = Field(default=None, max_length=300)


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = SessionId
    text: str = Field(min_length=1, max_length=2000)
    # Used only when the session has no committed revision yet.
    current_params: BracketParams | None = None


class DownloadFile(ExportedFile):
    url: str


class Revision(BaseModel):
    number: int
    params: BracketParams
    design_id: str
    files: list[DownloadFile]
    metadata: ModelMetadata
    reports: list[ValidationReport]
    source: Literal["prompt", "controls", "default"]
    note: str | None = None
    created_at: datetime


class HistoryEntry(BaseModel):
    revision: int | None  # None for rejected attempts
    status: Literal["committed", "rejected"]
    params: dict[str, Any] | None
    summary: str
    created_at: datetime


class DesignResponse(BaseModel):
    """Common shape for generate and edit.

    status:
      committed   - a new valid revision was created
      rejected    - validation failed; ``revision`` is the preserved last valid one
      clarify     - the request was ambiguous; nothing was built
      unsupported - the request was not for this bracket; nothing was built
    """

    status: Literal["committed", "rejected", "clarify", "unsupported"]
    message: str
    revision: Revision | None = None
    attempt_reports: list[ValidationReport] = []
    interpretation: Interpretation | None = None
    history: list[HistoryEntry] = []


class Health(BaseModel):
    status: Literal["ok"]
    cad_engine: dict[str, Any]
    ai: dict[str, Any]
