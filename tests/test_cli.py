"""The command line as a user runs it, and the demo as the README promises it."""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import RULES_PATH, ROOT, TODAY, GeneratedMailbox


class Cli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mbx = GeneratedMailbox()

    @classmethod
    def tearDownClass(cls):
        cls.mbx.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "radar.db")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        cmd = [sys.executable, "-B", "-m", "inbox_radar", "--db", self.db, "--today", TODAY.isoformat(), *args]
        return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=60)

    def test_scan_remind_status_mark(self):
        scan = self.run_cli("scan", "--rules", RULES_PATH, "--mailbox", self.mbx.folder)
        self.assertEqual(scan.returncode, 0, scan.stderr)
        self.assertIn("187 messages", scan.stdout)
        remind = self.run_cli("remind", "--rules", RULES_PATH)
        self.assertEqual(remind.returncode, 0, remind.stderr)
        self.assertIn("Skylark Mobile — phone bill: $54.32, due tomorrow", remind.stdout)
        self.assertEqual(self.run_cli("remind", "--rules", RULES_PATH).stdout, "")     # said once
        status = self.run_cli("status")
        ident = next(line.split()[0] for line in status.stdout.splitlines() if "Skylark Mobile" in line)
        self.assertEqual(self.run_cli("mark", ident, "paid").returncode, 0)
        self.assertIn("Paid or dismissed (1)", self.run_cli("status").stdout)
        self.assertEqual(self.run_cli("mark", ident, "open").returncode, 0)
        self.assertNotIn("Paid or dismissed", self.run_cli("status").stdout)

    def test_global_flags_work_before_and_after_the_command(self):
        other = os.path.join(self.tmp.name, "other.db")
        for cmd in ([sys.executable, "-B", "-m", "inbox_radar", "--db", other, "status"],
                    [sys.executable, "-B", "-m", "inbox_radar", "status", "--db", other]):
            subprocess.run(cmd, capture_output=True, cwd=ROOT, check=True)
        self.assertTrue(os.path.exists(other))

    def test_plan_draft_from_a_mailbox(self):
        plan = os.path.join(self.tmp.name, "draft.json")
        out = self.run_cli("plan", "draft", plan, "--sender", "news@kettlebrook-weekly.example", "--mailbox", self.mbx.folder)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("archive:", self.run_cli("plan", "show", plan).stdout)

    def test_a_card_number_dressed_as_an_amount_never_reaches_the_notifier(self):
        box = os.path.join(self.tmp.name, "box")
        os.makedirs(box)
        with open(os.path.join(box, "messages.jsonl"), "w") as f:
            for i, figure in enumerate(("$4111111111111111.00", "$482913.00")):
                f.write(json.dumps({"id": f"x{i}", "from": "Skylark Mobile <billing@skylarkmobile.example>",
                                    "date": "2026-05-13T09:00:00", "subject": "bill", "headers": {},
                                    "body": f"This month\nAmount due {figure}\nDue date May 15, 2026"}) + "\n")
        self.run_cli("scan", "--rules", RULES_PATH, "--mailbox", box)
        out = self.run_cli("remind", "--rules", RULES_PATH)
        self.assertNotIn("4,111,111", out.stdout)
        self.assertIn("$482,913.00", out.stdout)           # a six-figure bill is a bill, written as money

    def test_plan_draft_is_private(self):
        import stat
        plan = os.path.join(self.tmp.name, "draft.json")
        self.run_cli("plan", "draft", plan, "--sender", "news@kettlebrook-weekly.example", "--mailbox", self.mbx.folder)
        self.assertEqual(stat.S_IMODE(os.stat(plan).st_mode), 0o600)

    def test_unsupported_currency_is_reported(self):
        from inbox_radar import rules as R
        _r, problems = R.parse({"issuers": [{"issuer": "A", "senders": ["a@a.example"], "concept": "c",
                                             "currency": "GBP", "amount_anchors": ["x"]}]})
        self.assertTrue(any("currency" in p for p in problems))

    def test_output_never_carries_message_text(self):
        out = self.run_cli("scan", "--rules", RULES_PATH, "--mailbox", self.mbx.folder)
        out2 = self.run_cli("remind", "--rules", RULES_PATH)
        out3 = self.run_cli("status")
        everything = out.stdout + out.stderr + out2.stdout + out2.stderr + out3.stdout + out3.stderr
        for secret in ("482913", "7741", "551203", "token=", "0000 1111 2222", "Test A", "mg/dL", "Dr. Example"):
            self.assertNotIn(secret, everything)

    def test_plan_show_propose_approve(self):
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w") as f:
            json.dump([{"verb": "archive", "sender": "news@kettlebrook-weekly.example", "message_ids": ["m0100"]}], f)
        state = os.path.join(self.tmp.name, "plans")
        self.assertIn("archive: 1 messages", self.run_cli("plan", "show", plan).stdout)
        self.assertEqual(self.run_cli("plan", "propose", plan, "--state", state).returncode, 1)  # no notifier
        proposed = self.run_cli("plan", "propose", plan, "--state", state, "--notifier", "stdout")
        code = proposed.stdout.split("reply with code ")[1][:4]
        self.assertEqual(self.run_cli("plan", "approve", plan, "--state", state, "--code", "2222").returncode, 2)
        ok = self.run_cli("plan", "approve", plan, "--state", state, "--code", code)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("archive: 1 messages", ok.stdout)
        self.assertEqual(self.run_cli("plan", "approve", plan, "--state", state, "--code", code).returncode, 2)

    def test_a_plan_with_a_system_label_is_refused(self):
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w") as f:
            json.dump([{"verb": "label", "label": "TRASH", "message_ids": ["m1"]}], f)
        result = self.run_cli("plan", "propose", plan, "--state", os.path.join(self.tmp.name, "p"), "--notifier", "stdout")
        self.assertEqual(result.returncode, 2)
        self.assertIn("system label", result.stderr)


class Demo(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext not installed")
    def test_the_demo_runs_and_shows_each_stage(self):
        spec = importlib.util.spec_from_file_location("demo", os.path.join(ROOT, "examples", "demo.py"))
        demo = importlib.util.module_from_spec(spec)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            spec.loader.exec_module(demo)
            demo.main(os.path.join(tmp, "demo"))
        text = out.getvalue()
        for marker in ("187 messages", "10 bills read", "due tomorrow", "blocked 1", "refused, and the code is spent",
                       "{'archive': 19, 'label': 28}"):
            self.assertIn(marker, text)
        self.assertNotIn("482913", text.split("── 3.")[0])
