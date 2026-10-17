"""FlowCAD benchmark: natural language -> parameters -> CAD -> export.

    cd backend
    python ../benchmarks/run_benchmark.py --mode rules     # offline parser
    python ../benchmarks/run_benchmark.py --mode gemma     # needs DO_INFERENCE_API_KEY

Every prompt starts from the default bracket. For each case it records:
  * extraction  - status and every expected value match (values within 0.01)
  * extras      - parameters the interpreter set that the case did not expect
  * geometry    - CAD built and passed geometry validation (valid designs)
  * export      - STL (and STEP) written and verified
  * constraint  - invalid designs were rejected before CAD
  * latency     - interpretation time (Gemma mode)

Results print to the terminal and are written to benchmarks/results/.
Nothing is estimated: a case that did not run is reported as not run.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

from app.config import AI  # noqa: E402
from app.schemas.design import DEFAULT_PARAMS  # noqa: E402
from app.services.cad_engine import generate_bracket  # noqa: E402
from app.services.interpreter import interpret  # noqa: E402


def close(a, b) -> bool:
    if isinstance(b, (int, float)) and isinstance(a, (int, float)):
        return abs(a - b) <= 0.01
    return a == b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rules", "gemma"], default="rules")
    args = ap.parse_args()

    if args.mode == "rules":
        os.environ["DO_INFERENCE_API_KEY"] = ""
    elif not AI.configured:
        print("Gemma mode needs DO_INFERENCE_API_KEY (in .env or the environment).")
        return 1

    cases = json.loads((HERE / "prompts.json").read_text())
    base = DEFAULT_PARAMS.model_dump()
    rows = []
    out_dir = Path(tempfile.mkdtemp(prefix="flowcad-bench-"))

    for case in cases:
        exp = case["expect"]
        t0 = time.perf_counter()
        it = interpret(case["prompt"], base)
        wall_ms = int((time.perf_counter() - t0) * 1000)

        row = {"id": case["id"], "source": it.source, "status": it.status, "ai_error": it.ai_error,
               "latency_ms": it.latency_ms if args.mode == "gemma" else wall_ms}
        status_ok = it.status == exp["status"]
        values_ok = all(close(it.changes.get(k), v) for k, v in exp.get("changes", {}).items())
        row["extraction"] = status_ok and values_ok and (args.mode == "rules" or it.source == "gemma")
        row["extras"] = sorted(set(it.changes) - set(exp.get("changes", {})))
        row["got"] = it.changes
        row["geometry"] = row["export"] = row["constraint"] = None

        if it.status == "ok" and "design_valid" in exp:
            if exp["design_valid"]:
                if it.parameter_report and it.parameter_report.ok:
                    res = generate_bracket(it.proposed_params, out_dir=out_dir)
                    stages = {r.stage: r.ok for r in res.reports}
                    row["geometry"] = stages.get("geometry", False)
                    row["export"] = stages.get("export", False) and any(f.format == "stl" for f in res.files)
                else:
                    row["geometry"] = row["export"] = False
            else:
                row["constraint"] = bool(it.parameter_report) and not it.parameter_report.ok
        rows.append(row)

    def rate(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return (sum(vals), len(vals))

    lat = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# FlowCAD benchmark — {args.mode} — {when}",
        "",
        f"Interpreter: {'Gemma 4 (' + AI.model + ')' if args.mode == 'gemma' else 'rule-based parser (offline)'}  ",
        f"Cases: {len(rows)} (from benchmarks/prompts.json)",
        "",
        "| Metric | Result |",
        "| --- | --- |",
    ]
    for key, label in [("extraction", "Parameter extraction correct"), ("geometry", "Geometry generated & valid"),
                       ("export", "Export verified (STL)"), ("constraint", "Invalid designs rejected")]:
        ok, n = rate(key)
        lines.append(f"| {label} | {ok}/{n}" + (f" ({100 * ok / n:.0f}%)" if n else "") + " |")
    if lat:
        lines.append(f"| Interpretation latency (median / max) | {statistics.median(lat):.0f} ms / {max(lat)} ms |")
    fallbacks = [r["id"] for r in rows if r["ai_error"]]
    if args.mode == "gemma":
        lines.append(f"| Fell back to rule parser | {len(fallbacks)} |")
    lines += ["", "| Case | Status | Extraction | Geometry | Export | Constraint | Extra params |", "| --- | --- | --- | --- | --- | --- | --- |"]
    mark = {True: "pass", False: "FAIL", None: "–"}
    for r in rows:
        lines.append(f"| {r['id']} | {r['status']} | {mark[r['extraction']]} | {mark[r['geometry']]} | {mark[r['export']]} | {mark[r['constraint']]} | {', '.join(r['extras']) or '–'} |")
    failures = [r for r in rows if r["extraction"] is False]
    if failures:
        lines += ["", "## Extraction failures", ""]
        lines += [f"- **{r['id']}**: got `{json.dumps(r['got'])}` (status {r['status']}){' — ' + r['ai_error'] if r['ai_error'] else ''}" for r in failures]

    report = "\n".join(lines) + "\n"
    print(report)
    results = HERE / "results"
    results.mkdir(exist_ok=True)
    (results / f"{args.mode}.md").write_text(report, encoding="utf-8")
    print(f"Written to benchmarks/results/{args.mode}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
