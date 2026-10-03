"""Extraction, rules and triage on hand-built messages: one behaviour per test."""
import datetime as dt
import unittest
from decimal import Decimal

from inbox_radar import extract, rules as R, triage
from inbox_radar.model import Message, Movement, Obligation

WHEN = dt.datetime(2026, 5, 10, 9, 0)


def rule(**kw):
    base = {"issuer": "Acme Power", "senders": ["bills@acme.example"], "concept": "power bill",
            "currency": "USD", "amount_anchors": ["amount due"], "date_anchors": ["due date"]}
    base.update(kw)
    rules, problems = R.parse({"issuers": [base]})
    assert problems == [], problems
    return rules.issuers[0]


def msg(body, sender="Acme Power <bills@acme.example>", subject="Your bill", headers=None, pdf=""):
    m = Message("x1", sender, subject, WHEN, headers or {}, body)
    m.attachment_text = pdf
    return m


class Extraction(unittest.TestCase):
    def test_reads_the_value_after_its_anchor_not_the_first_number(self):
        ob = extract.extract(msg("Promo $5.00 off!\nAmount due $80.10\nDue date May 20, 2026"), rule())
        self.assertEqual((ob.amount, ob.due, ob.confidence), (Decimal("80.10"), dt.date(2026, 5, 20), "high"))

    def test_a_confirm_phrase_skips_a_lookalike_block(self):
        body = ("AMOUNT DUE $0.99 PAY NOW\n\nAccount summary\nAmount due $80.10\n"
                "Due date May 20, 2026")
        ob = extract.extract(msg(body), rule(amount_confirm=["account summary"]))
        self.assertEqual(ob.amount, Decimal("80.10"))

    def test_without_the_confirm_phrase_nothing_is_taken(self):
        ob = extract.extract(msg("Amount due $80.10\nDue date May 20, 2026"),
                             rule(amount_confirm=["account summary"]))
        self.assertIsNone(ob.amount)
        self.assertEqual(ob.confidence, "low")

    def test_without_a_signal_phrase_it_is_not_a_bill(self):
        self.assertIsNone(extract.extract(msg("Save $20 until June 1, 2026"), rule(signal=["amount due"])))

    def test_an_amount_in_another_currency_is_ignored(self):
        ob = extract.extract(msg("Amount due 80,10 €\nDue date May 20, 2026"), rule())
        self.assertIsNone(ob.amount)

    def test_no_date_means_now_uses_the_send_date(self):
        ob = extract.extract(msg("Your fee of $10.00 is past due."),
                             rule(amount_anchors=["fee of"], date_anchors=[], no_date_means_now=True))
        self.assertEqual(ob.due, WHEN.date())
        self.assertEqual(ob.confidence, "high")

    def test_missing_date_without_the_flag_is_low_confidence(self):
        ob = extract.extract(msg("Amount due $80.10"), rule())
        self.assertEqual((ob.due, ob.confidence), (None, "low"))

    def test_figures_in_the_attachment_count(self):
        ob = extract.extract(msg("Your statement is attached.", pdf="Amount due $12.00\nDue date 05/20/2026"), rule())
        self.assertEqual((ob.amount, ob.due), (Decimal("12.00"), dt.date(2026, 5, 20)))

    def test_a_card_number_before_the_amount_is_not_the_amount(self):
        ob = extract.extract(msg("Amount due for card 1234: $80.10\nDue date May 20, 2026"), rule())
        self.assertEqual(ob.amount, Decimal("80.10"))

    def test_a_credit_is_not_an_amount_owed(self):
        for body in ("Amount due -$50.00", "Amount due ($50.00)", "Amount due −$50.00"):
            self.assertIsNone(extract.extract(msg(body + "\nDue date May 20, 2026"), rule()).amount, body)

    def test_an_anchor_without_a_value_does_not_borrow_the_next_figure(self):
        ob = extract.extract(msg("Amount due shown in the app.\nMinimum $30.00\n"
                                 "Due date see below\nStatement closing date May 1, 2026"), rule())
        self.assertEqual((ob.amount, ob.due), (None, None))

    def test_a_label_above_its_value_is_read(self):
        ob = extract.extract(msg("Amount due\n$80.10\nDue date\nMay 20, 2026"), rule())
        self.assertEqual((ob.amount, ob.due), (Decimal("80.10"), dt.date(2026, 5, 20)))

    def test_accented_rule_phrases_match_either_spelling(self):
        r = rule(amount_anchors=["montant dû"], date_anchors=["date d'échéance"],
                 currency="EUR", number_format="1.234,56", date_order="DMY")
        ob = extract.extract(msg("Montant du 80,10 €\nDate d'echeance 20/05/2026"), r)
        self.assertEqual((ob.amount, ob.due), (Decimal("80.10"), dt.date(2026, 5, 20)))

    def test_a_code_that_belongs_to_the_next_figure_does_not_make_a_bare_number_money(self):
        ob = extract.extract(msg("Amount due 1234 USD 1,234.56\nDue date May 20, 2026"), rule())
        self.assertEqual(ob.amount, Decimal("1234.56"))

    def test_windows_line_endings(self):
        ob = extract.extract(msg("Amount due\r\n$80.10\r\nDue date\r\nMay 20, 2026"), rule())
        self.assertEqual((ob.amount, ob.due), (Decimal("80.10"), dt.date(2026, 5, 20)))

    def test_more_ways_of_writing_a_credit(self):
        for body in ("Amount due –$50.00", "Amount due $50.00 CR", "Amount due $50.00 credit"):
            self.assertIsNone(extract.extract(msg(body + "\nDue date May 20, 2026"), rule()).amount, body)

    def test_an_unreadable_charge_is_kept_for_review(self):
        found = extract.extract(msg("A charge of USD 1.234,56 at SHOP"),
                                rule(amount_anchors=["a charge of"], movement=True))
        self.assertIsInstance(found, Obligation)
        self.assertEqual(found.confidence, "low")

    def test_movements(self):
        mv = extract.extract(msg("A charge of $500.00 at SHOP, card ending in 1234"),
                             rule(amount_anchors=["a charge of"], movement=True))
        self.assertIsInstance(mv, Movement)
        self.assertEqual((mv.amount, mv.last_four), (Decimal("500.00"), "1234"))

    def test_the_same_bill_has_the_same_key(self):
        a = Obligation("1", "A", "bill", "USD", Decimal("1.00"), dt.date(2026, 1, 1))
        b = Obligation("2", "A", "bill", "USD", Decimal("1.00"), dt.date(2026, 1, 1))
        self.assertEqual(a.key, b.key)


