"""Delivery: every notice passes the veto, then goes to a notifier, then is confirmed or released.

A notifier is any object with `send(text) -> bool`. Two ship with the package: standard output
and an append-only JSONL file. Sending to a phone is the same interface with an HTTP call inside.
"""
import datetime as dt
import json
import os
import sys

from . import radar, store, veto


class StdoutNotifier:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout

    def send(self, text):
        print(text, file=self.stream)
        return True


class JsonlNotifier:
    """Appends {"ts", "text"} lines to a private file (0600)."""

    def __init__(self, path):
        self.path = path

    def send(self, text):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), mode=0o700, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "text": text},
                               ensure_ascii=False) + "\n")
        return True


def deliver(db, notices, notifier, log=None):
    """Send each notice that passes the veto. Returns (sent, blocked, failed) counts.

    Blocked: the veto found something; the step is recorded as blocked (never retried) and the
    log gets the notice's key, step and the categories found — never its text. Failed: the
    notifier said no or raised; the claim is released so the next run tries again.
    """
    sent = blocked = failed = 0
    for notice in notices:
        findings = veto.audit_notice(notice)
        if findings:
            store.block(db, notice.key, notice.step)
            blocked += 1
            if log:
                log({"event": "blocked", "key": notice.key, "step": notice.step, "found": findings})
            continue
        try:
            ok = notifier.send(notice.text)
        except Exception as exc:                       # a broken notifier must not lose the reminder
            ok = False
            if log:
                log({"event": "notifier_error", "key": notice.key, "error": type(exc).__name__})
        if ok:
            radar.confirm_sent(db, notice)
            sent += 1
        else:
            radar.release(db, notice)
            failed += 1
    return sent, blocked, failed
