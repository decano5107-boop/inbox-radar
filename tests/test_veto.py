"""The veto must block every sensitive case in the fixture mailbox and pass every real notice."""
import datetime as dt
import json
import os
import shutil
import unittest

from inbox_radar import notify, pipeline, radar, sources, store, veto
from inbox_radar.radar import Notice
from tests.helpers import TODAY, GeneratedMailbox, example_rules

BLOCK = {
    "Your verification code is 482913.": "passcode",
    "Tu código de verificación es 7741.": "passcode",
    "Use this PIN: 0042 now": "passcode",
    "Login at 551203": "passcode",
    "https://accounts.nubelia.example/reset?token=abc": "link",
    "reset at nubelia.example/reset": "link",
    "Account XX00 9999 0000 1111 2222 3333": "account_number",
    "card 1234 1234 5678 9012": "account_number",
    "SSN 123-45-6789": "government_id",
    "DNI 12345678Z": "government_id",
    "NI number QQ 12 34 56 C": "government_id",
    "CURP XEXX010101HNEXXXA4": "government_id",
    "Passport X1234567": "government_id",
    "Test A 1.0 ng/mL (low)": "medical",
    "Glucose high, see doctor": "medical",
}
PASS = [
    "Copperline Bank — card statement: $1,234.56, due in 5 days (Tue 19 May), card ending 1234.",
    "Fernway Lending — loan installment: $250.00, due in 12 days (Tue 26 May), loan account ending 0000.",
    "Example Bank — card statement: 1.234,56 €, due in 12 days (Tue 26 May), card ending 0000.",
    "Copperline Bank: a charge of $500.00, card ending 1234.",
    "City Tax Office — estimated tax payment: $1,000.00, due in 40 days (Tue 23 Jun).",
    "Lakeside Clinic sent you something; open your mail to read it.",
    "Brightwater Toll — unpaid toll, past due: $10.00, overdue since Wed 13 May.",
]


class Audit(unittest.TestCase):
    def test_every_sensitive_pattern_is_found(self):
        for text, category in BLOCK.items():
            self.assertIn(category, veto.audit(text), text)

    def test_ordinary_notices_pass(self):
        for text in PASS:
            self.assertEqual(veto.audit(text), [], text)

    def test_fields_are_audited_too(self):
        n = Notice("due", "k", "7", "Acme — bill: $1.00, due today.", {"issuer": "Acme", "last_four": "482913"})
        self.assertEqual(veto.audit_notice(n), ["passcode"])

    def test_issuer_and_concept_from_the_owners_rules_are_trusted(self):
        for issuer in ("Passport Services", "Prescription Plan Co", "Acme.example", "Security Bank"):
            n = Notice("due", "k", "7", f"{issuer} — refill: $12.00, due tomorrow (Fri 15 May), card ending 1234.",
                       {"issuer": issuer, "concept": "refill", "amount": "12.00", "last_four": "1234"})
            self.assertEqual(veto.audit_notice(n), [], issuer)

    def test_spacing_and_encoding_tricks(self):
        cases = {
            "4821 is your verification code": "passcode",
            "Your 4821 code": "passcode",
            "code: 4 8 2 1": "passcode",
            "1 2 3 4 5 6": "passcode",
            "1.2.3.4.5.6": "passcode",
            "co\u200bde 4821": "passcode",
            "123\u200b456": "passcode",
            "ｃｏｄｅ ４８２１": "passcode",
            "code ٤٨٢١": "passcode",
            "evil.xyz/reset?t=1": "link",
            "bit.ly/abc": "link",
            "go to login.bank.example now": "link",
            "mailto:x@y.example": "link",
            "ht\u200btps://x.y": "link",
            "ｈｔｔｐｓ：／／evil.example": "link",
            "1234.1234.5678.9012": "account_number",
            "1234\u00a01234\u00a05678\u00a09012": "account_number",
            "xx00.9999.0000.1111.2222.3333": "account_number",   # an IBAN shape with a fictional country and bank code
            "123.45.6789": "government_id",
            "12.345.678-Z": "government_id",
            "qq123456c": "government_id",
            "xexx010101hnexxxa4": "government_id",
            "HIV test": "medical",
            "take 500 mg daily": "medical",
            "blood pressure 140/90": "medical",
        }
        for text, category in cases.items():
            self.assertIn(category, veto.audit(text), text)

    def test_money_as_the_template_writes_it_and_dates_pass(self):
        for text in ("$1,234,567.00", "1.234.567,00 €", "$1,200.00", "123.456,00 €", "$10.00",
                     "due 2026-05-26", "05/19/2026", "card ending 1234", "tarjeta terminada en 0000",
                     "in 40 days (Tue 23 Jun)", "e.g. soon"):
            self.assertEqual(veto.audit(text), [], text)

    def test_money_shapes_do_not_hide_codes_or_cards(self):
        cases = {"Your code is $482913": "passcode", "code is EUR 482913": "passcode",
                 "code 482913 EUR": "passcode", "code 4821.55": "passcode",
                 "code 48.29.13": "passcode", "code 12-34-56": "passcode",
                 "card $4111111111111111": "account_number", "USD 4111 1111 1111 1111": "account_number",
                 "card 4111 1111 1111 1111.00": "account_number", "4111  1111  1111  1111": "account_number",
                 "4111/1111/1111/1111": "account_number", "4111_1111_1111_1111": "account_number",
                 "evil[.]com/reset": "link", "evil。com/x": "link", "localhost:8080/x": "link",
                 "192.168.0.1/reset": "link", "passport is X1234567": "government_id",
                 "passport no. X1234567": "government_id", "tested positive": "medical",
                 "insulin dose": "medical"}
        for text, category in cases.items():
            self.assertIn(category, veto.audit(text), text)

    def test_typed_fields_with_the_right_shape_are_not_mistaken_for_codes(self):
        n = Notice("due", "k", "7", "Acme — bill: $482,913.00, due today.",
                   {"issuer": "Acme", "amount": "482913.00", "currency": "USD", "due": "2026-05-14"})
        self.assertEqual(veto.audit_notice(n), [])
        n.fields["amount"] = "code 482913"
        self.assertEqual(veto.audit_notice(n), ["passcode"])


