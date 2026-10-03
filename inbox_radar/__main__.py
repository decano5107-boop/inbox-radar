"""Command line: python3 -m inbox_radar <command>. Every command prints fields and counts, never
message text."""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys

from . import __version__, notify, pipeline, plans, radar, rules as R, sources, store
from .text import money


def _notifier(spec):
    if spec == "stdout":
        return notify.StdoutNotifier()
    if spec.startswith("jsonl:"):
        return notify.JsonlNotifier(os.path.expanduser(spec[6:]))
    raise SystemExit(f"unknown notifier '{spec}': use 'stdout' or 'jsonl:PATH'")


def _rules(path):
    rules, problems = R.load(path)
    for p in problems:
        print(f"rules: {p}", file=sys.stderr)
    return rules


def short_id(key):
    return hashlib.sha256(key.encode()).hexdigest()[:8]


def cmd_scan(args, db):
    rules = _rules(args.rules)
    if args.gmail:
        source = _gmail_source(args.gmail, days=args.days)
    else:
        source = sources.FixtureSource(args.mailbox)
    result = pipeline.scan(source, rules, db)
    if getattr(source, "skipped", 0):
        print(f"{source.skipped} malformed record(s) skipped", file=sys.stderr)
    counts = result.counts()
    print(f"{len(result.buckets)} messages: " + ", ".join(f"{counts.get(b, 0)} {b}" for b in ("urgent", "important", "ignore")))
    print(f"{result.obligations - result.review} bills read, {result.movements} card charges, "
          f"{result.review} need review")
    return 0


def cmd_remind(args, db):
    rules = _rules(args.rules)
    notices = radar.run(db, args.today, rules.thresholds)
    log = lambda event: print(json.dumps({**event, "key": short_id(event["key"])}), file=sys.stderr)  # noqa: E731
    sent, blocked, failed = notify.deliver(db, notices, _notifier(args.notifier), log)
    if blocked or failed:
        print(f"{blocked} blocked by the veto, {failed} not delivered (will retry)", file=sys.stderr)
    return 1 if failed else 0


def _line(ob, today):
    if ob.due is None:
        when = "no due date found"
    else:
        days = (ob.due - today).days
        span = abs(days)
        unit = "day" if span == 1 else "days"
        when = f"due {ob.due:%a %d %b %Y}" + (f" (in {span} {unit})" if days >= 0 else f" ({span} {unit} overdue)")
    amount = money(ob.amount, ob.currency) if ob.amount is not None else "amount not found"
    return f"  {short_id(ob.key)}  {ob.issuer} — {ob.concept}: {amount}, {when}"


def cmd_status(args, db):
    open_items = store.open_obligations(db)
    current = [o for o in open_items if o not in radar.long_overdue(db, args.today)]
    old = radar.long_overdue(db, args.today)
    review = store.needs_review(db)
    blocked = db.execute("SELECT COUNT(*) FROM reminders WHERE sent = 3").fetchone()[0]
    closed = store.all_obligations(db, status=("paid", "dismissed"))
    print(f"Open ({len(current)}):")
    for ob in current:
        print(_line(ob, args.today))
    if old:
        print(f"Overdue more than {radar.OVERDUE_WINDOW} days, no longer announced ({len(old)}):")
        for ob in old:
            print(_line(ob, args.today))
    if review:
        print(f"Need review — a bill from a known issuer that could not be read ({len(review)}):")
        for ob in review:
            print(_line(ob, args.today))
    if closed:
        print(f"Paid or dismissed ({len(closed)}): reopen one with `mark ID open`")
        for ob in closed:
            print(_line(ob, args.today))
    if blocked:
        print(f"{blocked} notice(s) were blocked by the veto; see the remind log.")
    return 0


def _find(db, ident):
    for ob in store.all_obligations(db):
        if short_id(ob.key) == ident:
            return ob
    raise SystemExit(f"no obligation with id {ident}; see `status`")


def cmd_mark(args, db):
    ob = _find(db, args.id)
    store.set_status(db, ob.key, args.status)
    print(f"{ob.issuer} — {ob.concept}: marked {args.status}")
    return 0


def cmd_override(args, db):
    store.set_override(db, args.sender, args.bucket)
    print(f"mail from {args.sender.lower()} will be '{args.bucket}'")
    return 0


def _load_plan(path):
    with open(path, encoding="utf-8") as f:
        return plans.make_plan(json.load(f))


def _draft(args):
    if not args.sender or not (args.mailbox or args.gmail):
        raise SystemExit("draft needs --sender and --mailbox or --gmail")
    source = (_gmail_source(args.gmail) if args.gmail else sources.FixtureSource(args.mailbox))
    ids = source.ids_from(args.sender)
    if not ids:
        raise SystemExit(f"no messages from {args.sender}")
    action = {"verb": "label", "label": args.label} if args.label else {"verb": "archive"}
    plan = plans.make_plan([{**action, "sender": args.sender, "message_ids": ids}])
    fd = os.open(args.file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=1)
    print(f"wrote {args.file}: {len(ids)} messages from {args.sender}; review it with `plan show`")


