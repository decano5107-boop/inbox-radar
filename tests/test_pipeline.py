"""Extraction and triage checked against the generated mailbox, a regression fixture.

The assertion is exact: every obligation, movement and bucket the generator says is in the
mailbox must come out, and nothing else. The fixture is invented, so this guards against regressions; it is not an accuracy figure.
"""
import shutil
import unittest
from decimal import Decimal

from inbox_radar import pipeline, sources, store
from tests.helpers import GeneratedMailbox, example_rules


@unittest.skipUnless(shutil.which("pdftotext"), "pdftotext not installed; PDF figures unreadable")
class AgainstTheFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mbx = GeneratedMailbox()
        cls.db = store.connect(":memory:")
        cls.scan = pipeline.scan(sources.FixtureSource(cls.mbx.folder), example_rules(), cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.mbx.cleanup()

    def test_every_bucket_matches(self):
        wrong = {mid: (got, self.mbx.expected[mid]["bucket"]) for mid, got in self.scan.buckets.items()
                 if got != self.mbx.expected[mid]["bucket"]}
        self.assertEqual(wrong, {})
        self.assertEqual(len(self.scan.buckets), len(self.mbx.expected))

    def test_every_obligation_matches(self):
        rows = {r["message_id"]: r for r in self.db.execute("SELECT * FROM obligations")}
        expected = {mid: e["obligation"] for mid, e in self.mbx.expected.items() if e["obligation"]}
        self.assertEqual(set(rows), set(expected))
        for mid, want in expected.items():
            got = rows[mid]
            self.assertEqual((got["issuer"], Decimal(got["amount"]), got["currency"], got["due"]),
                             (want["issuer"], Decimal(want["amount"]), want["currency"], want["due"]), mid)
            self.assertEqual(got["confidence"], "high", mid)

    def test_every_movement_matches(self):
        rows = {r["message_id"]: r for r in self.db.execute("SELECT * FROM movements")}
        expected = {mid: e["movement"] for mid, e in self.mbx.expected.items() if e["movement"]}
        self.assertEqual(set(rows), set(expected))
        for mid, want in expected.items():
            self.assertEqual((Decimal(rows[mid]["amount"]), rows[mid]["currency"]),
                             (Decimal(want["amount"]), want["currency"]), mid)

    def test_the_same_bill_from_two_emails_is_one_obligation(self):
        bill = [o for o in store.open_obligations(self.db) if o.issuer == "Skylark Mobile"]
        self.assertEqual(len(bill), 1)
        self.assertEqual(bill[0].amount, Decimal("54.32"))

    def test_no_text_is_stored(self):
        for table in ("obligations", "movements"):
            for row in self.db.execute(f"SELECT * FROM {table}"):
                for value in row:
                    self.assertLess(len(str(value)), 120, (table, value))


class Sources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mbx = GeneratedMailbox()

    @classmethod
    def tearDownClass(cls):
        cls.mbx.cleanup()

    def test_unruled_senders_arrive_without_body(self):
        rules = example_rules()
        for m in sources.FixtureSource(self.mbx.folder).messages(rules.all_senders()):
            if rules.for_sender(m.address) is None:
                self.assertEqual((m.body, m.attachment_text), ("", ""), m.id)

    def test_passcodes_never_enter_the_process(self):
        rules = example_rules()
        bodies = " ".join(m.body for m in sources.FixtureSource(self.mbx.folder).messages(rules.all_senders()))
        for secret in ("482913", "7741", "551203", "reset?token", "0000 1111 2222"):
            self.assertNotIn(secret, bodies)
