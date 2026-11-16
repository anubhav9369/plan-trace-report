"""Tests for plan_trace_report — HTML trace reports for plan runs."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import plan_trace_report as ptr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples")


def load_plan():
    with open(os.path.join(EX, "plan.json")) as f:
        return json.load(f)


def load_run(name="run.jsonl"):
    return ptr.read_run_log(os.path.join(EX, name))


def load_evidence():
    with open(os.path.join(EX, "evidence.json")) as f:
        return json.load(f)


class TestReport(unittest.TestCase):
    def test_clean_run_is_clean(self):
        report = ptr.build_report(load_plan(), load_run(), load_evidence(),
                                  check_files=True, evidence_dir=EX)
        self.assertEqual(report["verdict"], "clean")
        self.assertEqual(report["outcome"], "completed")
        self.assertEqual(report["problems"], [])

    def test_tampered_run_is_problems(self):
        report = ptr.build_report(load_plan(), load_run("broken-run.jsonl"),
                                  load_evidence())
        self.assertEqual(report["verdict"], "problems")
        self.assertTrue(any("tampered" in p for p in report["problems"]))

    def test_html_escapes_user_content(self):
        plan = load_plan()
        plan["steps"][0]["action"] = "<script>alert(1)</script>"
        report = ptr.build_report(plan, load_run(), load_evidence())
        html = ptr.render_html(report)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_missing_image_file_flagged(self):
        evidence = load_evidence()
        evidence["s2"] = [{"kind": "image", "ref": "evidence/nope.svg",
                           "caption": "missing chart"}]
        report = ptr.build_report(load_plan(), load_run(), evidence,
                                  check_files=True, evidence_dir=EX)
        self.assertEqual(report["verdict"], "problems")
        self.assertTrue(any("does not exist" in p
                            for p in report["problems"]))

    def test_uncaptioned_image_flagged(self):
        evidence = load_evidence()
        evidence["s2"] = [{"kind": "image",
                           "ref": "evidence/summary-chart.svg"}]
        report = ptr.build_report(load_plan(), load_run(), evidence)
        self.assertTrue(any("no caption" in p for p in report["problems"]))

    def test_version_mismatch_flagged(self):
        plan = load_plan()
        plan["version"] = 99
        report = ptr.build_report(plan, load_run(), load_evidence())
        self.assertTrue(any("different plan version" in p
                            for p in report["problems"]))

    def test_step_never_run_shown(self):
        plan = load_plan()
        plan["steps"].append({"id": "s5", "action": "Archive the run"})
        report = ptr.build_report(plan, load_run(), load_evidence())
        statuses = {s["id"]: s["status"] for s in report["steps"]}
        self.assertEqual(statuses["s5"], "not run")
        html = ptr.render_html(report)
        self.assertIn("not run", html)

    def test_blocked_step_shown(self):
        entries = load_run()
        entries[3] = dict(entries[3], action="step_blocked",
                          detail="s4: Send the summary to the reviewer")
        entries[4] = dict(entries[4], action="run_blocked")
        # Re-hash the two changed entries so only the block is new.
        entries[3] = dict(entries[3])
        entries[3]["hash"] = ptr.entry_hash(entries[3])
        entries[4] = dict(entries[4])
        entries[4]["prev_hash"] = entries[3]["hash"]
        entries[4]["hash"] = ptr.entry_hash(entries[4])
        report = ptr.build_report(load_plan(), entries, load_evidence())
        statuses = {s["id"]: s["status"] for s in report["steps"]}
        self.assertEqual(statuses["s4"], "blocked")
        self.assertEqual(report["outcome"], "blocked")

    def test_duration_measured_between_entries(self):
        report = ptr.build_report(load_plan(), load_run(), load_evidence())
        durations = {s["id"]: s["duration_s"] for s in report["steps"]}
        self.assertEqual(durations["s1"], 12.0)
        self.assertEqual(durations["s2"], 19.0)
        self.assertEqual(durations["s3"], 34.0)


class TestCli(unittest.TestCase):
    def test_clean_exit_zero(self):
        rc = ptr.main(["examples/plan.json", "--run", "examples/run.jsonl",
                       "--evidence", "examples/evidence.json",
                       "--check-files", "--out", "/tmp/ptr-clean.html"])
        self.assertEqual(rc, 0)

    def test_broken_exit_one(self):
        rc = ptr.main(["examples/plan.json", "--run",
                       "examples/broken-run.jsonl",
                       "--evidence", "examples/evidence.json",
                       "--out", "/tmp/ptr-broken.html"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
