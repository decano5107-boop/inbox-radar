"""Shared fixtures: the generated mailbox on a fixed date, and the example rules."""
import datetime as dt
import json
import os
import sys
import tempfile

from inbox_radar import rules as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "examples", "mailbox"))
import generate  # noqa: E402

TODAY = dt.date(2026, 5, 14)
RULES_PATH = os.path.join(ROOT, "examples", "rules.example.json")


def example_rules():
    rules, problems = R.load(RULES_PATH)
    assert problems == [], problems
    return rules


class GeneratedMailbox:
    """Generate once per test class; `expected` is what the generator says each message is."""

    def __init__(self, today=TODAY):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self._tmp.name, "mbx")
        generate.generate(self.folder, today)
        with open(os.path.join(self.folder, "expected.json"), encoding="utf-8") as f:
            self.expected = json.load(f)["messages"]

    def cleanup(self):
        self._tmp.cleanup()
