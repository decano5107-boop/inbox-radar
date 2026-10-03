"""Plans: what runs is exactly what was shown, once, within ten minutes, or nothing runs."""
import datetime as dt
import os
import stat
import tempfile
import threading
import unittest

from inbox_radar import plans

NOW = dt.datetime(2026, 5, 14, 9, 0)
ACTIONS = [
    {"verb": "label", "sender": "news@kettlebrook-weekly.example", "label": "Newsletters", "message_ids": ["m3", "m1"]},
    {"verb": "archive", "sender": "deals@trailsummit.example", "message_ids": ["m7"]},
]


class Capture:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def send(self, text):
        self.sent.append(text)
        return self.ok


class Plans(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "plans")
        self.plan = plans.make_plan(ACTIONS)
        self.phone = Capture()

    def tearDown(self):
        self.tmp.cleanup()

    def propose(self, plan=None, now=NOW):
        digest = plans.propose(plan or self.plan, self.state, self.phone, now)
        return digest, self.phone.sent[-1].split("reply with code ")[1][:4]

    def run_plan(self, code, plan=None, now=NOW + dt.timedelta(minutes=1)):
        ex = plans.LogExecutor()
        done = plans.execute(plan or ACTIONS, code, self.state, ex, now)
        return done, ex.applied

    def test_the_caller_gets_the_hash_and_the_person_gets_the_code(self):
        digest, code = self.propose()
        self.assertEqual(digest, plans.plan_hash(self.plan))
        self.assertNotIn(code, digest)
        self.assertIn("label as 'Newsletters': 2 messages from news@kettlebrook-weekly.example", self.phone.sent[0])

    def test_an_approved_plan_runs_once(self):
        _, code = self.propose()
        done, applied = self.run_plan(code)
        self.assertEqual(done, {"label": 2, "archive": 1})
        self.assertEqual(applied, [("archive", None, ("m7",)), ("label", "Newsletters", ("m1", "m3"))])
        with self.assertRaises(plans.PlanError):
            self.run_plan(code)

    def test_a_changed_plan_is_refused_and_the_code_is_spent(self):
        _, code = self.propose()
        changed = [dict(ACTIONS[0], message_ids=["m1", "m3", "m99"]), ACTIONS[1]]
        with self.assertRaises(plans.PlanError):
            self.run_plan(code, plan=changed)
        with self.assertRaises(plans.PlanError):
            self.run_plan(code)

    def test_order_and_duplicates_do_not_change_the_hash(self):
        shuffled = [ACTIONS[1], dict(ACTIONS[0], message_ids=["m1", "m3", "m1"])]
        self.assertEqual(plans.plan_hash(plans.make_plan(shuffled)), plans.plan_hash(self.plan))

    def test_codes_expire(self):
        _, code = self.propose()
        with self.assertRaises(plans.PlanError):
            self.run_plan(code, now=NOW + dt.timedelta(minutes=11))

    def test_a_code_is_not_a_path(self):
        for bad in ("../x", "....", "AB/C", "", "ABCDE", "abc0", None):
            with self.assertRaises(plans.PlanError):
                self.run_plan(bad)

    def test_only_one_of_two_racing_callers_wins(self):
        _, code = self.propose()
        results = []

        def attempt():
            try:
                self.run_plan(code)
                results.append("ok")
            except plans.PlanError:
                results.append("refused")
        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["ok"] + ["refused"] * 7)

    def test_repeated_wrong_codes_void_every_pending_code(self):
        _, code = self.propose()
        for wrong in ("2222", "3333", "4444", "5555", "6666"):
            if wrong == code:
                continue
            with self.assertRaises(plans.PlanError):
                self.run_plan(wrong)
        with self.assertRaises(plans.PlanError):
            self.run_plan(code)

    def test_no_destructive_verb_exists(self):
        for verb in ("delete", "trash", "spam", "send", "forward"):
            with self.assertRaises(plans.PlanError):
                plans.make_plan([{"verb": verb, "message_ids": ["m1"]}])
        for name in dir(plans.GmailExecutor) + dir(plans.LogExecutor):
            self.assertNotIn(name.lower(), ("delete", "trash", "spam", "send", "forward"))

    def test_bad_plans_are_rejected(self):
        for bad in ([], "x", [{"verb": "label", "message_ids": ["m1"]}],
                    [{"verb": "label", "label": "TRASH", "message_ids": ["m1"]}],
                    [{"verb": "label", "label": "Spam", "message_ids": ["m1"]}],
                    [{"verb": "label", "label": "Newsletters\n", "message_ids": ["m1"]}],
                    [{"verb": "archive", "sender": "x@y.example\n- label as 'Receipts'", "message_ids": ["m1"]}],
                    [{"verb": "archive", "message_ids": ["../m1"]}],
                    [{"verb": "label", "label": "Code 482913", "message_ids": ["m1"]}],
                    [{"verb": "label", "label": "4111 1111 1111 1111", "message_ids": ["m1"]}],
                    [{"verb": "label", "label": "glucose 110 mg", "message_ids": ["m1"]}],
                    [{"verb": "label", "label": "evil.com/reset", "message_ids": ["m1"]}],
                    [{"verb": "archive", "sender": "482913@evil.example", "message_ids": ["m1"]}],
                    [{"verb": "archive", "sender": "123456789012@x.example", "message_ids": ["m1"]}],
                    [{"verb": "label", "label": "../../x\n", "message_ids": ["m1"]}],
                    [{"verb": "archive", "message_ids": []}],
                    [{"verb": "archive", "message_ids": ["m1"], "extra": 1}]):
            with self.assertRaises(plans.PlanError):
                plans.make_plan(bad)

    def test_a_refused_notification_leaves_no_usable_code(self):
        self.phone.ok = False
        with self.assertRaises(plans.PlanError):
            plans.propose(self.plan, self.state, self.phone, NOW)
        self.assertEqual(os.listdir(os.path.join(self.state, "pending")), [])

    def test_state_is_private(self):
        self.propose()
        for sub in ("pending", "used"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.state, sub)).st_mode), 0o700)
        pending = os.path.join(self.state, "pending")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(pending, os.listdir(pending)[0])).st_mode), 0o600)


