"""plan_trace_report — render an execution trace as a self-contained HTML report.

plan-trace proves a run on paper (a Markdown timeline). This is the
half a reviewer actually opens: one HTML file that shows, at a glance,
whether the run is trustworthy — a verdict banner, the step timeline
with durations, and the multimodal evidence (text receipts inline,
image thumbnails with captions) behind each step.

It reuses the same entry shape and hashing as plan-audit-log,
plan-sandbox-run and plan-trace, so it reads their logs directly.
Nothing is rendered as clean until the checks pass:

* the run log's hash chain is intact — a tampered entry is shown in
  the report, not silently rendered;
* the log's plan version matches this exact plan (content hash);
* every plan step is accounted for: executed, blocked, or never run;
* timestamps run forward, so a duration is never negative;
* every executed step has at least one evidence item;
* image evidence has a caption, and — with --check-files — the file
  actually exists next to the report.

Usage:

    python3 plan_trace_report.py examples/plan.json \\
        --run examples/run.jsonl --evidence examples/evidence.json \\
        --check-files --out examples/report.html

Open report.html in a browser. The verdict banner is green when the
chain verifies and every step has evidence; red when anything is
wrong, with each problem listed. Image evidence is referenced by
relative path, so keep the report in the same folder as the evidence
manifest (or copy the evidence folder next to it).

Exit codes: 0 report clean, 1 problems found, 2 input error (a file
that cannot be read, or a malformed log line).
"""

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

GENESIS = "GENESIS"
STEP_RE = re.compile(r"^([^:]+):\s")


def plan_hash(plan):
    """Content hash of a plan — the version pin, same as plan-approve."""
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def entry_hash(entry):
    """sha256 of a log entry's canonical JSON, excluding its own hash."""
    payload = {k: v for k, v in entry.items() if k != "hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_run_log(path):
    """Parse a run-log JSONL file; a malformed line is an input error."""
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for n, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"run log {path} line {n}: invalid JSON: {exc}")
            if not isinstance(entry, dict):
                raise ValueError(
                    f"run log {path} line {n}: not a JSON object")
            entries.append(entry)
    return entries


def _parse_ts(value):
    """Parse an ISO timestamp, or None when it is missing/unparseable."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def verify_chain(entries):
    """Check the log's hash chain; returns a list of problem strings."""
    problems = []
    if not entries:
        return ["run log is empty"]
    prev_stored = GENESIS
    for i, entry in enumerate(entries):
        seq = entry.get("seq")
        if seq != i + 1:
            problems.append(
                f"entry {i + 1}: seq is {seq!r}, expected {i + 1} "
                "(gap, replay, or out-of-order entry)")
        stored = entry.get("hash")
        if stored is None:
            problems.append(f"entry {i + 1}: no hash recorded")
        elif entry_hash(entry) != stored:
            problems.append(
                f"entry {seq}: stored hash does not match its content "
                "(entry was tampered with after it was written)")
        if entry.get("prev_hash") != prev_stored:
            problems.append(
                f"entry {seq}: prev_hash does not link to the previous "
                "entry (chain is broken here)")
        if isinstance(stored, str):
            prev_stored = stored
    return problems


def _step_id_from_detail(detail):
    """The step id a log detail starts with, e.g. 'step-2' in
    'step-2: Summarise ...' — None when the detail names no step."""
    if not isinstance(detail, str):
        return None
    match = STEP_RE.match(detail)
    return match.group(1) if match else None


