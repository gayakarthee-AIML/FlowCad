"""Gemma 4 via DigitalOcean Serverless Inference (OpenAI-compatible API).

The model proposes parameter values only. Its reply is parsed defensively,
validated against ``ModelReply`` (unknown keys rejected), and never executed.
"""

from __future__ import annotations

import json
import re
import time

import httpx
from pydantic import ValidationError

from app.config import AI, LIMITS
from app.schemas.ai import ModelReply


class AIError(Exception):
    """Inference failed; ``kind`` is one of: not_configured, timeout,
    rate_limited, auth, unavailable, http_error, malformed."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


SYSTEM_PROMPT = f"""You convert requests for ONE part, an L-shaped mounting bracket, into parameter changes.

Bracket geometry (millimetres):
- base_length_mm: length of the horizontal base plate, measured from the back face of the vertical plate. Limits {LIMITS['base_length_mm']}.
- base_width_mm: width of both plates. Limits {LIMITS['base_width_mm']}.
- vertical_height_mm: overall height of the vertical plate, from the underside of the base. Limits {LIMITS['vertical_height_mm']}.
- thickness_mm: material thickness of both plates. Limits {LIMITS['thickness_mm']}.
- hole_diameter_mm: diameter of every mounting hole. Limits {LIMITS['hole_diameter_mm']}.
- holes_per_plate: integer count of holes on EACH plate, in one row across the width. Limits {LIMITS['holes_per_plate']}.
- edge_margin_mm: minimum material between a hole edge and any plate edge, the bend, or another hole. Limits {LIMITS['edge_margin_mm']}.
- fillet_radius_mm: inside bend fillet radius, 0 = sharp. Limits {LIMITS['fillet_radius_mm']}.
- material: one of "aluminum_6061", "steel_mild", "stainless_304", "pla".

Rules:
1. Output ONLY one JSON object, no prose, no code fences:
   {{"status": "ok" | "clarify" | "unsupported", "changes": {{...}}, "message": "..."}}
2. "changes" contains ONLY parameters the user explicitly set or changed. Never repeat unchanged values and never fill in defaults.
3. Convert every length to millimetres (1 in = 25.4 mm, 1 cm = 10 mm). Numbers must be JSON numbers.
4. Relative edits ("10 mm longer", "increase thickness by 1 mm", "double the width") are computed from CURRENT PARAMETERS.
5. "Move the holes farther from the edge" without a number: increase edge_margin_mm by 2 and say so in "message".
6. Metric fasteners (M6 bolts): use the ISO 273 medium clearance hole (M3 3.4, M4 4.5, M5 5.5, M6 6.6, M8 9, M10 11, M12 13.5, M16 17.5).
7. If a value cannot be tied to a specific parameter (e.g. "make it 120", "make it bigger"), use status "clarify", empty changes, and ask one short question in "message".
8. If the request is for anything other than this bracket (gears, enclosures, slots, ribs, threads...), use status "unsupported" and explain briefly.
9. Do not judge whether the design is physically valid; FlowCAD validates geometry separately.
10. Never output code, file paths or instructions."""


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles code fences
    and leading prose)."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    if start < 0:
        raise AIError("malformed", "The model reply contained no JSON object.")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(cleaned[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise AIError("malformed", f"The model reply was not valid JSON ({exc.msg}).") from exc
                if not isinstance(obj, dict):
                    raise AIError("malformed", "The model reply was not a JSON object.")
                return obj
    raise AIError("malformed", "The model reply contained an unterminated JSON object.")


def parse_reply(content: str) -> ModelReply:
    obj = _extract_json(content)
    try:
        reply = ModelReply.model_validate(obj)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise AIError("malformed", f"The model reply did not match the parameter schema ({loc}: {first['msg']}).") from exc
    if reply.status == "ok" and not reply.changes.as_dict():
        # An "ok" with nothing to change is really a request for clarification.
        return ModelReply(status="clarify", message=reply.message or "Which dimension should change?")
    return reply


def interpret_with_gemma(text: str, current: dict, client: httpx.Client | None = None) -> tuple[ModelReply, int]:
    """Call the model. Returns (validated reply, latency in ms) or raises AIError."""
    if not AI.configured:
        raise AIError("not_configured", "DO_INFERENCE_API_KEY is not set.")

    body: dict = {
        "model": AI.model,
        "temperature": 0,
        "max_completion_tokens": 400,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"CURRENT PARAMETERS: {json.dumps(current)}\n\nREQUEST: {text}"},
        ],
    }
    if AI.json_mode:
        body["response_format"] = {"type": "json_object"}

    own_client = client is None
    client = client or httpx.Client(timeout=AI.timeout_s)
    started = time.perf_counter()
    try:
        resp = client.post(
            f"{AI.base_url}/v1/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {AI.api_key}", "Content-Type": "application/json"},
            timeout=AI.timeout_s,
        )
    except httpx.TimeoutException as exc:
        raise AIError("timeout", f"Gemma did not answer within {AI.timeout_s:g} s.") from exc
    except httpx.HTTPError as exc:
        raise AIError("unavailable", f"Could not reach the inference endpoint ({type(exc).__name__}).") from exc
    finally:
        if own_client:
            client.close()
    latency = int((time.perf_counter() - started) * 1000)

    if resp.status_code == 429:
        raise AIError("rate_limited", "The inference endpoint is rate limiting requests (HTTP 429).")
    if resp.status_code in (401, 403):
        raise AIError("auth", f"The inference endpoint rejected the API key (HTTP {resp.status_code}).")
    if resp.status_code == 404:
        raise AIError("http_error", f"Model or endpoint not found (HTTP 404). Check DO_INFERENCE_MODEL='{AI.model}' with scripts/check_inference.py.")
    if resp.status_code >= 500:
        raise AIError("unavailable", f"The inference service is unavailable (HTTP {resp.status_code}).")
    if resp.status_code >= 400:
        raise AIError("http_error", f"The inference request failed (HTTP {resp.status_code}).")

    try:
        content = resp.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIError("malformed", "The inference response did not contain a chat message.") from exc

    return parse_reply(content), latency