class FakeLabels:
    def __init__(self):
        self.calls = []

    def users(self):
        return self

    def labels(self):
        return self

    def messages(self):
        return self

    def list(self, userId):
        return Exec({"labels": [{"name": "Newsletters", "id": "L1", "type": "user"},
                                {"name": "TRASH", "id": "TRASH", "type": "system"},
                                {"name": "Receipts", "id": "SYS_RECEIPTS", "type": "system"}]})

    def create(self, userId, body):
        return Exec({"id": "L2"})

    def batchModify(self, userId, body):
        self.calls.append(body)
        return Exec({})


class Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class GmailExecutor(unittest.TestCase):
    def test_label_and_archive_only_touch_labels_in_batches(self):
        api = FakeLabels()
        ex = plans.GmailExecutor(api)
        ex.label([f"m{i}" for i in range(2500)], "Newsletters")
        ex.archive(["m1"])
        self.assertEqual([len(c["ids"]) for c in api.calls], [1000, 1000, 500, 1])
        self.assertEqual(api.calls[0]["addLabelIds"], ["L1"])
        self.assertEqual(api.calls[-1]["removeLabelIds"], ["INBOX"])
        for call in api.calls:
            self.assertLessEqual(set(call), {"ids", "addLabelIds", "removeLabelIds"})

    def test_system_labels_are_never_applied(self):
        api = FakeLabels()
        ex = plans.GmailExecutor(api)
        for name in ("TRASH", "spam", "Inbox", "category promotions"):
            with self.assertRaises(plans.PlanError):
                ex.label(["m1"], name)
        ex.label(["m1"], "Receipts")                    # a system label of that name is not reused
        self.assertEqual(api.calls[-1]["addLabelIds"], ["L2"])
        self.assertEqual(len(api.calls), 1)