class RuleValidation(unittest.TestCase):
    def test_bad_rules_are_dropped_and_named(self):
        rules, problems = R.parse({"issuers": [
            {"issuer": "A", "senders": ["a@a.example"], "concept": "c", "currency": "USD",
             "amount_anchors": ["x"], "number_format": "1 234,56"},
            {"issuer": "B", "senders": ["b@b.example"], "concept": "c", "currency": "USD"},
            {"issuer": "C", "senders": ["c@c.example"], "concept": "c", "currency": "USD",
             "amount_anchors": ["x"], "colour": "red"},
            "nope",
        ]})
        self.assertEqual(rules.issuers, [])
        self.assertEqual(len(problems), 4)

    def test_a_sender_in_two_rules_is_reported(self):
        same = {"senders": ["a@a.example"], "concept": "c", "currency": "USD", "amount_anchors": ["x"]}
        _rules, problems = R.parse({"issuers": [dict(same, issuer="A"), dict(same, issuer="B")]})
        self.assertTrue(any("two rules" in p for p in problems))

    def test_thresholds_and_triage_lists(self):
        rules, problems = R.parse({"thresholds": {"USD": 250, "EUR": -1}, "triage": {"important": ["X.example"]}})
        self.assertEqual(rules.thresholds["USD"], 250)
        self.assertEqual(rules.thresholds["EUR"], 100)
        self.assertEqual(rules.important, ("x.example",))
        self.assertEqual(len(problems), 1)


class Triage(unittest.TestCase):
    def setUp(self):
        self.rules, _ = R.parse({"triage": {"important": ["clinic.example"], "ignore": ["pushy.example"]},
                                 "issuers": [{"issuer": "Gym", "senders": ["m@gym.example"],
                                              "concept": "fee", "currency": "USD",
                                              "amount_anchors": ["fee of"], "no_date_means_now": True}]})

    def bucket(self, sender, found=None, headers=None, overrides=None):
        return triage.classify(msg("", sender=sender, headers=headers), self.rules, found, overrides)

    def test_people_are_important_and_role_addresses_are_not(self):
        self.assertEqual(self.bucket("Sam Okafor <sam@okafor.example>"), "important")
        self.assertEqual(self.bucket("Records Desk <records@collector.example>"), "ignore")
        self.assertEqual(self.bucket("Sam <no-reply@okafor.example>"), "ignore")
        self.assertEqual(self.bucket("sam@okafor.example"), "ignore")          # no name: not enough
        self.assertEqual(self.bucket("Customer Care <care@bigshop.example>"), "ignore")

    def test_bulk_headers_mean_ignore(self):
        self.assertEqual(self.bucket("Jo Bloggs <jo@shop.example>", headers={"List-Unsubscribe": "<x>"}), "ignore")

    def test_lists_and_overrides(self):
        self.assertEqual(self.bucket("Lab <results@clinic.example>"), "important")
        self.assertEqual(self.bucket("Jo Bloggs <jo@pushy.example>"), "ignore")
        self.assertEqual(self.bucket("Jo Bloggs <jo@shop.example>", overrides={"shop.example": "ignore"}), "ignore")

    def test_extraction_results(self):
        big = Movement("1", "Bank", Decimal("100.00"), "USD", WHEN.date())
        small = Movement("1", "Bank", Decimal("99.99"), "USD", WHEN.date())
        self.assertEqual(self.bucket("Bank <a@bank.example>", big), "urgent")
        self.assertEqual(self.bucket("Bank <a@bank.example>", small), "ignore")
        past_due = Obligation("1", "Gym", "fee", "USD", Decimal("49"), WHEN.date())
        self.assertEqual(self.bucket("Gym <m@gym.example>", past_due), "urgent")
        bill = Obligation("1", "X", "bill", "USD", Decimal("49"), WHEN.date())
        self.assertEqual(self.bucket("X <bills@x.example>", bill), "important")
