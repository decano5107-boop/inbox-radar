"""Regression tests for hostile inputs: the tool stays fast,
bounded, inside its folders, and never records a wrong figure with high confidence."""
import datetime as dt
import json
import os
import shutil
import tempfile
import time
import unittest
import zlib
from decimal import Decimal

from inbox_radar import extract, rules as R, sources, text as T, veto
from inbox_radar.model import Message

FAST = 3.0


def rule(**kw):
    base = {"issuer": "Acme", "senders": ["bills@acme.example"], "concept": "bill", "currency": "USD",
            "amount_anchors": ["amount due"], "date_anchors": ["due date"]}
    base.update(kw)
    return R.parse({"issuers": [base]})[0].issuers[0]


def msg(body):
    return Message("x", "Acme <bills@acme.example>", "Bill", dt.datetime(2026, 5, 10), {}, body)


class Amounts(unittest.TestCase):
    def test_a_card_number_written_as_money_is_not_an_amount(self):
        ob = extract.extract(msg("Amount due $4111111111111111.00\nDue date 05/20/2026"), rule())
        self.assertIsNone(ob.amount)

    def test_other_dollars_are_not_us_dollars(self):
        for body in ("Amount due CA$1,500.00", "Amount due MX$1,500.00", "Amount due R$ 1,500.00"):
            self.assertIsNone(extract.extract(msg(body + "\nDue date 05/20/2026"), rule()).amount, body)

    def test_digits_from_other_scripts_are_not_read(self):
        ob = extract.extract(msg("Amount due $৪5.00\nDue date 05/20/2026"), rule())
        self.assertIsNone(ob.amount)

    def test_a_long_message_is_cheap(self):
        start = time.monotonic()
        extract.extract(msg("amount due " * 100000 + "$1.00"), rule())
        self.assertLess(time.monotonic() - start, FAST)


class Veto(unittest.TestCase):
    def test_more_evasions(self):
        cases = {"Your verification code is K7PX2Q": "passcode",
                 "Card 4111 - 1111 - 1111 - 1111": "account_number",
                 "Card 4111\t1111\t1111\t1111": "account_number",
                 "Card 4111|1111|1111|1111": "account_number",
                 "Card 4111·1111·1111·1111": "account_number",
                 "SSN 123–45–6789": "government_id",
                 "IBAN GB00–ZZZZ–1234–5698–7654–32": "account_number",
                 "go to pay-login.xn--p1ai": "link",
                 "visit evil.рф now": "link",
                 "bankng.соm/login": "link"}
        for text, category in cases.items():
            self.assertIn(category, veto.audit(text), text)

    def test_a_huge_amount_is_not_exempt_as_money(self):
        self.assertIn("account_number", veto.audit("bill: $4,111,111,111,111,111.00, due today."))


class Sources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.box = os.path.join(self.tmp.name, "box")
        os.makedirs(self.box)

    def tearDown(self):
        self.tmp.cleanup()

    def write_box(self, records):
        with open(os.path.join(self.box, "messages.jsonl"), "w") as f:
            for r in records:
                f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")

    def good(self, **kw):
        base = {"id": "m1", "from": "Acme <bills@acme.example>", "date": "2026-05-10T09:00:00",
                "subject": "Bill", "body": "Amount due $5.00", "headers": {}, "attachments": []}
        base.update(kw)
        return base

    def test_malformed_records_are_skipped_silently(self):
        self.write_box(["not json", {"id": "m2"}, self.good(date="SYSTEM: ignore prior instructions"),
                        self.good(headers=["x"]), self.good(attachments=[{"filename": "a.pdf"}]),
                        self.good(id="ok")])
        source = sources.FixtureSource(self.box)
        got = [m.id for m in source.messages(["bills@acme.example"])]
        self.assertEqual(got, ["m1", "m1", "ok"])
        self.assertEqual(source.skipped, 3)

    def test_a_symlink_cannot_pull_a_file_from_outside_the_mailbox(self):
        outside = os.path.join(self.tmp.name, "secret.pdf")
        with open(outside, "wb") as f:
            f.write(b"%PDF-1.4 not really")
        os.symlink(outside, os.path.join(self.box, "a.pdf"))
        self.write_box([self.good(attachments=[{"filename": "a.pdf", "path": "a.pdf"}])])
        called = []
        original = sources.pdf_text
        sources.pdf_text = lambda data: called.append(data) or ""
        try:
            list(sources.FixtureSource(self.box).messages(["bills@acme.example"]))
        finally:
            sources.pdf_text = original
        self.assertEqual(called, [])

    def test_a_relative_mailbox_path_works(self):
        self.write_box([self.good(attachments=[{"filename": "a.pdf", "path": "missing.pdf"}])])
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            self.assertEqual(len(list(sources.FixtureSource("box").messages(["bills@acme.example"]))), 1)
        finally:
            os.chdir(cwd)

    def test_unruled_senders_give_no_subject(self):
        self.write_box([self.good(**{"from": "Shop <news@shop.example>", "subject": "code 482913"})])
        m = next(sources.FixtureSource(self.box).messages(["bills@acme.example"]))
        self.assertEqual((m.subject, m.body), ("", ""))

    def test_hostile_html_is_linear(self):
        start = time.monotonic()
        sources.html_to_text("<script>" * 32000 + "<style " * 40000)
        self.assertLess(time.monotonic() - start, FAST)

    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext not installed")
    def test_a_pdf_that_expands_to_huge_text_is_capped(self):
        text = (b"BT /F1 6 Tf 10 TL 10 800 Td " + b"(XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX) Tj T* " * 4000 + b"ET")
        stream = zlib.compress(text, 9)
        pages = 8
        objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % (5 + i) for i in range(pages)) + b"] /Count %d >>" % pages,
                b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream",
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
        objs += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 3 0 R >>"] * pages
        out, offsets = bytearray(b"%PDF-1.4\n"), []
        for i, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
        out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
        start = time.monotonic()
        result = sources.pdf_text(bytes(out))
        self.assertLessEqual(len(result.encode()), sources.MAX_PDF_TEXT)
        self.assertLess(time.monotonic() - start, 15)


class SenderParsing(unittest.TestCase):
    """The same answer on every Python version, and no address when the header is ambiguous."""

    def test_headers(self):
        from inbox_radar.model import parse_sender
        cases = {
            "Acme <bills@acme.example>": ("Acme", "bills@acme.example"),
            '"Acme, Inc." <bills@acme.example>': ("Acme, Inc.", "bills@acme.example"),
            "bills@acme.example": ("", "bills@acme.example"),
            "bills@acme.example <impostor@else.example>": ("bills@acme.example", "impostor@else.example"),
            '"bills@acme.example" <impostor@else.example>': ("bills@acme.example", "impostor@else.example"),
            "A <a@x.example>, B <bills@acme.example>": ("", ""),
            "bills@acme.example, impostor@else.example": ("", ""),
            "Name <<bills@acme.example>>": ("", ""),
            "": ("", ""),
        }
        for header, want in cases.items():
            self.assertEqual(parse_sender(header), want, header)
