"""Schemas for natural-language interpretation.

The language model may only return this shape. It cannot name operations,
code, files or anything other than bracket parameter values.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.design import Material, ValidationReport


class ProposedChanges(BaseModel):
    """Only the parameters the user asked to change. All optional; unknown
    keys are rejected. Lengths are millimetres."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    base_length_mm: float | None = None
    base_width_mm: float | None = None
    vertical_height_mm: float | None = None
    thickness_mm: float | None = None
    hole_diameter_mm: float | None = None
    holes_per_plate: int | None = None
    edge_margin_mm: float | None = None
    fillet_radius_mm: float | None = None
    material: Material | None = None

    def as_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


class ModelReply(BaseModel):
    """The exact JSON object the model must produce."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "clarify", "unsupported"]
    changes: ProposedChanges = Field(default_factory=ProposedChanges)
    message: str = Field(default="", max_length=600)


InterpretSource = Literal["gemma", "rules"]


class Interpretation(BaseModel):
    status: Literal["ok", "clarify", "unsupported"]
    source: InterpretSource
    message: str = ""
    changes: dict = {}
    changed_fields: list[str] = []
    proposed_params: dict | None = None
    parameter_report: ValidationReport | None = None
    ai_error: str | None = None
    latency_ms: int | None = None
