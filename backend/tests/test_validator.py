"""Parameter validation: pure Python, no CAD kernel required."""

import math

import pytest

from app.schemas.design import DEFAULT_PARAMS
from app.services.layout import hole_layout
from app.services.validator import validate_parameters


def params(**overrides):
    return {**DEFAULT_PARAMS.model_dump(), **overrides}


def codes(report):
    return {e.code for e in report.errors}


def test_default_parameters_pass_every_check():
    p, report = validate_parameters(DEFAULT_PARAMS)
    assert report.ok and p == DEFAULT_PARAMS
    assert report.warnings == []
    assert {"schema", "ranges", "edge_margin_across_width", "hole_centres_inside_faces"} <= set(report.checks_run)


def test_valid_dimension_change_passes():
    _, report = validate_parameters(params(base_length_mm=120.0, thickness_mm=6.0))
    assert report.ok


@pytest.mark.parametrize("field", ["base_length_mm", "base_width_mm", "vertical_height_mm", "thickness_mm", "hole_diameter_mm"])
@pytest.mark.parametrize("value", [0.0, -5.0])
def test_zero_or_negative_dimension_rejected(field, value):
    _, report = validate_parameters(params(**{field: value}))
    assert not report.ok
    assert "not_positive" in codes(report)
    assert "greater than 0 mm" in report.errors[0].message


def test_missing_dimension_rejected():
    data = params()
    del data["thickness_mm"]
    p, report = validate_parameters(data)
    assert p is None and not report.ok
    assert codes(report) == {"missing"}
    assert "thickness is required" in report.errors[0].message


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_non_finite_rejected(bad):
    _, report = validate_parameters(params(base_length_mm=bad))
    assert not report.ok and "not_finite" in codes(report)


@pytest.mark.parametrize("bad", ["100", True, None])
def test_non_numeric_rejected(bad):
    _, report = validate_parameters(params(base_length_mm=bad))
    assert not report.ok


def test_unknown_parameter_rejected():
    _, report = validate_parameters(params(rib_count=3))
    assert codes(report) == {"unsupported_parameter"}


def test_out_of_range_rejected():
    _, report = validate_parameters(params(base_length_mm=5000.0))
    assert codes(report) == {"out_of_range"}
    assert "between 10 mm and 500 mm" in report.errors[0].message


def test_thickness_consuming_whole_flange_rejected():
    _, report = validate_parameters(params(vertical_height_mm=20.0, thickness_mm=20.0, hole_diameter_mm=4.0))
    assert "flange_too_short" in codes(report)


def test_oversized_hole_rejected():
    # 25 mm hole on a 20 mm wide plate: bigger than the face itself.
    _, report = validate_parameters(params(base_width_mm=20.0, hole_diameter_mm=25.0))
    assert not report.ok
    assert "hole_too_large" in codes(report)
    assert "does not fit" in report.errors[0].message


def test_nearly_face_sized_hole_fails_on_margin():
    # 49 mm hole fits a 50 mm face but leaves no edge material.
    _, report = validate_parameters(params(hole_diameter_mm=49.0))
    assert codes(report) == {"edge_margin_violation"}


def test_hole_violating_edge_margin_across_width():
    # 2 x 16 mm holes + 3 x 8 mm margins = 56 mm > 50 mm wide.
    _, report = validate_parameters(params(hole_diameter_mm=16.0))
    assert not report.ok
    assert "edge_margin_violation" in codes(report)
    msg = report.errors[0].message
    assert "does not leave enough material at the plate edge" in msg
    assert "Reduce the hole diameter" in msg


def test_hole_violating_edge_margin_along_flange():
    # Free base flange = 25 - 5 = 20 mm; needs 8 + 2*8 = 24 mm.
    _, report = validate_parameters(params(base_length_mm=25.0))
    assert "edge_margin_violation" in codes(report)
    assert "base plate" in report.errors[0].message


def test_fillet_reduces_usable_flange():
    _, ok = validate_parameters(params(fillet_radius_mm=3.0))
    assert ok.ok and "fillet_feasibility" in ok.checks_run
    _, bad = validate_parameters(params(base_length_mm=30.0, fillet_radius_mm=6.0))
    assert "edge_margin_violation" in codes(bad)


def test_small_margin_is_valid_but_warns():
    _, report = validate_parameters(params(edge_margin_mm=3.0))
    assert report.ok
    assert [w.code for w in report.warnings] == ["edge_distance_below_recommended"]


def test_no_holes_is_valid():
    _, report = validate_parameters(params(holes_per_plate=0))
    assert report.ok
    assert "hole_vs_face" not in report.checks_run


def test_layout_places_holes_inside_faces():
    holes = hole_layout(DEFAULT_PARAMS)
    assert len(holes) == 4
    base = [h for h in holes if h.plate == "base"]
    # Default: base x centre midway along the 95 mm free flange; y at margin + r.
    assert base[0].center == pytest.approx((52.5, 12.0, 0.0))
    assert base[1].center == pytest.approx((52.5, 38.0, 0.0))
