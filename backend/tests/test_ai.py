"""Gemma client, defensive parsing, fallback and the rule parser.

The DigitalOcean endpoint is simulated with httpx.MockTransport; these tests
never call the real service.
"""

import json

import httpx
import pytest

from app.schemas.design import DEFAULT_PARAMS
from app.services import rules
from app.services.gemma import AIError, interpret_with_gemma, parse_reply
from app.services.interpreter import interpret

CURRENT = DEFAULT_PARAMS.model_dump()


@pytest.fixture
def ai_on(monkeypatch):
    monkeypatch.setenv("DO_INFERENCE_API_KEY", "test-key")
    monkeypatch.setenv("DO_INFERENCE_MODEL", "gemma-4-31B-it")


@pytest.fixture
def ai_off(monkeypatch):
    monkeypatch.setenv("DO_INFERENCE_API_KEY", "")


def client_returning(content=None, status=200, raw=None, exc=None, seen=None):
    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        if exc:
            raise exc
        if raw is not None:
            return httpx.Response(status, text=raw)
        return httpx.Response(status, json={"choices": [{"message": {"role": "assistant", "content": content}}]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def reply(**kw):
    return json.dumps({"status": "ok", "changes": {}, "message": "", **kw})


# --- Gemma path -------------------------------------------------------------

def test_gemma_request_shape_and_valid_reply(ai_on):
    seen = []
    c = client_returning(reply(changes={"base_length_mm": 120, "thickness_mm": 6}), seen=seen)
    r, ms = interpret_with_gemma("Make it 120 mm long, 6 mm thick", CURRENT, client=c)
    assert r.changes.as_dict() == {"base_length_mm": 120, "thickness_mm": 6}
    req = seen[0]
    assert req.url.path == "/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer test-key"
    body = json.loads(req.content)
    assert body["model"] == "gemma-4-31B-it" and body["temperature"] == 0
    assert "CURRENT PARAMETERS" in body["messages"][1]["content"]
    assert "response_format" not in body  # JSON mode is opt-in


def test_edit_preserves_unspecified_values(ai_on):
    c = client_returning(reply(changes={"thickness_mm": 6}))
    out = interpret("Change the thickness to 6 mm.", {**CURRENT, "base_length_mm": 140.0}, client=c)
    assert out.source == "gemma" and out.status == "ok"
    assert out.proposed_params["base_length_mm"] == 140.0  # kept, not reset to default
    assert out.proposed_params["thickness_mm"] == 6
    assert out.changed_fields == ["thickness_mm"]
    assert out.parameter_report.ok


def test_reply_in_code_fence_with_prose_is_parsed():
    r = parse_reply('Sure!\n```json\n{"status":"ok","changes":{"hole_diameter_mm":10},"message":""}\n```')
    assert r.changes.hole_diameter_mm == 10


@pytest.mark.parametrize("content,expect", [
    ("I think you want a longer bracket.", "no JSON"),
    ('{"status":"ok","changes":{"base_length_mm":120}', "unterminated"),
    ('{"status":"ok","changes":{"rib_count":3}}', "schema"),
    ('{"status":"ok","changes":{"base_length_mm":"long"}}', "schema"),
    ('{"status":"maybe"}', "schema"),
    ('{"status":"ok","changes":{},"message":"","run":"import os"}', "schema"),
])
def test_malformed_model_output_rejected(content, expect):
    with pytest.raises(AIError) as e:
        parse_reply(content)
    assert e.value.kind == "malformed" and expect in e.value.detail


def test_ok_with_no_changes_becomes_clarify():
    assert parse_reply('{"status":"ok","changes":{}}').status == "clarify"


@pytest.mark.parametrize("status,kind", [(429, "rate_limited"), (401, "auth"), (404, "http_error"), (503, "unavailable"), (400, "http_error")])
def test_http_errors_classified(ai_on, status, kind):
    with pytest.raises(AIError) as e:
        interpret_with_gemma("x", CURRENT, client=client_returning(raw="err", status=status))
    assert e.value.kind == kind


def test_timeout_classified(ai_on):
    c = client_returning(exc=httpx.ReadTimeout("slow"))
    with pytest.raises(AIError) as e:
        interpret_with_gemma("x", CURRENT, client=c)
    assert e.value.kind == "timeout"


def test_missing_message_in_response(ai_on):
    with pytest.raises(AIError) as e:
        interpret_with_gemma("x", CURRENT, client=client_returning(raw='{"choices":[]}'))
    assert e.value.kind == "malformed"


# --- Fallback ---------------------------------------------------------------

def test_timeout_falls_back_to_rules(ai_on):
    out = interpret("Change the thickness to 6 mm.", CURRENT, client=client_returning(exc=httpx.ConnectTimeout("down")))
    assert out.source == "rules" and out.status == "ok"
    assert out.changes == {"thickness_mm": 6.0}
    assert out.ai_error.startswith("timeout")


def test_invalid_ai_output_falls_back_to_rules(ai_on):
    out = interpret("Make the holes 10 mm wide.", CURRENT, client=client_returning("not json at all"))
    assert out.source == "rules" and out.changes == {"hole_diameter_mm": 10.0}
    assert out.ai_error.startswith("malformed")


def test_no_api_key_uses_rules_without_error(ai_off):
    out = interpret("Increase the base length to 120 mm.", CURRENT)
    assert out.source == "rules" and out.ai_error is None
    assert out.proposed_params["base_length_mm"] == 120.0


def test_interpretation_reports_invalid_proposal(ai_off):
    out = interpret("Make the holes 30 mm wide.", CURRENT)
    assert out.status == "ok"  # understood...
    assert not out.parameter_report.ok  # ...but the design is not valid


# --- Rule parser ------------------------------------------------------------

SPEC_PROMPT = ("Create an L-shaped aluminum mounting bracket. The base is 100 mm long and 50 mm wide. "
               "The vertical plate is 70 mm tall. Both plates are 5 mm thick. Add two mounting holes to each plate.")


@pytest.mark.parametrize("text,expected", [
    (SPEC_PROMPT, {"base_length_mm": 100, "base_width_mm": 50, "vertical_height_mm": 70, "thickness_mm": 5,
                   "holes_per_plate": 2, "material": "aluminum_6061"}),
    ("Make the bracket 120 mm long and increase its thickness to 6 mm.", {"base_length_mm": 120, "thickness_mm": 6}),
    ("Increase the base length to 120 mm.", {"base_length_mm": 120}),
    ("Change the thickness to 6 mm.", {"thickness_mm": 6}),
    ("Make the holes 10 mm wide.", {"hole_diameter_mm": 10}),
    ("Move the holes farther from the edge.", {"edge_margin_mm": 10}),
    ("Increase the height by 10 mm", {"vertical_height_mm": 80}),
    ("reduce the width by 5 mm", {"base_width_mm": 45}),
    ("make it 1 mm thicker and 20 mm longer", {"thickness_mm": 6, "base_length_mm": 120}),
    ("8 mm holes, 3 holes per plate, steel", {"hole_diameter_mm": 8, "holes_per_plate": 3, "material": "steel_mild"}),
    ("0.25 inch thick, 12 cm long", {"thickness_mm": 6.35, "base_length_mm": 120}),
    ("hole diameter 6.5 mm with a 4 mm fillet", {"hole_diameter_mm": 6.5, "fillet_radius_mm": 4}),
    ("Use M6 bolts", {"hole_diameter_mm": 6.6}),
    ("edge margin 10 mm, width 60mm", {"edge_margin_mm": 10, "base_width_mm": 60}),
    # "base 20 mm longer" is relative, never an absolute 20 mm base.
    ("make the base 20 mm longer, 80 mm wide, 3 holes per plate and a 4 mm bend fillet",
     {"base_length_mm": 120, "base_width_mm": 80, "holes_per_plate": 3, "fillet_radius_mm": 4}),
    ("thickness 2 mm thicker", {"thickness_mm": 7}),
    ("make the holes 2 mm bigger", {"hole_diameter_mm": 10}),
    ("base 4 inches longer", {"base_length_mm": 201.6}),
])
def test_rules_extract_parameters(text, expected):
    r = rules.parse(text, CURRENT)
    assert r.status == "ok"
    assert r.changes.keys() == expected.keys()
    for k, v in expected.items():
        assert r.changes[k] == (pytest.approx(v) if isinstance(v, (int, float)) else v), k


@pytest.mark.parametrize("text,status", [
    ("Make it bigger", "clarify"),
    ("make it 120", "clarify"),
    ("hello", "clarify"),
    ("design a spur gear with 20 teeth", "unsupported"),
])
def test_rules_ask_instead_of_guessing(text, status):
    r = rules.parse(text, CURRENT)
    assert r.status == status and r.changes == {} and r.message


def test_rules_report_unlinked_numbers():
    r = rules.parse("Bracket 150 x 40, 90 mm tall", CURRENT)
    assert r.changes == {"vertical_height_mm": 90.0}
    assert "150" in r.message and "40" in r.message
