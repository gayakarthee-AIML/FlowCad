"""Check the DigitalOcean inference configuration before a demo.

    cd backend && python scripts/check_inference.py

1. Lists the models your key can use and shows the Gemma ones, so you can
   confirm DO_INFERENCE_MODEL is a real model ID.
2. Sends one sample request through FlowCAD's real Gemma path and prints
   the validated result and latency.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import AI  # noqa: E402
from app.schemas.design import DEFAULT_PARAMS  # noqa: E402
from app.services.gemma import AIError, interpret_with_gemma  # noqa: E402


def main() -> int:
    if not AI.configured:
        print("DO_INFERENCE_API_KEY is not set (in .env or the environment).")
        return 1
    print(f"Endpoint: {AI.base_url}\nModel:    {AI.model}\n")
    try:
        r = httpx.get(f"{AI.base_url}/v1/models", headers={"Authorization": f"Bearer {AI.api_key}"}, timeout=AI.timeout_s)
        r.raise_for_status()
        ids = sorted(m.get("id", "") for m in r.json().get("data", []))
        gemma = [i for i in ids if "gemma" in i.lower()]
        print(f"{len(ids)} models available. Gemma models: {gemma or 'none found'}")
        if AI.model not in ids:
            print(f"WARNING: '{AI.model}' is not in the list. Set DO_INFERENCE_MODEL to one of the IDs above.")
    except Exception as exc:  # report and continue to the request test
        print(f"Could not list models: {exc}")

    print("\nSample request: 'Make the bracket 120 mm long and increase its thickness to 6 mm.'")
    try:
        reply, ms = interpret_with_gemma("Make the bracket 120 mm long and increase its thickness to 6 mm.", DEFAULT_PARAMS.model_dump())
    except AIError as err:
        print(f"FAILED ({err.kind}): {err.detail}")
        return 1
    print(f"OK in {ms} ms -> status={reply.status} changes={reply.changes.as_dict()} message={reply.message!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