def build_report(plan, entries, evidence=None, check_files=False,
                 evidence_dir=None):
    """Check a plan run and return the report dict for rendering.

    evidence maps step ids to lists of {"kind": "text"|"image",
    "ref": ..., "caption": ...}. With check_files, an image ref is
    resolved against evidence_dir and must exist on disk.
    """
    evidence = evidence or {}
    version = plan_hash(plan)
    plan_id = plan.get("id")
    steps = {s["id"]: s for s in plan.get("steps", [])}
    problems = []

    report = {"plan": plan_id, "plan_version": version,
              "verdict": None, "entries_checked": len(entries),
              "steps": [], "problems": problems}

    problems.extend(verify_chain(entries))

    logged_versions = {e.get("version") for e in entries}
    if logged_versions and logged_versions != {version}:
        problems.append(
            "run log is for a different plan version than this plan "
            f"(log: {sorted(logged_versions)}, plan: {version[:12]}...)")
    logged_plans = {e.get("plan") for e in entries}
    if logged_plans and logged_plans != {plan_id}:
        problems.append(
            f"run log is for plan(s) {sorted(logged_plans)}, "
            f"not {plan_id!r}")

    stamps = []
    for entry in entries:
        ts = _parse_ts(entry.get("ts"))
        if ts is None:
            problems.append(
                f"entry {entry.get('seq')}: timestamp "
                f"{entry.get('ts')!r} is missing or unparseable")
        stamps.append(ts)
    for a, b in zip(stamps, stamps[1:]):
        if a is not None and b is not None and b < a:
            problems.append(
                "timestamps run backwards in the log "
                f"({a.isoformat()} after {b.isoformat()})")

    step_events = {}
    outcome = None
    for entry in entries:
        action = entry.get("action")
        if action == "run_completed":
            outcome = "completed"
            continue
        if action == "run_blocked":
            outcome = "blocked"
            continue
        if action not in ("step_executed", "step_blocked"):
            problems.append(
                f"entry {entry.get('seq')}: unknown log action "
                f"{action!r}")
            continue
        step_id = _step_id_from_detail(entry.get("detail"))
        if step_id not in steps:
            problems.append(
                f"entry {entry.get('seq')}: log names step "
                f"{step_id!r}, which this plan does not have")
            continue
        step_events.setdefault(step_id, []).append(entry)
    if outcome is None:
        problems.append("run log has no outcome entry "
                        "(neither run_completed nor run_blocked)")

    for step_id, step in steps.items():
        events = step_events.get(step_id, [])
        executed = [e for e in events if e.get("action") == "step_executed"]
        blocked = [e for e in events if e.get("action") == "step_blocked"]
        if executed:
            status, result = "executed", executed[-1].get("detail", "")
        elif blocked:
            status, result = "blocked", blocked[-1].get("detail", "")
        else:
            status, result = "not run", ""
        duration = None
        if executed:
            idx = entries.index(executed[-1])
            if idx + 1 < len(entries):
                start, end = stamps[idx], stamps[idx + 1]
                if start is not None and end is not None:
                    duration = round((end - start).total_seconds(), 3)
        items = evidence.get(step_id, [])
        step_problems = []
        if status == "executed" and not items:
            step_problems.append(
                f"step {step_id} executed but has no evidence attached")
        for item in items:
            kind, ref = item.get("kind"), item.get("ref")
            if kind not in ("text", "image"):
                step_problems.append(
                    f"step {step_id}: evidence kind {kind!r} is neither "
                    "'text' nor 'image'")
            elif not ref:
                step_problems.append(
                    f"step {step_id}: evidence item has no ref")
            elif kind == "image":
                if not item.get("caption"):
                    step_problems.append(
                        f"step {step_id}: image evidence {ref!r} has no "
                        "caption saying what it shows")
                if check_files and evidence_dir is not None:
                    if not (Path(evidence_dir) / ref).is_file():
                        step_problems.append(
                            f"step {step_id}: image evidence file "
                            f"{ref!r} does not exist")
        problems.extend(step_problems)
        report["steps"].append({
            "id": step_id, "action": step.get("action", ""),
            "status": status, "result": result,
            "duration_s": duration, "evidence": items,
            "problems": step_problems,
        })

    report["outcome"] = outcome
    report["verdict"] = "clean" if not problems else "problems"
    return report


def _status_class(status):
    return {"executed": "ok", "blocked": "warn",
            "not run": "idle"}.get(status, "idle")


