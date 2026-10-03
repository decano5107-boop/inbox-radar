"""Decide what is worth saying today, and say each thing once.

Reminder steps are 7, 3 and 1 days before the due date and the due date itself. A run announces
the step that matches today's distance to the due date; earlier steps it missed while the tool
was not running are recorded as skipped, so they never arrive late and out of order. Past the
due date there is one overdue notice. Large card charges get one notice each.
"""
import datetime as dt
from dataclasses import dataclass, field

from . import store
from .text import money

STEPS = (7, 3, 1, 0)
OVERDUE = "overdue"
OVERDUE_WINDOW = 30          # days; older unpaid items are listed by `status`, not announced
CHARGE_WINDOW = 2            # days; a first run does not alert on last month's charges


@dataclass
class Notice:
    kind: str                # "due", "overdue" or "charge"
    key: str
    step: str
    text: str
    fields: dict = field(default_factory=dict)


def step_for(days_left):
    """The step today falls on, or None when the due date is more than a week away."""
    if days_left < 0:
        return OVERDUE
    reached = [s for s in STEPS if days_left <= s]
    return str(min(reached)) if reached else None


def _when(days_left, due):
    if days_left == 0:
        return "due today"
    if days_left == 1:
        return f"due tomorrow ({due:%a %d %b})"
    if days_left > 1:
        return f"due in {days_left} days ({due:%a %d %b})"
    return f"overdue since {due:%a %d %b}"


def _card(last_four):
    return f", ending in {last_four}" if last_four else ""


def long_overdue(db, today):
    """Open obligations more than OVERDUE_WINDOW days past due: no longer announced, listed by
    the `status` command so they are not silently forgotten."""
    return [ob for ob in store.open_obligations(db) if (ob.due - today).days < -OVERDUE_WINDOW]


def due_notices(db, today):
    notices = []
    for ob in store.open_obligations(db):
        days_left = (ob.due - today).days
        if days_left < -OVERDUE_WINDOW:
            continue
        step = step_for(days_left)
        if step is None:
            continue
        done = store.recorded(db, ob.key)
        if step in done or (step == OVERDUE and done.get("0")):
            continue                                  # said already, or said on the due date
        if not store.claim(db, ob.key, step, now()):
            continue                                  # another run has it
        for earlier in STEPS:                         # missed steps: recorded, never sent late
            if earlier > (-1 if step == OVERDUE else int(step)):
                store.mark(db, ob.key, str(earlier), sent=False, when=today)
        kind = "overdue" if step == OVERDUE else "due"
        text = (f"{ob.issuer} — {ob.concept}: {money(ob.amount, ob.currency)}, "
                f"{_when(days_left, ob.due)}{_card(ob.last_four)}.")
        notices.append(Notice(kind, ob.key, step, text, {
            "issuer": ob.issuer, "concept": ob.concept, "amount": str(ob.amount),
            "currency": ob.currency, "due": ob.due.isoformat(), "last_four": ob.last_four}))
    return notices


def charge_notices(db, today, thresholds):
    notices = []
    for mv in store.movements_since(db, today - dt.timedelta(days=CHARGE_WINDOW)):
        if mv.amount < thresholds.get(mv.currency, float("inf")):      # at or above alerts
            continue
        key = f"charge|{mv.message_id}"
        if store.recorded(db, key) or not store.claim(db, key, "charge", now()):
            continue
        text = f"{mv.issuer}: a charge of {money(mv.amount, mv.currency)}{_card(mv.last_four)}."
        notices.append(Notice("charge", key, "charge", text, {
            "issuer": mv.issuer, "amount": str(mv.amount), "currency": mv.currency,
            "date": mv.received.isoformat(), "last_four": mv.last_four}))
    return notices


def run(db, today, thresholds):
    """The notices due today, most pressing first. An empty list means: say nothing."""
    notices = charge_notices(db, today, thresholds) + due_notices(db, today)
    order = {"overdue": 0, "charge": 1, "due": 2}
    notices.sort(key=lambda n: (order[n.kind], n.fields.get("due", ""), n.key))
    return notices


def confirm_sent(db, notice, today=None):
    """Record a notice as delivered. Call it only after the notifier accepted the notice."""
    store.confirm(db, notice.key, notice.step)


def release(db, notice):
    """Give a notice back when delivery failed, so the next run offers it again."""
    store.release(db, notice.key, notice.step)


def now():
    return dt.datetime.now()
