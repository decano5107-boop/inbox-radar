"""The fictional mailbox is the regression fixture for every other test, so it must be reproducible,
self-consistent and unmistakably fictional."""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "examples", "mailbox"))
import generate  # noqa: E402

TODAY = dt.date(2026, 5, 14)


class Mailbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = os.path.join(cls.tmp.name, "mbx")
        generate.generate(cls.out, TODAY)
        with open(os.path.join(cls.out, "messages.jsonl"), encoding="utf-8") as f:
            cls.messages = [json.loads(line) for line in f]
        with open(os.path.join(cls.out, "expected.json"), encoding="utf-8") as f:
            cls.expected = json.load(f)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_same_seed_same_mailbox(self):
        other = os.path.join(self.tmp.name, "again")
        generate.generate(other, TODAY)
        for name in ("messages.jsonl", "expected.json"):
            with open(os.path.join(self.out, name), "rb") as a, open(os.path.join(other, name), "rb") as b:
                self.assertEqual(a.read(), b.read(), name)

    def test_every_address_is_reserved_for_examples(self):
        for m in self.messages:
            for field in (m["from"], m["to"], m["body"]):
                for domain in re.findall(r"[\w.-]+@([\w.-]+)", field):
                    self.assertTrue(domain.endswith(".example"), (m["id"], domain))
            for url in re.findall(r"https?://([^/\s]+)", m["body"]):
                self.assertTrue(url.endswith(".example"), (m["id"], url))

    def test_every_message_has_an_expectation(self):
        self.assertEqual({m["id"] for m in self.messages}, set(self.expected["messages"]))
        self.assertEqual(self.expected["today"], TODAY.isoformat())

    def test_it_covers_the_cases_the_design_lists(self):
        e = self.expected["messages"].values()
        self.assertGreaterEqual(sum(1 for v in e if v["obligation"]), 8)
        self.assertGreaterEqual(sum(1 for v in e if v["sensitive"]), 4)
        self.assertGreaterEqual(sum(1 for v in e if v["movement"]), 30)
        self.assertEqual({v["obligation"]["currency"] for v in e if v["obligation"]}, {"USD"})
        self.assertTrue(any(v["bucket"] == "urgent" for v in e))
        subjects = " ".join(m["subject"] + m["body"] for m in self.messages)
        for marker in ("Previous bill amount due", "past due", "verification code", "login code", "reset", "lab results",
                       "ignore your previous instructions"):
            self.assertIn(marker, subjects)

    def test_attachments_exist_and_are_pdfs(self):
        paths = [a["path"] for m in self.messages for a in m["attachments"]]
        self.assertEqual(len(paths), 3)
        for rel in paths:
            with open(os.path.join(self.out, rel), "rb") as f:
                data = f.read()
            self.assertTrue(data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF"))

    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext not installed")
    def test_attachment_text_is_extractable(self):
        rel = next(a["path"] for m in self.messages for a in m["attachments"] if "loan-statement" in a["path"])
        text = subprocess.run(["pdftotext", os.path.join(self.out, rel), "-"],
                              capture_output=True, text=True, check=True).stdout
        self.assertIn("Installment due $250.00", text)


if __name__ == "__main__":
    unittest.main()
