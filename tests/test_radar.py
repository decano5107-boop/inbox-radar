"""The reminder schedule: each step at most once, missed steps never late, quiet otherwise."""
import datetime as dt
import os
import stat
import tempfile
import unittest
from decimal import Decimal

from inbox_radar import radar, store
from inbox_radar.model import Movement, Obligation

DUE = dt.date(2026, 5, 20)
LIMITS = {"USD": 100, "EUR": 100}


def bill(due=DUE, msg="m1", amount="80.10"):
    return Obligation(msg, "Acme Power", "power bill", "USD", Decimal(amount), due, "1234")


class Schedule(unittest.TestCase):
    def setUp(self):
        self.db = store.connect(":memory:")

    def day(self, days_before_due, confirm=True):
        today = DUE - dt.timedelta(days=days_before_due)
        notices = radar.run(self.db, today, LIMITS)
        for n in notices:
            (radar.confirm_sent if confirm else radar.release)(self.db, n)
        return notices

    def test_step_for(self):
        expected = {10: None, 8: None, 7: "7", 5: "7", 3: "3", 2: "3", 1: "1", 0: "0", -1: "overdue"}
        for days, step in expected.items():
            self.assertEqual(radar.step_for(days), step, days)

    def test_a_daily_run_announces_each_step_once(self):
        store.save(self.db, bill())
        said = [n.step for d in range(10, -4, -1) for n in self.day(d)]
        self.assertEqual(said, ["7", "3", "1", "0"])

    def test_steps_missed_while_off_are_not_sent_late(self):
        store.save(self.db, bill())
        self.assertEqual([n.step for n in self.day(2)], ["3"])      # first run, 2 days before
        self.assertEqual(self.day(2), [])
        self.assertEqual(store.recorded(self.db, bill().key), {"7": False, "3": True})

    def test_overdue_is_announced_once_when_the_due_date_was_missed(self):
        store.save(self.db, bill())
        self.assertEqual([n.kind for n in self.day(-2)], ["overdue"])
        self.assertEqual(self.day(-3), [])

    def test_no_overdue_after_a_due_date_notice(self):
        store.save(self.db, bill())
        self.assertEqual([n.step for n in self.day(0)], ["0"])
        self.assertEqual(self.day(-1), [])

    def test_a_notice_that_was_not_delivered_is_offered_again(self):
        store.save(self.db, bill())
        self.assertEqual(len(self.day(-1, confirm=False)), 1)
        self.assertEqual([n.kind for n in self.day(-1)], ["overdue"])

    def test_two_runs_at_once_cannot_both_take_a_step(self):
        store.save(self.db, bill())
        today = DUE - dt.timedelta(days=3)
        first = radar.run(self.db, today, LIMITS)          # not confirmed yet: still in flight
        second = radar.run(self.db, today, LIMITS)
        self.assertEqual((len(first), len(second)), (1, 0))

    def test_a_claim_left_by_a_crashed_run_expires(self):
        store.save(self.db, bill())
        key = bill().key
        self.assertTrue(store.claim(self.db, key, "3", dt.datetime(2026, 5, 17, 8, 0)))
        self.assertFalse(store.claim(self.db, key, "3", dt.datetime(2026, 5, 17, 8, 30)))
        self.assertTrue(store.claim(self.db, key, "3", dt.datetime(2026, 5, 17, 9, 1)))
        store.confirm(self.db, key, "3")
        self.assertFalse(store.claim(self.db, key, "3", dt.datetime(2026, 5, 18, 9, 0)))

    def test_repeated_past_due_notices_in_a_month_are_one_obligation(self):
        a = Obligation("p1", "Gym", "fee", "USD", Decimal("49"), dt.date(2026, 5, 9), None, "high", True)
        b = Obligation("p2", "Gym", "fee", "USD", Decimal("49"), dt.date(2026, 5, 12), None, "high", True)
        store.save(self.db, a)
        store.save(self.db, b)
        self.assertEqual(len(radar.run(self.db, dt.date(2026, 5, 14), LIMITS)), 1)

    def test_past_due_repeats_across_a_month_boundary_are_one_obligation(self):
        a = Obligation("p1", "Gym", "fee", "USD", Decimal("49"), dt.date(2026, 4, 30), None, "high", True)
        b = Obligation("p2", "Gym", "fee", "USD", Decimal("49"), dt.date(2026, 5, 1), None, "high", True)
        store.save(self.db, a)
        store.save(self.db, b)
        self.assertEqual(len(radar.run(self.db, dt.date(2026, 5, 3), LIMITS)), 1)

    def test_the_earliest_due_date_represents_a_merged_obligation(self):
        for msg, due in (("m10", dt.date(2026, 5, 2)), ("m9", dt.date(2026, 5, 9))):
            store.save(self.db, Obligation(msg, "Gym", "fee", "USD", Decimal("49"), due, None, "high", True))
        self.assertEqual(store.open_obligations(self.db)[0].due, dt.date(2026, 5, 2))

    def test_long_overdue_bills_are_listed_not_announced(self):
        store.save(self.db, bill(due=DUE - dt.timedelta(days=45)))
        self.assertEqual(self.day(0), [])
        self.assertEqual(len(radar.long_overdue(self.db, DUE)), 1)

    def test_quiet_when_nothing_is_near(self):
        store.save(self.db, bill(due=DUE + dt.timedelta(days=30)))
        self.assertEqual(self.day(0), [])

    def test_two_emails_for_one_bill_give_one_notice(self):
        store.save(self.db, bill(msg="m1"))
        store.save(self.db, bill(msg="m2"))
        self.assertEqual(len(self.day(3)), 1)

    def test_paid_and_low_confidence_are_never_announced(self):
        store.save(self.db, bill())
        store.set_status(self.db, bill().key, "paid")
        store.save(self.db, Obligation("m9", "B", "bill", "USD", Decimal("5"), None, None, "low"))
        self.assertEqual(self.day(1), [])
        self.assertEqual(len(store.needs_review(self.db)), 1)

    def test_old_overdue_items_are_not_announced(self):
        store.save(self.db, bill(due=DUE - dt.timedelta(days=45)))
        self.assertEqual(self.day(0), [])

    def test_the_notice_text_is_made_of_fields(self):
        store.save(self.db, bill())
        n = self.day(5)[0]
        self.assertEqual(n.text, "Acme Power — power bill: $80.10, due in 5 days (Wed 20 May), ending in 1234.")

    def test_the_database_refuses_a_duplicate_step(self):
        self.assertTrue(store.mark(self.db, "k", "3", True, DUE))
        self.assertFalse(store.mark(self.db, "k", "3", True, DUE))


class Charges(unittest.TestCase):
    def setUp(self):
        self.db = store.connect(":memory:")

    def charge(self, amount, received, msg="c1", currency="USD"):
        store.save(self.db, Movement(msg, "Bank", Decimal(amount), currency, received, "1234"))

    def test_only_recent_charges_over_the_threshold_once_each(self):
        today = dt.date(2026, 5, 14)
        self.charge("500.00", today, "c1")
        self.charge("99.99", today, "c2")
        self.charge("900.00", today - dt.timedelta(days=10), "c3")
        notices = radar.run(self.db, today, LIMITS)
        self.assertEqual([n.key for n in notices], ["charge|c1"])
        radar.confirm_sent(self.db, notices[0], today)
        self.assertEqual(radar.run(self.db, today, LIMITS), [])


class Files(unittest.TestCase):
    def test_the_database_file_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out", "radar.db")
            store.connect(path).close()
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