def cmd_plan(args, db):
    try:
        if args.action == "draft":
            _draft(args)
            return 0
        plan = _load_plan(args.file)
        if args.action == "show":
            print(plans.summary(plan))
        elif args.action == "propose":
            if not args.notifier:
                raise SystemExit("propose needs --notifier: the code must reach you, not whoever runs this")
            digest = plans.propose(plan, args.state, _notifier(args.notifier))
            print(f"approval request sent through the notifier for plan {digest[:12]}")
        else:
            if not args.code:
                raise SystemExit("approve needs --code")
            if args.gmail_modify:
                executor = plans.GmailExecutor(_gmail_modify(args.gmail_modify))
            else:
                executor = plans.LogExecutor()
            done = plans.execute(plan, args.code, args.state, executor)
            print(", ".join(f"{verb}: {n} messages" for verb, n in done.items()) +
                  ("" if args.gmail_modify else " (dry run: pass --gmail-modify TOKEN to apply)"))
    except plans.PlanError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"cannot read the plan: {exc}", file=sys.stderr)
        return 2
    return 0


GOOGLE_MISSING = ("Gmail needs the Google client libraries: "
                  "pip install google-api-python-client google-auth (in a virtual environment)")


def _gmail_source(token, **kwargs):
    try:
        return sources.GmailSource.from_token(os.path.expanduser(token), **kwargs)
    except ImportError:
        raise SystemExit(GOOGLE_MISSING) from None


def _gmail_modify(token):
    try:
        from google.auth.transport.requests import Request           # optional dependency
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        raise SystemExit(GOOGLE_MISSING) from None
    creds = Credentials.from_authorized_user_file(os.path.expanduser(token),
                                                  ["https://www.googleapis.com/auth/gmail.modify"])
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS, help="SQLite file (default: out/radar.db)")
    common.add_argument("--today", type=dt.date.fromisoformat, default=argparse.SUPPRESS,
                        help="pretend today is this date (YYYY-MM-DD)")
    p = argparse.ArgumentParser(prog="python3 -m inbox_radar", parents=[common],
                                description="Deterministic bill tracking for a personal inbox.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    real_add = sub.add_parser
    sub.add_parser = lambda *a, **k: real_add(*a, parents=[common], **k)   # --db/--today after the command too

    s = sub.add_parser("scan", help="read the mailbox and store bills and charges")
    s.add_argument("--rules", required=True)
    src = s.add_mutually_exclusive_group(required=True)
    src.add_argument("--mailbox", help="a folder with messages.jsonl (see examples/mailbox)")
    src.add_argument("--gmail", metavar="TOKEN", help="authorized-user token file, scope gmail.readonly")
    s.add_argument("--days", type=int, default=90)
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("remind", help="send today's reminders, if any")
    s.add_argument("--rules", required=True)
    s.add_argument("--notifier", default="stdout", help="'stdout' or 'jsonl:PATH'")
    s.set_defaults(func=cmd_remind)

    sub.add_parser("status", help="list open bills, old overdue ones and ones to review").set_defaults(func=cmd_status)

    s = sub.add_parser("mark", help="mark a bill paid or dismissed (id from `status`)")
    s.add_argument("id")
    s.add_argument("status", choices=("paid", "dismissed", "open"))
    s.set_defaults(func=cmd_mark)

    s = sub.add_parser("override", help="always put mail from an address or domain in a bucket")
    s.add_argument("sender")
    s.add_argument("bucket", choices=("urgent", "important", "ignore"))
    s.set_defaults(func=cmd_override)

    s = sub.add_parser("plan", help="draft, show, propose or approve a bulk label/archive plan")
    s.add_argument("action", choices=("draft", "show", "propose", "approve"))
    s.add_argument("file", help="the plan: a JSON list of actions (draft writes it)")
    s.add_argument("--state", default="out/plans")
    s.add_argument("--notifier", help="where the approval code goes: 'stdout' or 'jsonl:PATH' (required for propose)")
    s.add_argument("--code")
    s.add_argument("--sender", help="draft: the address whose mail the plan covers")
    s.add_argument("--label", help="draft: label instead of archive")
    draft_src = s.add_mutually_exclusive_group()
    draft_src.add_argument("--mailbox", help="draft: a fixture mailbox folder")
    draft_src.add_argument("--gmail", metavar="TOKEN", help="draft: a read-only token")
    s.add_argument("--gmail-modify", metavar="TOKEN", help="token with scope gmail.modify; omit for a dry run")
    s.set_defaults(func=cmd_plan)

    args = p.parse_args(argv)
    args.db = getattr(args, "db", "out/radar.db")          # accepted before or after the command
    args.today = getattr(args, "today", dt.date.today())
    db = store.connect(args.db)
    try:
        return args.func(args, db)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
