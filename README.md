# plan-trace-report

Render an execution trace as a self-contained HTML report — the half of
the trustworthy-agentic-ai pipeline (BRJP26100123) a reviewer actually
opens. Week 4: multimodal tracing.

`plan-trace` proves a run on paper (a Markdown timeline). This turns
the same plan + hash-chained run log into one HTML file: a verdict
banner, the step timeline with durations, and the multimodal evidence
behind each step — text receipts inline, image thumbnails with captions.

Nothing is rendered as clean until the checks pass: the log's hash
chain is intact, the log's plan version matches this exact plan
(content hash), every plan step is accounted for (executed, blocked,
or never run), timestamps run forward, every executed step has at
least one evidence item, image evidence has a caption, and — with
`--check-files` — the image file actually exists. A tampered entry is
shown in the report, not silently rendered.

## Usage

```bash
python3 plan_trace_report.py examples/plan.json \
    --run examples/run.jsonl --evidence examples/evidence.json \
    --check-files --out examples/report.html
```

Open `report.html` in a browser. Green banner: the chain verifies and
every step has evidence. Red banner: the problems are listed — do not
trust the run yet. Image evidence is linked by relative path, so keep
the report in the same folder as the evidence manifest (or copy the
evidence folder next to it).

Exit codes: 0 report clean, 1 problems found, 2 input error.

## Examples

- `examples/plan.json` — a 4-step demo plan.
- `examples/run.jsonl` — a clean run of it (hash chain intact).
- `examples/broken-run.jsonl` — the same log with entry 2's detail
  edited after it was hashed; the report catches it.
- `examples/evidence.json` — text receipts plus one captioned chart.
- `examples/evidence/summary-chart.svg` — the chart itself.
- `examples/report.html` — the clean report.
- `examples/broken-report.html` — the tampered report (red banner).

Try the tampered one:

```bash
python3 plan_trace_report.py examples/plan.json \
    --run examples/broken-run.jsonl --evidence examples/evidence.json \
    --check-files --out examples/broken-report.html; echo "exit: $?"
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

9 stdlib tests, no dependencies. Run from the repo root.
