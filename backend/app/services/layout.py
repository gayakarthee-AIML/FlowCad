"""Bracket coordinate system and mounting-hole layout.

Coordinate system (millimetres, right-handed):

* Origin at the outside corner of the bend, on one side face.
* +X runs along the base plate (length), +Y across both plates (width),
  +Z up the vertical plate (height).
* Base plate occupies x in [0, L], y in [0, W], z in [0, t].
* Vertical plate occupies x in [0, t], y in [0, W], z in [0, H].

Hole layout (documented, deterministic):

* Each plate gets ``holes_per_plate`` through-holes in a single row across
  the width.
* The row sits midway along the plate's *free flange*: the flat region that
  starts after the inside of the bend (thickness plus any bend fillet).
* Across the width, one hole is centred; two or more are spread so the outer
  holes keep ``edge_margin_mm`` of material to the side edges.

The generator and the validator both call this module, so the holes that are
checked are exactly the holes that are cut.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.schemas.design import BracketParams


@dataclass(frozen=True)
class Hole:
    plate: Literal["base", "vertical"]
    center: tuple[float, float, float]  # point on the plate's outer face
    axis: tuple[float, float, float]  # drilling direction, through the plate
    diameter: float


def free_flange_mm(p: BracketParams, plate: Literal["base", "vertical"]) -> float:
    """Length of flat plate beyond the inside of the bend."""
    outer = p.base_length_mm if plate == "base" else p.vertical_height_mm
    return outer - p.thickness_mm - p.fillet_radius_mm


def hole_y_positions(p: BracketParams) -> list[float]:
    n = p.holes_per_plate
    w, d, m = p.base_width_mm, p.hole_diameter_mm, p.edge_margin_mm
    if n <= 0:
        return []
    if n == 1:
        return [w / 2]
    first = m + d / 2
    last = w - m - d / 2
    step = (last - first) / (n - 1)
    return [first + i * step for i in range(n)]


def hole_layout(p: BracketParams) -> list[Hole]:
    if p.holes_per_plate <= 0:
        return []
    t, r, d = p.thickness_mm, p.fillet_radius_mm, p.hole_diameter_mm
    base_x = t + r + free_flange_mm(p, "base") / 2
    vert_z = t + r + free_flange_mm(p, "vertical") / 2
    holes: list[Hole] = []
    for y in hole_y_positions(p):
        holes.append(Hole("base", (base_x, y, 0.0), (0.0, 0.0, 1.0), d))
    for y in hole_y_positions(p):
        holes.append(Hole("vertical", (0.0, y, vert_z), (1.0, 0.0, 0.0), d))
    return holes