@unittest.skipUnless(shutil.which("pdftotext"), "pdftotext not installed")
class OnTheFixtureMailbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mbx = GeneratedMailbox()

    @classmethod
    def tearDownClass(cls):
        cls.mbx.cleanup()

    def test_every_sensitive_body_would_be_blocked(self):
        with open(os.path.join(self.mbx.folder, "messages.jsonl"), encoding="utf-8") as f:
            raw = [json.loads(line) for line in f]
        for m in raw:
            if self.mbx.expected[m["id"]]["sensitive"]:
                self.assertTrue(veto.audit(m["body"]), m["id"])

    def test_every_notice_the_radar_produces_passes(self):
        db = store.connect(":memory:")
        pipeline.scan(sources.FixtureSource(self.mbx.folder), example_rules(), db)
        texts = []
        for day in range(0, 80):
            notices = radar.run(db, TODAY + dt.timedelta(days=day), {"USD": 100, "EUR": 100})
            for n in notices:
                self.assertEqual(veto.audit_notice(n), [], n.text)
                texts.append(n.text)
                radar.confirm_sent(db, n)
        self.assertGreater(len(texts), 10)


class Delivery(unittest.TestCase):
    def setUp(self):
        self.db = store.connect(":memory:")
        self.log = []

    def notice(self, text, key="k1"):
        store.claim(self.db, key, "7", dt.datetime(2026, 5, 14, 8))
        return Notice("due", key, "7", text, {})

    def test_a_blocked_notice_is_not_sent_not_retried_and_not_logged_as_text(self):
        sent = []

        class Rec:
            def send(self, text):
                sent.append(text)
                return True
        counts = notify.deliver(self.db, [self.notice("code 482913")], Rec(), self.log.append)
        self.assertEqual(counts, (0, 1, 0))
        self.assertEqual(sent, [])
        self.assertNotIn("482913", json.dumps(self.log))
        self.assertFalse(store.claim(self.db, "k1", "7", dt.datetime(2026, 5, 15, 8)))

    def test_a_failing_notifier_releases_the_reminder(self):
        class Broken:
            def send(self, text):
                raise ConnectionError("down")
        counts = notify.deliver(self.db, [self.notice("Acme — bill: $5.00, due today.")], Broken(), self.log.append)
        self.assertEqual(counts, (0, 0, 1))
        self.assertTrue(store.claim(self.db, "k1", "7", dt.datetime(2026, 5, 14, 8, 5)))

    def test_jsonl_notifier_writes_a_private_file(self):
        import stat
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out", "notices.jsonl")
            notify.JsonlNotifier(path).send("hello")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
