"""Deterministic parameter validation for the L-bracket.

No language model is involved here. Every check that runs is recorded in
``ValidationReport.checks_run``.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import ValidationError

from app.config import LABELS, LIMITS, RECOMMENDED_EDGE_DISTANCE_FACTOR
from app.schemas.design import BracketParams, Issue, ValidationReport
from app.services.layout import free_flange_mm, hole_layout


def _n(x: float) -> str:
    """Format a number for messages: 100.0 -> '100', 7.25 -> '7.25'."""
    return f"{x:g}"


def _schema_issues(err: ValidationError) -> list[Issue]:
    issues: list[Issue] = []
    for e in err.errors():
        field = str(e["loc"][0]) if e["loc"] else None
        label = LABELS.get(field or "", field or "input")
        kind = e["type"]
        if kind == "missing":
            msg = f"Missing value: the {label} is required."
            code = "missing"
        elif kind == "extra_forbidden":
            msg = f"Unsupported parameter '{field}'. FlowCAD only supports the L-bracket parameters."
            code = "unsupported_parameter"
        elif kind == "finite_number":
            msg = f"Invalid value: the {label} must be a finite number."
            code = "not_finite"
        elif kind == "literal_error":
            msg = f"Unsupported {label}. Choose one of: aluminum_6061, steel_mild, stainless_304, pla."
            code = "unsupported_value"
        else:
            msg = f"Invalid value for the {label}: {e['msg']}."
            code = "invalid_type"
        issues.append(Issue(code=code, message=msg, field=field))
    return issues


def validate_parameters(data: BracketParams | dict[str, Any]) -> tuple[BracketParams | None, ValidationReport]:
    """Validate raw or parsed parameters.

    Returns the parsed parameters (or ``None`` if the schema check failed)
    and a report. ``report.ok`` is True only if every check ran and passed.
    """
    checks: list[str] = ["schema"]

    if isinstance(data, BracketParams):
        params = data
    else:
        try:
            params = BracketParams.model_validate(data)
        except ValidationError as err:
            return None, ValidationReport(stage="parameters", ok=False, errors=_schema_issues(err), checks_run=checks)

    errors: list[Issue] = []
    warnings: list[Issue] = []

    # 1. Finite, positive and within configured limits.
    checks.append("ranges")
    for field, (lo, hi) in LIMITS.items():
        value = getattr(params, field)
        label = LABELS[field]
        if not math.isfinite(value):
            errors.append(Issue(code="not_finite", field=field, message=f"Invalid value: the {label} must be a finite number."))
        elif field in ("holes_per_plate", "fillet_radius_mm") and value < 0:
            errors.append(Issue(code="negative", field=field, message=f"Invalid design: the {label} cannot be negative."))
        elif field not in ("holes_per_plate", "fillet_radius_mm") and value <= 0:
            errors.append(Issue(code="not_positive", field=field, message=f"Invalid design: the {label} must be greater than 0 mm (got {_n(value)} mm)."))
        elif value < lo or value > hi:
            unit = "" if field == "holes_per_plate" else " mm"
            errors.append(Issue(code="out_of_range", field=field, message=f"Invalid design: the {label} must be between {_n(lo)}{unit} and {_n(hi)}{unit} (got {_n(value)}{unit})."))

    if errors:
        # Geometry checks are meaningless on out-of-range numbers.
        return params, ValidationReport(stage="parameters", ok=False, errors=errors, checks_run=checks)

    L, W, H = params.base_length_mm, params.base_width_mm, params.vertical_height_mm
    t, d, m, r, n = params.thickness_mm, params.hole_diameter_mm, params.edge_margin_mm, params.fillet_radius_mm, params.holes_per_plate

    # 2. Each plate must extend beyond the bend (thickness + fillet).
    checks.append("flange_length")
    for plate, field, outer in (("base", "base_length_mm", L), ("vertical", "vertical_height_mm", H)):
        if free_flange_mm(params, plate) <= 0:
            fillet_note = f" plus the {_n(r)} mm bend fillet" if r > 0 else ""
            errors.append(Issue(
                code="flange_too_short", field=field,
                message=(f"Invalid design: the {LABELS[field]} ({_n(outer)} mm) must be greater than the "
                         f"{_n(t)} mm thickness{fillet_note}, otherwise there is no {plate} plate left. "
                         f"Increase the {LABELS[field]} or reduce the thickness."),
            ))

    if r > 0:
        checks.append("fillet_feasibility")
        # Covered by flange_length above; recorded so the report is explicit.

    if n > 0 and not errors:
        # 3. Hole must be smaller than the faces it sits on.
        checks.append("hole_vs_face")
        for plate in ("base", "vertical"):
            flange = free_flange_mm(params, plate)
            if d >= flange or d >= W:
                limit = min(flange, W)
                errors.append(Issue(
                    code="hole_too_large", field="hole_diameter_mm",
                    message=(f"Invalid design: the {_n(d)} mm hole does not fit on the {plate} plate, whose flat "
                             f"face is only {_n(limit)} mm across. Reduce the hole diameter or enlarge the plate."),
                ))

        # 4. Edge margin along each flange (to the free end and to the bend).
        checks.append("edge_margin_along_flange")
        if not any(e.code == "hole_too_large" for e in errors):
            for plate, field in (("base", "base_length_mm"), ("vertical", "vertical_height_mm")):
                flange = free_flange_mm(params, plate)
                need = d + 2 * m
                if flange < need:
                    errors.append(Issue(
                        code="edge_margin_violation", field="edge_margin_mm",
                        message=(f"Invalid design: the {_n(d)} mm holes on the {plate} plate need {_n(need)} mm of flat "
                                 f"plate ({_n(m)} mm of material on each side), but only {_n(flange)} mm is available "
                                 f"beyond the bend. Reduce the hole diameter or edge margin, or increase the {LABELS[field]}."),
                    ))

            # 5. Edge margin across the width, including between holes.
            checks.append("edge_margin_across_width")
            need_w = n * d + (n + 1) * m
            if W < need_w:
                errors.append(Issue(
                    code="edge_margin_violation", field="hole_diameter_mm",
                    message=(f"Invalid design: the selected hole diameter does not leave enough material at the plate edge. "
                             f"{n} holes of {_n(d)} mm with {_n(m)} mm margins need a width of {_n(need_w)} mm, "
                             f"but the bracket is {_n(W)} mm wide. Reduce the hole diameter or the number of holes, "
                             f"reduce the edge margin, or widen the bracket."),
                ))

        # 6. Every computed hole centre lies on its mounting face.
        if not errors:
            checks.append("hole_centres_inside_faces")
            for h in hole_layout(params):
                x, y, z = h.center
                inside_y = d / 2 <= y <= W - d / 2
                if h.plate == "base":
                    inside_main = t + r + d / 2 <= x <= L - d / 2
                else:
                    inside_main = t + r + d / 2 <= z <= H - d / 2
                if not (inside_y and inside_main):
                    errors.append(Issue(
                        code="hole_outside_face", field="hole_diameter_mm",
                        message=f"Invalid design: a hole on the {h.plate} plate would fall outside the flat mounting face. Adjust the hole size or plate dimensions.",
                    ))

            # Advisory only: recommended edge distance from hole centre.
            checks.append("recommended_edge_distance")
            centre_to_edge = m + d / 2
            recommended = RECOMMENDED_EDGE_DISTANCE_FACTOR * d
            if centre_to_edge + 1e-9 < recommended:
                warnings.append(Issue(
                    code="edge_distance_below_recommended", field="edge_margin_mm", severity="warning",
                    message=(f"Holes sit {_n(round(centre_to_edge, 2))} mm (centre) from the nearest edge; "
                             f"{_n(RECOMMENDED_EDGE_DISTANCE_FACTOR)} x diameter ({_n(round(recommended, 2))} mm) is a common "
                             f"minimum for loaded fasteners. The part is valid, but consider a larger edge margin."),
                ))

    return params, ValidationReport(stage="parameters", ok=not errors, errors=errors, warnings=warnings, checks_run=checks)
