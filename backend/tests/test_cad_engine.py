"""Real CAD geometry tests. These build solids with CadQuery/OpenCASCADE.

If CadQuery is not installed the whole module is reported as SKIPPED with
the reason, never silently passed.
"""

import pytest

cq = pytest.importorskip("cadquery", reason="CadQuery (CAD kernel) not installed; geometry tests need it")

from app.schemas.design import DEFAULT_PARAMS  # noqa: E402
from app.services.cad_engine import (  # noqa: E402
    build_bracket,
    expected_volume_mm3,
    generate_bracket,
    validate_geometry,
)


def params(**overrides):
    return DEFAULT_PARAMS.model_copy(update=overrides)


def stage(result, name):
    return next(r for r in result.reports if r.stage == name)


def test_default_bracket_generates_valid_solid_and_files(tmp_path):
    result = generate_bracket(DEFAULT_PARAMS, out_dir=tmp_path)
    assert result.ok, result.model_dump_json(indent=2)
    assert [r.stage for r in result.reports] == ["parameters", "geometry", "export"]
    assert all(r.ok for r in result.reports)
    assert {f.format for f in result.files} == {"stl", "step"}
    for f in result.files:
        path = tmp_path / f.filename
        assert path.exists() and path.stat().st_size == f.size_bytes > 0
    assert result.metadata.bounding_box_mm == pytest.approx((100, 50, 70), abs=0.01)
    assert result.metadata.hole_count == 4
    assert result.metadata.volume_mm3 == pytest.approx(expected_volume_mm3(DEFAULT_PARAMS), rel=1e-3)
    # No temporary files left behind.
    assert not list(tmp_path.glob(".*.tmp"))


def test_holes_are_real_through_cuts():
    solid = build_bracket(DEFAULT_PARAMS)
    no_holes = build_bracket(params(holes_per_plate=0))
    assert solid.Volume() < no_holes.Volume()
    # A point at the base hole centre, mid-thickness, is empty space.
    assert not solid.isInside(cq.Vector(52.5, 12.0, 2.5))
    # A point in solid material next to it is inside the part.
    assert solid.isInside(cq.Vector(52.5, 25.0, 2.5))
    # Vertical-plate hole at mid-height of the free flange.
    assert not solid.isInside(cq.Vector(2.5, 12.0, 37.5))


def test_valid_dimension_change_regenerates(tmp_path):
    result = generate_bracket(params(base_length_mm=120.0, thickness_mm=6.0), out_dir=tmp_path)
    assert result.ok
    assert result.metadata.bounding_box_mm == pytest.approx((120, 50, 70), abs=0.01)


@pytest.mark.parametrize("n", [0, 1, 3, 4])
def test_hole_counts(n, tmp_path):
    p = params(holes_per_plate=n, base_width_mm=80.0)
    result = generate_bracket(p, out_dir=tmp_path)
    assert result.ok, result.model_dump_json(indent=2)
    assert result.metadata.hole_count == 2 * n


def test_bend_fillet(tmp_path):
    result = generate_bracket(params(fillet_radius_mm=4.0), out_dir=tmp_path)
    assert result.ok, result.model_dump_json(indent=2)
    assert result.metadata.volume_mm3 > generate_bracket(DEFAULT_PARAMS, out_dir=tmp_path).metadata.volume_mm3


def test_invalid_parameters_stop_before_cad(tmp_path):
    result = generate_bracket({**DEFAULT_PARAMS.model_dump(), "hole_diameter_mm": 49.0}, out_dir=tmp_path)
    assert not result.ok
    assert [r.stage for r in result.reports] == ["parameters"]
    assert result.files == [] and list(tmp_path.iterdir()) == []


def test_geometry_check_rejects_null_and_wrong_size():
    assert validate_geometry(None, DEFAULT_PARAMS).errors[0].code == "null_shape"
    wrong = build_bracket(params(base_length_mm=90.0))
    report = validate_geometry(wrong, DEFAULT_PARAMS)
    assert not report.ok and report.errors[0].code == "bounding_box"


def test_stl_export_failure_is_reported(tmp_path):
    def broken(shape, fmt, path):
        raise OSError("disk full")

    result = generate_bracket(DEFAULT_PARAMS, out_dir=tmp_path, exporter=broken)
    assert not result.ok
    export = stage(result, "export")
    assert not export.ok
    assert export.errors[0].code == "stl_export_failed"
    assert "disk full" in export.errors[0].message
    assert result.files == []


def test_empty_stl_is_rejected(tmp_path):
    def empty(shape, fmt, path):
        path.write_bytes(b"")

    result = generate_bracket(DEFAULT_PARAMS, out_dir=tmp_path, exporter=empty)
    assert not result.ok
    assert "empty" in stage(result, "export").errors[0].message


def test_step_failure_only_warns_and_stl_still_offered(tmp_path):
    from app.services.cad_engine import _export_one

    def no_step(shape, fmt, path):
        if fmt == "step":
            raise RuntimeError("STEP writer unavailable")
        _export_one(shape, fmt, path)

    result = generate_bracket(DEFAULT_PARAMS, out_dir=tmp_path, exporter=no_step)
    assert result.ok
    assert [f.format for f in result.files] == ["stl"]
    assert stage(result, "export").warnings[0].code == "step_export_failed"
