"""Deterministic natural-language parser: the offline fallback for Gemma 4.

It understands the common, explicit phrasings of bracket requests:

    "The base is 100 mm long and 50 mm wide"
    "Make the bracket 120 mm long and increase its thickness to 6 mm"
    "Make the holes 10 mm wide", "8 mm holes", "add two mounting holes to each plate"
    "Increase the height by 10 mm", "make it 1 mm thicker"
    "Move the holes farther from the edge"     (edge margin +2 mm)
    "0.25 in thick", "12 cm long", "steel", "4 mm bend fillet"

Anything it cannot map to a parameter is reported back, and a request with
no recognisable parameter becomes a clarification question. It never
guesses a field for a bare number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

UNIT_TO_MM = {
    "mm": 1.0, "millimeter": 1.0, "millimeters": 1.0, "millimetre": 1.0, "millimetres": 1.0,
    "cm": 10.0, "centimeter": 10.0, "centimeters": 10.0, "centimetre": 10.0, "centimetres": 10.0,
    "m": 1000.0, "meter": 1000.0, "meters": 1000.0, "metre": 1000.0, "metres": 1000.0,
    "in": 25.4, "inch": 25.4, "inches": 25.4, '"': 25.4,
}
WORD_NUMBERS = {"no": 0, "zero": 0, "one": 1, "a": 1, "single": 1, "two": 2, "three": 3, "four": 4}

_UNIT = r'(mm|millimet(?:er|re)s?|cm|centimet(?:er|re)s?|met(?:er|re)s?|m(?![a-z])|inch(?:es)?|in(?![a-z])|")'
NUM = r"(\d+(?:\.\d+)?)\s*" + _UNIT + "?"
# A number followed by a comparative ("20 mm longer") is a relative change,
# handled by COMPARATIVE, never an absolute value for the preceding keyword.
_NOT_COMPARATIVE = (r"(?!\d+(?:\.\d+)?\s*(?:" + _UNIT[1:] + r"?\s*"
                    r"(?:longer|shorter|wider|narrower|taller|higher|lower|thicker|thinner|bigger|smaller|larger)\b)")
# Filler words allowed between a keyword and its number ("thickness to 6 mm").
_FILL = r"(?:\s+(?:to|of|is|are|be|at|about|around|approximately|=|:|should|must|set|now|exactly|the|its|it|plate|plates|diameter|dia\.?|size|by|equal|roughly|=)\b|\s*[:=])*\s+"

INCREASE = re.compile(r"\b(increase|raise|extend|lengthen|widen|thicken|enlarge|add|grow|bigger|larger)\b")
DECREASE = re.compile(r"\b(decrease|reduce|lower|shorten|narrow|thin|shrink|cut|smaller|less)\b")

# keyword-first and number-first patterns per field (holes handled first).
FIELD_PATTERNS: list[tuple[str, list[str]]] = [
    ("edge_margin_mm", [
        r"(?:edge\s+margin|margin|edge\s+distance)" + _FILL + _NOT_COMPARATIVE + NUM,
        NUM + r"\s*(?:edge\s+)?margin",
        NUM + r"\s*(?:from|away\s+from)\s+(?:the\s+)?(?:plate\s+)?edges?",
    ]),
    ("fillet_radius_mm", [
        r"(?:fillet|bend\s+radius|inside\s+radius|radius)" + _FILL + _NOT_COMPARATIVE + NUM,
        NUM + r"\s*(?:radius\s+)?(?:bend\s+)?(?:fillet|radius)",
    ]),
    ("thickness_mm", [
        r"thickness" + _FILL + _NOT_COMPARATIVE + NUM,
        NUM + r"\s*(?:thick|in\s+thickness)\b",
    ]),
    ("base_width_mm", [
        r"width" + _FILL + _NOT_COMPARATIVE + NUM,
        NUM + r"\s*(?:wide|in\s+width)\b",
    ]),
    ("vertical_height_mm", [
        r"(?:height|vertical\s+plate)" + _FILL + _NOT_COMPARATIVE + NUM,
        NUM + r"\s*(?:tall|high|in\s+height)\b",
    ]),
    ("base_length_mm", [
        r"(?:length|base)" + _FILL + _NOT_COMPARATIVE + NUM,
        NUM + r"\s*(?:long|in\s+length)\b",
    ]),
]
COMPARATIVE = [
    ("base_length_mm", r"longer", 1), ("base_length_mm", r"shorter", -1),
    ("base_width_mm", r"wider", 1), ("base_width_mm", r"narrower", -1),
    ("vertical_height_mm", r"taller|higher", 1), ("vertical_height_mm", r"shorter\s+vertical|lower", -1),
    ("thickness_mm", r"thicker", 1), ("thickness_mm", r"thinner", -1),
]

HOLE_DIAMETER = [
    r"(?:hole\s+diameter|hole\s+size|holes?|bore)" + _FILL + _NOT_COMPARATIVE + NUM + r"(?!\s*(?:long|tall|high|thick)\b)",
    NUM + r"\s*(?:diameter\s+|dia\.?\s+|wide\s+)?(?:mounting\s+)?holes?\b",
    r"[øØ⌀]\s*(\d+(?:\.\d+)?)()",
    r"\bm(\d+)()\s+(?:bolts?|screws?|fasteners?|holes?)\b",  # metric fastener size
]
# ISO 273 medium-series clearance holes, mm.
ISO_273_MEDIUM_CLEARANCE = {3: 3.4, 4: 4.5, 5: 5.5, 6: 6.6, 8: 9.0, 10: 11.0, 12: 13.5, 16: 17.5}
HOLE_COUNT = r"\b(\d+|no|zero|one|a|single|two|three|four)\s+(?:mounting\s+|bolt\s+|through\s+)?holes?\b"

MATERIALS = [
    (r"\bstainless\b", "stainless_304"),
    (r"\balumin(?:i)?um\b|\b6061\b", "aluminum_6061"),
    (r"\bsteel\b", "steel_mild"),
    (r"\bpla\b|\b3d[- ]?print", "pla"),
]


@dataclass
class RuleResult:
    status: str  # ok | clarify | unsupported
    changes: dict = field(default_factory=dict)
    message: str = ""
    notes: list[str] = field(default_factory=list)


def _to_mm(value: str, unit: str | None) -> float:
    return round(float(value) * UNIT_TO_MM.get((unit or "mm").lower(), 1.0), 4)


def _direction(text: str, start: int, gap: str) -> int:
    """+1/-1 for a relative change ('increase X by 10 mm'), 0 for absolute."""
    if not re.search(r"\bby\b", gap):
        return 0
    before = text[max(0, start - 50):start + len(gap)]
    if DECREASE.search(before):
        return -1
    if INCREASE.search(before):
        return 1
    return 0


UNSUPPORTED = re.compile(r"\b(gear|shaft|enclosure|box|cylinder|flange\s+coupling|gusset|rib|slot|countersink|chamfer|thread)\w*\b")


def parse(text: str, current: dict) -> RuleResult:
    t = " " + text.lower().replace("×", "x") + " "
    changes: dict = {}
    notes: list[str] = []
    consumed: list[tuple[int, int]] = []

    def free(span: tuple[int, int]) -> bool:
        return all(span[1] <= a or span[0] >= b for a, b in consumed)

    def apply(fieldname: str, value: float, rel: int) -> None:
        if rel:
            value = round(float(current.get(fieldname, 0)) + rel * value, 4)
        changes[fieldname] = value

    # Hole count first ("two mounting holes", "4 holes": a bare number, no unit).
    for m in re.finditer(HOLE_COUNT, t):
        if not free(m.span()):
            continue
        word = m.group(1)
        # "8 mm holes" has a unit and is a diameter, handled below.
        if re.match(r"\s*" + _UNIT, t[m.end(1):m.end(1) + 6]):
            continue
        count = WORD_NUMBERS.get(word, None) if not word.isdigit() else int(word)
        if count is not None:
            changes["holes_per_plate"] = count
            consumed.append(m.span())

    for i, pat in enumerate(HOLE_DIAMETER):
        for m in re.finditer(pat, t):
            if not free(m.span()):
                continue
            value, unit = m.group(1), (m.group(2) or None)
            if i == 3:  # metric fastener size -> ISO 273 medium clearance hole
                size = int(value)
                if size not in ISO_273_MEDIUM_CLEARANCE:
                    notes.append(f"M{size} is not in FlowCAD's clearance table; hole size not changed.")
                    consumed.append(m.span())
                    continue
                hole = ISO_273_MEDIUM_CLEARANCE[size]
                notes.append(f"M{size} fasteners: using the ISO 273 medium clearance hole, {hole:g} mm.")
                changes["hole_diameter_mm"] = hole
            else:
                gap = t[m.start():m.start(1)]
                apply("hole_diameter_mm", _to_mm(value, unit), _direction(t, m.start(), gap))
            consumed.append(m.span())

    for fieldname, patterns in FIELD_PATTERNS:
        for pat in patterns:
            for m in re.finditer(pat, t):
                if not free(m.span()):
                    continue
                gap = t[m.start():m.start(1)]
                apply(fieldname, _to_mm(m.group(1), m.group(2)), _direction(t, m.start(), gap))
                consumed.append(m.span())

    for m in re.finditer(r"holes?\b[^.,;\d]{0,15}?" + NUM + r"\s*(bigger|larger|wider|smaller|narrower)\b", t):
        if free(m.span()):
            sign = 1 if m.group(3) in ("bigger", "larger", "wider") else -1
            apply("hole_diameter_mm", _to_mm(m.group(1), m.group(2)), sign)
            consumed.append(m.span())

    for fieldname, word, sign in COMPARATIVE:
        for m in re.finditer(NUM + r"\s*(?:" + word + r")\b", t):
            if free(m.span()):
                apply(fieldname, _to_mm(m.group(1), m.group(2)), sign)
                consumed.append(m.span())

    if re.search(r"\b(farther|further|away)\s+from\s+(?:the\s+)?edges?\b|\bmore\s+(?:edge\s+)?margin\b", t) and "edge_margin_mm" not in changes:
        changes["edge_margin_mm"] = round(float(current.get("edge_margin_mm", 8)) + 2, 4)
        notes.append("'Farther from the edge' was applied as +2 mm edge margin.")
    elif re.search(r"\bcloser\s+to\s+(?:the\s+)?edges?\b|\bless\s+(?:edge\s+)?margin\b", t) and "edge_margin_mm" not in changes:
        changes["edge_margin_mm"] = round(float(current.get("edge_margin_mm", 8)) - 2, 4)
        notes.append("'Closer to the edge' was applied as -2 mm edge margin.")

    if re.search(r"\b(no|without(?:\s+an?)?|remove(?:\s+the)?)\s+(?:bend\s+)?fillet", t):
        changes["fillet_radius_mm"] = 0.0
    elif re.search(r"\bsharp\s+(?:inside\s+)?corner", t):
        changes["fillet_radius_mm"] = 0.0

    for pat, material in MATERIALS:
        if re.search(pat, t):
            changes["material"] = material
            break

    # Numbers that no rule consumed are reported, never silently assigned.
    leftovers = [m.group(0).strip() for m in re.finditer(NUM, t)
                 if free(m.span()) and not re.match(r"6061|304", m.group(0))
                 and not re.search(r"[a-z]$", t[max(0, m.start() - 1):m.start()])]

    if UNSUPPORTED.search(t) and not changes:
        return RuleResult("unsupported", message="FlowCAD currently supports one part, an L-shaped mounting bracket. Describe the bracket's dimensions, holes, thickness or material.")

    if not changes:
        if re.search(r"\b(bigger|larger|smaller|stronger|longer|shorter|wider|narrower|taller|thicker|thinner)\b", t):
            return RuleResult("clarify", message="Which dimension should change, and by how much? For example: 'make the base 20 mm longer' or 'thickness to 6 mm'.")
        if leftovers:
            return RuleResult("clarify", message=f"I found {', '.join(leftovers)} but couldn't tell which dimension it sets. Name it, for example 'base length 120 mm' or '6 mm thick'.")
        return RuleResult("clarify", message="I couldn't find a bracket dimension in that request. Try something like: 'base 100 mm long, 50 mm wide, 70 mm tall, 5 mm thick, two 8 mm holes per plate'.")

    if leftovers:
        notes.append(f"Ignored {', '.join(repr(x) for x in leftovers)}: not linked to a named dimension.")

    return RuleResult("ok", changes=changes, message=" ".join(notes))