def render_html(report):
    """Render the report as one self-contained HTML page.

    Everything user-supplied is escaped; image evidence is linked by
    relative path so the file stays viewable offline next to the
    evidence folder.
    """
    esc = html.escape
    verdict = report["verdict"]
    banner_class = "clean" if verdict == "clean" else "bad"
    banner_text = ("CLEAN — chain verifies, every step accounted for"
                   if verdict == "clean" else
                   f"PROBLEMS FOUND — {len(report['problems'])} "
                   "issue(s), do not trust this run yet")

    rows = []
    for step in report["steps"]:
        ev_parts = []
        for item in step["evidence"]:
            if item.get("kind") == "image":
                ev_parts.append(
                    f"<figure><img src=\"{esc(item.get('ref', ''))}\" "
                    f"alt=\"{esc(item.get('caption') or item.get('ref', ''))}\">"
                    f"<figcaption>{esc(item.get('caption') or '(no caption)')}"
                    f"</figcaption></figure>")
            else:
                ev_parts.append(
                    f"<div class=\"text-ev\">{esc(str(item.get('ref', '')))}"
                    f"</div>")
        ev = "\n".join(ev_parts) or "<span class=\"none\">—</span>"
        took = (f"{step['duration_s']}s"
                if step["duration_s"] is not None else "—")
        rows.append(
            f"<tr><td>{esc(step['id'])}</td>"
            f"<td>{esc(step['action'])}</td>"
            f"<td><span class=\"pill {_status_class(step['status'])}\">"
            f"{esc(step['status'])}</span></td>"
            f"<td>{took}</td><td>{ev}</td></tr>")

    if report["problems"]:
        problems_html = "<ul>\n" + "\n".join(
            f"<li>{esc(p)}</li>" for p in report["problems"]) + "\n</ul>"
    else:
        problems_html = ("<p>None — the chain verifies, every step is "
                         "accounted for, and every executed step has "
                         "evidence.</p>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Trace report — {esc(str(report['plan']))}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2em auto;
       max-width: 860px; padding: 0 1em; color: #1a1a1a; }}
.banner {{ padding: 1em; border-radius: 8px; font-weight: 700; }}
.banner.clean {{ background: #dcfce7; color: #166534; }}
.banner.bad {{ background: #fee2e2; color: #991b1b; }}
.meta {{ color: #555; margin: 0.8em 0; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 1em; }}
th, td {{ border: 1px solid #ccc; padding: 0.5em; text-align: left;
          vertical-align: top; }}
th {{ background: #f3f4f6; }}
.pill {{ padding: 0.15em 0.6em; border-radius: 999px; font-size: 0.85em;
        font-weight: 600; }}
.pill.ok {{ background: #dcfce7; color: #166534; }}
.pill.warn {{ background: #fef3c7; color: #92400e; }}
.pill.idle {{ background: #e5e7eb; color: #374151; }}
figure {{ margin: 0.4em 0; }}
figure img {{ max-width: 320px; border: 1px solid #ccc; }}
figcaption {{ font-size: 0.85em; color: #555; }}
.text-ev {{ font-family: monospace; font-size: 0.9em; }}
.none {{ color: #999; }}
</style>
</head>
<body>
<h1>Trace report — {esc(str(report['plan']))}</h1>
<div class="banner {banner_class}">{esc(banner_text)}</div>
<p class="meta">Outcome: {esc(report['outcome'] or 'unknown')} &middot;
{report['entries_checked']} log entries checked &middot;
plan version <code>{esc(report['plan_version'][:12])}</code></p>
<table>
<tr><th>Step</th><th>Action</th><th>Status</th><th>Took</th>
<th>Evidence</th></tr>
{''.join(rows)}
</table>
<h2>Problems</h2>
{problems_html}
</body>
</html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render an execution trace as an HTML report.")
    parser.add_argument("plan", help="InspectablePlan JSON file")
    parser.add_argument("--run", required=True,
                        help="run log JSONL file (from plan-sandbox-run)")
    parser.add_argument("--evidence",
                        help="evidence manifest JSON file (step id -> "
                        "list of {kind, ref, caption})")
    parser.add_argument("--check-files", action="store_true",
                        help="check that image evidence files exist")
    parser.add_argument("--out", help="where to write the HTML report")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON")
    args = parser.parse_args(argv)

    try:
        plan = read_json(args.plan)
        entries = read_run_log(args.run)
        evidence = read_json(args.evidence) if args.evidence else {}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    if not isinstance(evidence, dict):
        print("input error: evidence file must be a JSON object keyed "
              "by step id", file=sys.stderr)
        return 2

    evidence_dir = (str(Path(args.evidence).parent)
                    if args.evidence else None)
    report = build_report(plan, entries, evidence,
                          check_files=args.check_files,
                          evidence_dir=evidence_dir)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(render_html(report))
        print(f"report written to {args.out}")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"{report['plan']}: verdict {report['verdict']} "
              f"({report['outcome'] or 'no outcome'}, "
              f"{len(report['steps'])} steps, "
              f"{len(report['problems'])} problems)")
        for problem in report["problems"]:
            print(f"  ! {problem}")
    return 0 if report["verdict"] == "clean" else 1


if __name__ == "__main__":
    sys.exit(main())
