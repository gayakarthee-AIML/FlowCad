"""Deterministic CAD compiler: BracketParams -> B-rep solid -> STL/STEP.

Uses CadQuery (OpenCASCADE kernel). The language model never reaches this
module; it only ever receives validated ``BracketParams``.

Run directly to generate the default bracket:
    python -m app.services.cad_engine
"""

from __future__ import annotations

import math
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import cadquery as cq
from cadquery.occ_impl.shapes import Shape

from app import config
from app.schemas.design import (
    DEFAULT_PARAMS,
    BracketParams,
    ExportedFile,
    GenerationResult,
    Issue,
    ModelMetadata,
    ValidationReport,
)
from app.services.layout import hole_layout
from app.services.validator import validate_parameters

try:
    from importlib.metadata import version as _pkg_version

    _OCP_VERSION = _pkg_version("cadquery-ocp")
except Exception:  # pragma: no cover - version string is informational only
    _OCP_VERSION = "unknown"

ENGINE_NAME = f"CadQuery {cq.__version__} (OCP {_OCP_VERSION})"

# Small overshoot so cutting tools pass cleanly through each plate face and
# never leave a zero-thickness skin.
_CUT_OVERSHOOT_MM = 1.0

# OCCT bounding boxes are padded by a small gap, so compare with a tolerance
# that is far below any meaningful manufacturing dimension.
_BBOX_TOLERANCE_MM = 0.01


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def build_bracket(p: BracketParams) -> Shape:
    """Build the bracket as a single solid.

    The L profile is drawn in the XZ plane and extruded along +Y, so the two
    plates are one continuous solid (no union of overlapping boxes). Holes
    are subtracted with real boolean cuts.
    """
    L, W, H, t = p.base_length_mm, p.base_width_mm, p.vertical_height_mm, p.thickness_mm

    profile = [(0, 0), (L, 0), (L, t), (t, t), (t, H), (0, H)]
    # The XZ workplane's normal is -Y, so a negative extrude goes along +Y.
    body = cq.Workplane("XZ").polyline(profile).close().extrude(-W)

    if p.fillet_radius_mm > 0:
        body = (
            body.edges("|Y")
            .edges(cq.selectors.NearestToPointSelector((t, W / 2, t)))
            .fillet(p.fillet_radius_mm)
        )

    for h in hole_layout(p):
        ax = cq.Vector(*h.axis)
        start = cq.Vector(*h.center) - ax * _CUT_OVERSHOOT_MM
        tool = cq.Solid.makeCylinder(h.diameter / 2, t + 2 * _CUT_OVERSHOOT_MM, start, ax)
        body = body.cut(tool)

    return body.val()


def expected_volume_mm3(p: BracketParams) -> float:
    """Analytic volume of the intended part, used to verify the solid."""
    L, W, H, t, r = p.base_length_mm, p.base_width_mm, p.vertical_height_mm, p.thickness_mm, p.fillet_radius_mm
    profile_area = L * t + (H - t) * t
    fillet_area = r * r - math.pi * r * r / 4  # material added in the inside corner
    holes = 2 * p.holes_per_plate * math.pi * (p.hole_diameter_mm / 2) ** 2 * t
    return (profile_area + fillet_area) * W - holes


def validate_geometry(shape: Shape | None, p: BracketParams) -> ValidationReport:
    checks: list[str] = []
    errors: list[Issue] = []

    def fail(code: str, message: str) -> ValidationReport:
        errors.append(Issue(code=code, message=message))
        return ValidationReport(stage="geometry", ok=False, errors=errors, checks_run=checks)

    checks.append("non_null")
    if shape is None or shape.isNull():
        return fail("null_shape", "CAD generation produced no geometry.")

    checks.append("brep_valid")
    if not shape.isValid():
        return fail("invalid_brep", "The generated solid failed the CAD kernel's validity check.")

    checks.append("single_solid")
    solids = shape.Solids()
    if len(solids) != 1:
        return fail("solid_count", f"Expected one connected solid but the CAD kernel produced {len(solids)}.")

    checks.append("bounding_box")
    bb = shape.BoundingBox()
    want = (p.base_length_mm, p.base_width_mm, p.vertical_height_mm)
    got = (bb.xlen, bb.ylen, bb.zlen)
    if any(abs(a - b) > _BBOX_TOLERANCE_MM for a, b in zip(got, want)):
        return fail("bounding_box", f"Overall size {tuple(round(v, 3) for v in got)} mm does not match the requested {want} mm.")

    checks.append("volume")
    vol, exp = shape.Volume(), expected_volume_mm3(p)
    if exp <= 0 or abs(vol - exp) / exp > 0.005:
        return fail("volume", f"Solid volume {vol:.1f} mm³ differs from the expected {exp:.1f} mm³; a boolean operation may have failed.")

    checks.append("hole_topology")
    cyl_faces = sum(1 for f in shape.Faces() if f.geomType() == "CYLINDER")
    expected_cyl = 2 * p.holes_per_plate + (1 if p.fillet_radius_mm > 0 else 0)
    if cyl_faces != expected_cyl:
        return fail("hole_topology", f"Expected {expected_cyl} cylindrical faces (holes and fillet) but found {cyl_faces}.")

    return ValidationReport(stage="geometry", ok=True, checks_run=checks)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def _stl_triangle_count(path: Path) -> int:
    """Return the triangle count of an ASCII or binary STL, or 0 if unreadable."""
    size = path.stat().st_size
    with path.open("rb") as fh:
        head = fh.read(84)
        if len(head) == 84:
            n = int.from_bytes(head[80:84], "little")
            if n > 0 and 84 + 50 * n == size:
                return n
    if head.lstrip().startswith(b"solid"):
        with path.open("rb") as fh:
            return sum(line.lstrip().startswith(b"facet") for line in fh)
    return 0


