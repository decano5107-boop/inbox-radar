"""SQLite: obligations, movements, the reminder log and user overrides. No message text.

Amounts are stored as decimal strings, never floats. The reminder log's primary key is
(obligation key, step), so the database refuses to record the same reminder twice.
"""
import datetime as dt
import os
import sqlite3
from decimal import Decimal

from .model import Movement, Obligation

SCHEMA = """
CREATE TABLE IF NOT EXISTS obligations (
  message_id TEXT PRIMARY KEY,
  key        TEXT NOT NULL,
  issuer     TEXT NOT NULL,
  concept    TEXT NOT NULL,
  currency   TEXT NOT NULL,
  amount     TEXT,
  due        TEXT,
  last_four  TEXT,
  confidence TEXT NOT NULL,
  inferred   INTEGER NOT NULL DEFAULT 0,           -- 1: due date is the send date of a past-due notice
  status     TEXT NOT NULL DEFAULT 'open'          -- open | paid | dismissed
);
CREATE INDEX IF NOT EXISTS obligations_key ON obligations(key);
CREATE TABLE IF NOT EXISTS movements (
  message_id TEXT PRIMARY KEY,
  issuer     TEXT NOT NULL,
  amount     TEXT NOT NULL,
  currency   TEXT NOT NULL,
  received   TEXT NOT NULL,
  last_four  TEXT
);
CREATE TABLE IF NOT EXISTS reminders (
  key     TEXT NOT NULL,
  step    TEXT NOT NULL,
  sent_at TEXT NOT NULL,
  sent    INTEGER NOT NULL,                          -- 0 skipped, 1 announced, 2 claimed, 3 blocked
  PRIMARY KEY (key, step)
);
CREATE TABLE IF NOT EXISTS overrides (
  sender TEXT PRIMARY KEY,                           -- an address or a domain
  bucket TEXT NOT NULL CHECK (bucket IN ('urgent', 'important', 'ignore'))
);
"""


def connect(path):
    if path != ":memory:":
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, mode=0o700, exist_ok=True)
        if not os.path.exists(path):
            os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))   # private from the first byte
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


MERGE_DAYS = 31


def _key_for(db, item):
    """A past-due notice repeated within MERGE_DAYS of an earlier one for the same amount is the
    same obligation, whatever the calendar month."""
    if not (item.inferred_due and item.due):
        return item.key
    row = db.execute("SELECT key FROM obligations WHERE issuer = ? AND concept = ? AND amount = ?"
                     " AND currency = ? AND inferred = 1 AND due BETWEEN ? AND ? AND message_id != ?"
                     " ORDER BY due LIMIT 1",
                     (item.issuer, item.concept, str(item.amount), item.currency,
                      (item.due - dt.timedelta(days=MERGE_DAYS)).isoformat(),
                      (item.due + dt.timedelta(days=MERGE_DAYS)).isoformat(), item.message_id)).fetchone()
    return row["key"] if row else item.key


def save(db, item):
    if isinstance(item, Obligation):
        db.execute(
            "INSERT INTO obligations (message_id, key, issuer, concept, currency, amount, due,"
            " last_four, confidence, inferred) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(message_id) DO UPDATE SET key=excluded.key, amount=excluded.amount,"
            " due=excluded.due, last_four=excluded.last_four, confidence=excluded.confidence,"
            " inferred=excluded.inferred",
            (item.message_id, _key_for(db, item), item.issuer, item.concept, item.currency,
             None if item.amount is None else str(item.amount),
             None if item.due is None else item.due.isoformat(), item.last_four, item.confidence,
             1 if item.inferred_due else 0))
    elif isinstance(item, Movement):
        db.execute(
            "INSERT OR IGNORE INTO movements (message_id, issuer, amount, currency, received, last_four)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (item.message_id, item.issuer, str(item.amount), item.currency,
             item.received.isoformat(), item.last_four))
    db.commit()


def _obligation(row):
    return Obligation(row["message_id"], row["issuer"], row["concept"], row["currency"],
                      None if row["amount"] is None else Decimal(row["amount"]),
                      None if row["due"] is None else dt.date.fromisoformat(row["due"]),
                      row["last_four"], row["confidence"], bool(row["inferred"]), row["key"])


