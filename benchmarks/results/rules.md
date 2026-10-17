# FlowCAD benchmark — rules — 2026-10-16 07:17

Interpreter: rule-based parser (offline)  
Cases: 18 (from benchmarks/prompts.json)

| Metric | Result |
| --- | --- |
| Parameter extraction correct | 18/18 (100%) |
| Geometry generated & valid | 12/12 (100%) |
| Export verified (STL) | 12/12 (100%) |
| Invalid designs rejected | 3/3 (100%) |
| Interpretation latency (median / max) | 0 ms / 19 ms |

| Case | Status | Extraction | Geometry | Export | Constraint | Extra params |
| --- | --- | --- | --- | --- | --- | --- |
| spec-example | ok | pass | pass | pass | – | – |
| length-and-thickness | ok | pass | pass | pass | – | – |
| base-length-to | ok | pass | pass | pass | – | – |
| thickness-to | ok | pass | pass | pass | – | – |
| hole-size | ok | pass | pass | pass | – | – |
| holes-farther | ok | pass | pass | pass | – | – |
| relative-height | ok | pass | pass | pass | – | – |
| comparatives | ok | pass | pass | pass | – | – |
| imperial | ok | pass | pass | pass | – | – |
| metric-bolts | ok | pass | pass | pass | – | – |
| fillet | ok | pass | pass | pass | – | – |
| multi-edit | ok | pass | pass | pass | – | – |
| oversized-holes | ok | pass | – | – | pass | – |
| zero-thickness | ok | pass | – | – | pass | – |
| short-base | ok | pass | – | – | pass | – |
| ambiguous-bigger | clarify | pass | – | – | – | – |
| ambiguous-number | clarify | pass | – | – | – | – |
| unsupported-part | unsupported | pass | – | – | – | – |
