"""Pydantic schemas for the parametric L-bracket and its validation report."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Material = Literal["aluminum_6061", "steel_mild", "stainless_304", "pla"]


class BracketParams(BaseModel):
    """Every parameter of the one supported part, in millimetres.

    Strict mode: numbers must be numbers (no "100" strings, no booleans),
    infinities and NaN are rejected, and unknown keys are an error. Range and
    geometric feasibility checks live in ``services.validator`` so they can
    return engineering-language messages.
    """

    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)

    base_length_mm: float = Field(description="Length of the horizontal base plate, measured from the back face of the vertical plate.")
    base_width_mm: float = Field(description="Width of both plates (the extrusion depth).")
    vertical_height_mm: float = Field(description="Overall height of the vertical plate, measured from the underside of the base.")
    thickness_mm: float = Field(description="Material thickness of both plates.")
    hole_diameter_mm: float = Field(description="Diameter of every mounting hole.")
    holes_per_plate: int = Field(default=2, description="Mounting holes per plate, in one row across the width (0-4).")
    edge_margin_mm: float = Field(default=8.0, description="Minimum solid material between any hole edge and a plate edge, the bend, or a neighbouring hole.")
    fillet_radius_mm: float = Field(default=0.0, description="Inside bend fillet radius. 0 means a sharp inside corner.")
    material: Material = "aluminum_6061"


DEFAULT_PARAMS = BracketParams(
    base_length_mm=100.0,
    base_width_mm=50.0,
    vertical_height_mm=70.0,
    thickness_mm=5.0,
    hole_diameter_mm=8.0,
    holes_per_plate=2,
    edge_margin_mm=8.0,
    fillet_radius_mm=0.0,
    material="aluminum_6061",
)


class Issue(BaseModel):
    code: str
    message: str
    field: str | None = None
    severity: Literal["error", "warning"] = "error"


class ValidationReport(BaseModel):
    """Result of one validation stage.

    ``checks_run`` lists what was actually checked, so a report can never
    claim a pass for checks that did not execute.
    """

    stage: Literal["parameters", "geometry", "export"]
    ok: bool
    errors: list[Issue] = []
    warnings: list[Issue] = []
    checks_run: list[str] = []


class ExportedFile(BaseModel):
    format: Literal["stl", "step"]
    filename: str
    size_bytes: int


class ModelMetadata(BaseModel):
    volume_mm3: float
    mass_g_estimate: float
    bounding_box_mm: tuple[float, float, float]
    hole_count: int
    engine: str
    generation_ms: int


class GenerationResult(BaseModel):
    """Outcome of generate_bracket. ``ok`` is True only when parameter,
    geometry and export validation all ran and passed."""

    ok: bool
    design_id: str
    params: BracketParams | None
    reports: list[ValidationReport]
    files: list[ExportedFile] = []
    metadata: ModelMetadata | None = None