def open_obligations(db):
    """One Obligation per key (the same bill from two emails is one), high confidence only."""
    rows = db.execute("SELECT *, MIN(due) FROM obligations WHERE status = 'open'"
                      " AND confidence = 'high' GROUP BY key ORDER BY due, issuer").fetchall()
    return [_obligation(r) for r in rows]          # SQLite takes the bare columns from the MIN(due) row


def needs_review(db):
    rows = db.execute("SELECT * FROM obligations WHERE confidence = 'low' AND status = 'open'"
                      " ORDER BY issuer").fetchall()
    return [_obligation(r) for r in rows]


def all_obligations(db, status=("open", "paid", "dismissed")):
    marks = ",".join("?" * len(status))
    rows = db.execute(f"SELECT *, MIN(due) FROM obligations WHERE status IN ({marks})"
                      " GROUP BY key ORDER BY due, issuer", tuple(status)).fetchall()
    return [_obligation(r) for r in rows]


def movements_since(db, since):
    rows = db.execute("SELECT * FROM movements WHERE received >= ? ORDER BY received, message_id",
                      (since.isoformat(),)).fetchall()
    return [Movement(r["message_id"], r["issuer"], Decimal(r["amount"]), r["currency"],
                     dt.date.fromisoformat(r["received"]), r["last_four"]) for r in rows]


def mark(db, key, step, sent, when):
    """Record a step; returns False when it was already recorded (the primary key refuses it)."""
    try:
        db.execute("INSERT INTO reminders (key, step, sent_at, sent) VALUES (?, ?, ?, ?)",
                   (key, step, when.isoformat(), 1 if sent else 0))
        db.commit()
        return True
    except sqlite3.IntegrityError:
        return False


CLAIM_TTL = dt.timedelta(hours=1)


def claim(db, key, step, now):
    """Reserve a step for one run, atomically. True if this run may send it. A claim that was
    never confirmed (the run crashed) expires after CLAIM_TTL, so a reminder is retried rather
    than lost; a confirmed or skipped step can never be claimed again."""
    try:
        db.execute("INSERT INTO reminders (key, step, sent_at, sent) VALUES (?, ?, ?, 2)",
                   (key, step, now.isoformat()))
        db.commit()
        return True
    except sqlite3.IntegrityError:
        cur = db.execute("UPDATE reminders SET sent_at = ? WHERE key = ? AND step = ? AND sent = 2"
                         " AND sent_at < ?", (now.isoformat(), key, step, (now - CLAIM_TTL).isoformat()))
        db.commit()
        return cur.rowcount == 1


def confirm(db, key, step):
    db.execute("UPDATE reminders SET sent = 1 WHERE key = ? AND step = ? AND sent = 2", (key, step))
    db.commit()


def block(db, key, step):
    """The veto refused this step's notice: record it so it is not offered again."""
    db.execute("UPDATE reminders SET sent = 3 WHERE key = ? AND step = ? AND sent = 2", (key, step))
    db.commit()


def release(db, key, step):
    db.execute("DELETE FROM reminders WHERE key = ? AND step = ? AND sent = 2", (key, step))
    db.commit()


def recorded(db, key):
    """{step: True if it was announced, False if it was skipped} for one key. Steps that are only
    claimed are left out."""
    return {r["step"]: r["sent"] == 1 for r in
            db.execute("SELECT step, sent FROM reminders WHERE key = ? AND sent IN (0, 1, 3)", (key,))}


def set_status(db, key, status):
    if status not in ("open", "paid", "dismissed"):
        raise ValueError(status)
    db.execute("UPDATE obligations SET status = ? WHERE key = ?", (status, key))
    db.commit()


def overrides(db):
    return {r["sender"]: r["bucket"] for r in db.execute("SELECT sender, bucket FROM overrides")}


def set_override(db, sender, bucket):
    db.execute("INSERT INTO overrides (sender, bucket) VALUES (?, ?)"
               " ON CONFLICT(sender) DO UPDATE SET bucket = excluded.bucket", (sender.lower(), bucket))
    db.commit()
