#!/usr/bin/env python3
"""Run the whole pipeline on the fictional mailbox, in a new temporary folder.

    python3 examples/demo.py

Generates the mailbox, scans it, walks the reminder schedule over two weeks, shows the veto
blocking a notice that a careless rule would have produced, and approves a bulk-archive plan
with a one-time code. Nothing outside the temporary folder is read or written.
"""
import contextlib
import datetime as dt
import io
import json
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "mailbox"))

import generate  # noqa: E402
from inbox_radar import notify, pipeline, plans, radar, rules as R, sources, store, veto  # noqa: E402
from inbox_radar.radar import Notice  # noqa: E402


def title(text):
    print(f"\n── {text} " + "─" * max(0, 66 - len(text)))


class Phone:
    """Stands in for the person's phone: prints what it receives."""

    def __init__(self):
        self.received = []

    def send(self, text):
        self.received.append(text)
        for line in text.splitlines():
            print(f"   📱 {line}")
        return True


def main(folder=None):
    folder = folder or tempfile.mkdtemp(prefix="inbox-radar-demo-")
    today = dt.date.today()
    mailbox = os.path.join(folder, "mailbox")
    generate.generate(mailbox, today)
    rules, problems = R.load(os.path.join(HERE, "rules.example.json"))
    assert not problems, problems
    db = store.connect(os.path.join(folder, "radar.db"))

    title("1. scan: rules turn bills into fields; everything else is only triaged")
    if not shutil.which("pdftotext"):
        print("   (pdftotext not installed: bills whose figures are only in a PDF will need review)")
    result = pipeline.scan(sources.FixtureSource(mailbox), rules, db)
    counts = result.counts()
    print(f"   {len(result.buckets)} messages: {counts.get('urgent', 0)} urgent, "
          f"{counts.get('important', 0)} important, {counts.get('ignore', 0)} ignore")
    print(f"   {result.obligations - result.review} bills read, {result.movements} card charges, "
          f"{result.review} need review")

    title("2. remind, day by day for two weeks: each step once, quiet days print nothing")
    phone = Phone()
    for offset in range(0, 15):
        day = today + dt.timedelta(days=offset)
        notices = radar.run(db, day, rules.thresholds)
        if notices:
            print(f"   {day:%a %d %b}")
            notify.deliver(db, notices, phone)

    title("3. the veto: a notice that carries a passcode is not sent")
    leaky = Notice("due", "demo|leak", "7", "Tindleworth — order: your verification code is 482913.", {})
    store.claim(db, leaky.key, leaky.step, dt.datetime.now())
    events = []
    sent, blocked, failed = notify.deliver(db, [leaky], phone, events.append)
    print(f"   sent {sent}, blocked {blocked}; log entry: {json.dumps(events[0])}")
    print(f"   the five sensitive messages in the mailbox, audited: "
          + ", ".join("/".join(veto.audit(m)) for m in _sensitive_bodies(mailbox)))

    title("4. a bulk plan: archive two newsletters, approved with a one-time code")
    plan = plans.make_plan([
        {"verb": "archive", "sender": "news@kettlebrook-weekly.example", "message_ids": _ids(mailbox, "news@kettlebrook-weekly.example")},
        {"verb": "label", "sender": "deals@trailsummit.example", "label": "Deals",
         "message_ids": _ids(mailbox, "deals@trailsummit.example")},
    ])
    state = os.path.join(folder, "plans")
    digest = plans.propose(plan, state, phone)
    print(f"   the caller got back only the hash: {digest[:12]}…")
    code = phone.received[-1].split("reply with code ")[1][:4]
    tampered = plan + [{"verb": "archive", "sender": "statements@copperline.example", "message_ids": ["m0001"]}]
    with contextlib.suppress(plans.PlanError):
        plans.execute(tampered, code, state, plans.LogExecutor())
    print("   a plan with one extra action, with the right code: refused, and the code is spent")
    digest = plans.propose(plan, state, phone)
    code = phone.received[-1].split("reply with code ")[1][:4]
    executor = plans.LogExecutor()
    done = plans.execute(plan, code, state, executor)
    print(f"   the plan as shown, with its code: {done} (dry run, nothing deleted, ever)")

    print(f"\nDemo folder: {folder}  (delete it when you are done)")
    db.close()
    return folder


def _sensitive_bodies(mailbox):
    with open(os.path.join(mailbox, "expected.json"), encoding="utf-8") as f:
        expected = json.load(f)["messages"]
    with open(os.path.join(mailbox, "messages.jsonl"), encoding="utf-8") as f:
        return [m["body"] for m in map(json.loads, f) if expected[m["id"]]["sensitive"]]


def _ids(mailbox, sender):
    with open(os.path.join(mailbox, "messages.jsonl"), encoding="utf-8") as f:
        return [m["id"] for m in map(json.loads, f) if sender in m["from"]]


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        main(sys.argv[1] if len(sys.argv) > 1 else None)
