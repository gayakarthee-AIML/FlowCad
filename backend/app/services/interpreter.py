"""Natural language -> proposed parameters.

Uses Gemma 4 when an API key is configured and falls back to the
deterministic rule parser when inference is unavailable, times out,
rate-limits, or returns unusable output. Either way the merged parameters
are run through the deterministic validator before anything is built.
"""

from __future__ import annotations

import httpx

from app.config import AI
from app.schemas.ai import Interpretation
from app.services import rules
from app.services.gemma import AIError, interpret_with_gemma
from app.services.validator import validate_parameters


def merge(current: dict, changes: dict) -> dict:
    """Apply changes on top of the current design; unspecified values keep
    their current value."""
    return {**current, **changes}


def interpret(text: str, current: dict, client: httpx.Client | None = None) -> Interpretation:
    ai_error: str | None = None
    latency: int | None = None
    source = "rules"

    if AI.configured:
        try:
            reply, latency = interpret_with_gemma(text, current, client=client)
            status, changes, message, source = reply.status, reply.changes.as_dict(), reply.message, "gemma"
        except AIError as err:
            ai_error = f"{err.kind}: {err.detail}"

    if source == "rules":
        r = rules.parse(text, current)
        status, changes, message = r.status, r.changes, r.message

    if status != "ok":
        return Interpretation(status=status, source=source, message=message, ai_error=ai_error, latency_ms=latency)

    proposed = merge(current, changes)
    changed = [k for k, v in changes.items() if current.get(k) != v]
    _, report = validate_parameters(proposed)
    return Interpretation(
        status="ok", source=source, message=message, changes=changes, changed_fields=changed,
        proposed_params=proposed, parameter_report=report, ai_error=ai_error, latency_ms=latency,
    )