def _export_one(shape: Shape, fmt: str, path: Path) -> None:
    if fmt == "stl":
        cq.exporters.export(shape, str(path), exportType="STL",
                            tolerance=config.STL_TOLERANCE_MM,
                            angularTolerance=config.STL_ANGULAR_TOLERANCE_RAD)
    else:
        cq.exporters.export(shape, str(path), exportType="STEP")


def export_model(
    shape: Shape,
    design_id: str,
    out_dir: Path,
    formats: tuple[str, ...] = ("stl", "step"),
    exporter: Callable[[Shape, str, Path], None] | None = None,
) -> tuple[list[ExportedFile], ValidationReport]:
    """Write each format atomically and verify the file before reporting it.

    STL is required; STEP is optional (a STEP failure is a warning, and the
    STEP download is simply not offered).
    """
    exporter = exporter or _export_one
    files: list[ExportedFile] = []
    errors: list[Issue] = []
    warnings: list[Issue] = []
    checks: list[str] = []

    for fmt in formats:
        final = out_dir / f"{design_id}.{fmt}"
        tmp = out_dir / f".{design_id}.{fmt}.tmp"
        checks.append(f"{fmt}_written")
        problem: str | None = None
        try:
            exporter(shape, fmt, tmp)
            if not tmp.exists():
                problem = "the file was not created"
            elif tmp.stat().st_size == 0:
                problem = "the file is empty"
            elif fmt == "stl":
                checks.append("stl_triangles")
                if _stl_triangle_count(tmp) == 0:
                    problem = "the file contains no triangles"
            elif fmt == "step":
                checks.append("step_header")
                with tmp.open("rb") as fh:
                    if not fh.read(64).startswith(b"ISO-10303-21"):
                        problem = "the file is not a valid STEP (ISO-10303-21) file"
            if problem is None:
                os.replace(tmp, final)
                files.append(ExportedFile(format=fmt, filename=final.name, size_bytes=final.stat().st_size))
        except Exception as exc:  # exporter or filesystem failure
            problem = f"{type(exc).__name__}: {exc}"
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

        if problem is not None:
            issue = Issue(code=f"{fmt}_export_failed", message=f"{fmt.upper()} export failed: {problem}.",
                          severity="error" if fmt == "stl" else "warning")
            (errors if fmt == "stl" else warnings).append(issue)

    if "stl" in formats and not any(f.format == "stl" for f in files) and not errors:
        errors.append(Issue(code="stl_export_failed", message="STL export failed."))

    return files, ValidationReport(stage="export", ok=not errors, errors=errors, warnings=warnings, checks_run=checks)


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def generate_bracket(
    data: BracketParams | dict[str, Any],
    out_dir: Path | None = None,
    exporter: Callable[[Shape, str, Path], None] | None = None,
) -> GenerationResult:
    """Validate -> build -> verify geometry -> export -> verify files.

    Never raises for bad input or CAD failures; problems are reported.
    """
    started = time.perf_counter()
    design_id = uuid.uuid4().hex
    out = out_dir or config.output_dir()
    out.mkdir(parents=True, exist_ok=True)

    params, param_report = validate_parameters(data)
    reports = [param_report]
    if params is None or not param_report.ok:
        return GenerationResult(ok=False, design_id=design_id, params=params, reports=reports)

    shape: Shape | None
    try:
        shape = build_bracket(params)
    except Exception as exc:
        reports.append(ValidationReport(stage="geometry", ok=False, checks_run=["build"], errors=[
            Issue(code="cad_build_failed", message=f"The CAD kernel could not build this bracket ({type(exc).__name__}: {exc}).")
        ]))
        return GenerationResult(ok=False, design_id=design_id, params=params, reports=reports)

    geo_report = validate_geometry(shape, params)
    reports.append(geo_report)
    if not geo_report.ok:
        return GenerationResult(ok=False, design_id=design_id, params=params, reports=reports)

    files, export_report = export_model(shape, design_id, out, exporter=exporter or _export_one)
    reports.append(export_report)

    bb = shape.BoundingBox()
    volume = shape.Volume()
    metadata = ModelMetadata(
        volume_mm3=round(volume, 2),
        mass_g_estimate=round(volume / 1000 * config.DENSITY_G_CM3[params.material], 1),
        bounding_box_mm=(round(bb.xlen, 2), round(bb.ylen, 2), round(bb.zlen, 2)),
        hole_count=2 * params.holes_per_plate,
        engine=ENGINE_NAME,
        generation_ms=int((time.perf_counter() - started) * 1000),
    )
    return GenerationResult(
        ok=export_report.ok, design_id=design_id, params=params,
        reports=reports, files=files, metadata=metadata,
    )


if __name__ == "__main__":  # pragma: no cover
    result = generate_bracket(DEFAULT_PARAMS)
    print(result.model_dump_json(indent=2))
