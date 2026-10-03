"""Bulk mailbox changes, approved once with a one-time code bound to the plan's hash.

    plan  = make_plan([...actions...])        # validated, canonical
    propose(plan, state_dir, notifier)        # the code goes to the person, not to the caller
    execute(plan, code, state_dir, executor)  # redeems the code, re-hashes, then labels/archives

Only two verbs exist, `label` and `archive`. There is no delete and no spam report anywhere in
this module, so no code — stolen, guessed or approved — can make one happen.
"""
import datetime as dt
import hashlib
import json
import os
import re
import secrets
from collections import Counter

from . import veto

VERBS = ("label", "archive")
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"      # no 0/O, 1/I/L: read on a phone, typed back
CODE_LENGTH = 4
VALID_FOR = dt.timedelta(minutes=10)
MAX_FAILURES = 5                                  # wrong codes before every pending code is voided
RX_CODE = re.compile(rf"^[{ALPHABET}]{{{CODE_LENGTH}}}$")
RX_LABEL = re.compile(r"[\w][\w ./&-]{0,59}", re.UNICODE)
RX_SENDER = re.compile(r"(?:[A-Za-z0-9._%+\-]+@)?[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9\-]+)+")
# Gmail's system labels. Adding TRASH or SPAM is deleting or reporting under another name, and
# the others change state the user did not ask about; none may be a plan's label.
SYSTEM_LABELS = {"INBOX", "SPAM", "TRASH", "UNREAD", "STARRED", "IMPORTANT", "SENT", "DRAFT",
                 "CHAT", "CATEGORY_PERSONAL", "CATEGORY_SOCIAL", "CATEGORY_PROMOTIONS",
                 "CATEGORY_UPDATES", "CATEGORY_FORUMS"}


class PlanError(Exception):
    pass


def make_plan(actions):
    """Validate and normalise a list of {"verb", "sender", "message_ids", "label"?} dicts."""
    if not isinstance(actions, list) or not actions:
        raise PlanError("a plan is a non-empty list of actions")
    clean = []
    for i, a in enumerate(actions):
        if not isinstance(a, dict) or a.get("verb") not in VERBS:
            raise PlanError(f"action {i}: verb must be one of {VERBS}")
        ids = a.get("message_ids")
        if not (isinstance(ids, list) and ids and all(isinstance(m, str) and m for m in ids)):
            raise PlanError(f"action {i}: message_ids must be a non-empty list of ids")
        sender = a.get("sender", "")
        if not (isinstance(sender, str) and (sender == "" or (len(sender) <= 254 and RX_SENDER.fullmatch(sender)))):
            raise PlanError(f"action {i}: sender must be an email address or a domain")
        if not all(re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", m) for m in ids):
            raise PlanError(f"action {i}: message ids are letters, digits, - and _")
        if sender and set(veto.audit(sender)) - {"link"}:     # an address is a domain by design
            raise PlanError(f"action {i}: sender contains something the veto blocks")
        item = {"verb": a["verb"], "sender": sender.lower(), "message_ids": sorted(set(ids))}
        if a["verb"] == "label":
            label = a.get("label")
            if not (isinstance(label, str) and RX_LABEL.fullmatch(label)):
                raise PlanError(f"action {i}: label must be 1-60 letters, digits, spaces or ./&-_")
            if label.strip().upper().replace(" ", "_") in SYSTEM_LABELS:
                raise PlanError(f"action {i}: '{label}' is a system label")
            if veto.audit(label):                                 # labels reach the notifier too
                raise PlanError(f"action {i}: label contains something the veto blocks")
            item["label"] = label
        unknown = set(a) - {"verb", "sender", "message_ids", "label"}
        if unknown:
            raise PlanError(f"action {i}: unknown keys {sorted(unknown)}")
        clean.append(item)
    return sorted(clean, key=lambda x: (x["verb"], x.get("label", ""), x["sender"], x["message_ids"]))


def plan_hash(plan):
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def summary(plan):
    """What the person reads before approving: counts per verb, label and sender."""
    lines = []
    for a in plan:
        what = f"label as '{a['label']}'" if a["verb"] == "label" else "archive"
        lines.append(f"- {what}: {len(a['message_ids'])} messages from {a['sender'] or 'various senders'}")
    total = Counter(a["verb"] for a in plan)
    head = ", ".join(f"{n} {v} action{'s' if n > 1 else ''}" for v, n in sorted(total.items()))
    return f"Plan {plan_hash(plan)[:12]} ({head}):\n" + "\n".join(lines)


def _dirs(state_dir):
    paths = {name: os.path.join(state_dir, name) for name in ("pending", "used")}
    for path in paths.values():
        os.makedirs(path, mode=0o700, exist_ok=True)
    return paths


def _write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)


def issue_code(state_dir, digest, now=None):
    now = now or dt.datetime.now()
    pending = _dirs(state_dir)["pending"]
    for _ in range(20):
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        try:
            _write_private(os.path.join(pending, code), {"hash": digest,
                                                         "expires": (now + VALID_FOR).isoformat()})
            return code
        except FileExistsError:
            continue
    raise PlanError("could not issue a unique code; too many pending codes")


def propose(plan, state_dir, notifier, now=None):
    """Send the summary and a fresh code through the notifier. Returns the plan's hash only:
    whoever called this (a script, an agent) never sees the code."""
    text = summary(plan)
    checked = text.replace(plan_hash(plan)[:12], " · ")   # senders, labels and the hash were
    for a in plan:                                   # validated; audit everything else it says
        for value in (a["sender"], a.get("label")):
            if value:
                checked = checked.replace(value, " · ")
    if veto.audit(checked):
        raise PlanError("the plan summary contains something the veto blocks; not sent")
    digest = plan_hash(plan)
    code = issue_code(state_dir, digest, now)
    if not notifier.send(f"{text}\nTo approve, reply with code {code} (valid 10 minutes, one use)."):
        os.unlink(os.path.join(_dirs(state_dir)["pending"], code))
        raise PlanError("the notifier did not accept the approval request")
    return digest


def _fail(state_dir):
    """Count a wrong code; after MAX_FAILURES, void every pending code."""
    _dirs(state_dir)
    path = os.path.join(state_dir, "failures")
    try:
        with open(path, encoding="utf-8") as f:
            count = int(f.read().strip() or 0) + 1
    except (OSError, ValueError):
        count = 1
    if count >= MAX_FAILURES:
        pending = _dirs(state_dir)["pending"]
        for name in os.listdir(pending):
            os.unlink(os.path.join(pending, name))
        count = 0
    _write_count(path, count)


def _write_count(path, count):
    tmp = f"{path}.{secrets.token_hex(4)}"
    _write_private(tmp, count)
    os.replace(tmp, path)


def redeem(state_dir, code, plan, now=None):
    """Consume `code` for `plan`. Raises PlanError unless the code exists, is unexpired, was
    issued for exactly this plan, and this call is the one that consumed it."""
    now = now or dt.datetime.now()
    code = str(code).strip().upper()
    if not RX_CODE.match(code):                      # also what keeps `code` a safe file name
        _fail(state_dir)
        raise PlanError("not a valid code")
    dirs = _dirs(state_dir)
    used = os.path.join(dirs["used"], f"{code}.{secrets.token_hex(4)}")
    try:
        os.rename(os.path.join(dirs["pending"], code), used)   # atomic: only one caller wins
    except FileNotFoundError:
        _fail(state_dir)
        raise PlanError("unknown or already used code") from None
    with open(used, encoding="utf-8") as f:
        ticket = json.load(f)
    _write_count(os.path.join(state_dir, "failures"), 0)   # a correct code resets the count
    if dt.datetime.fromisoformat(ticket["expires"]) < now:
        raise PlanError("the code expired; propose the plan again")
    if ticket["hash"] != plan_hash(plan):
        raise PlanError("this plan is not the one the code was issued for")


def execute(plan, code, state_dir, executor, now=None):
    """Redeem the code for this exact plan, then apply it. Returns {verb: messages changed}."""
    plan = make_plan(plan)                           # re-validate whatever we were handed
    redeem(state_dir, code, plan, now)
    done = Counter()
    for a in plan:
        if a["verb"] == "label":
            executor.label(a["message_ids"], a["label"])
        else:
            executor.archive(a["message_ids"])
        done[a["verb"]] += len(a["message_ids"])
    return dict(done)


class LogExecutor:
    """Applies nothing; records what would change. For the demo and for dry runs."""

    def __init__(self):
        self.applied = []

    def label(self, ids, label):
        self.applied.append(("label", label, tuple(ids)))

    def archive(self, ids):
        self.applied.append(("archive", None, tuple(ids)))


class GmailExecutor:
    """Labels and archives through the Gmail API (scope gmail.modify, separate from the
    read-only token the scan uses). Archive removes the INBOX label; nothing is deleted."""

    def __init__(self, service):
        self.service = service

    def _label_id(self, name):
        if name.strip().upper().replace(" ", "_") in SYSTEM_LABELS:
            raise PlanError(f"'{name}' is a system label")
        labels = self.service.users().labels().list(userId="me").execute().get("labels", [])
        for label in labels:
            if label.get("type") == "user" and label["name"] == name:   # never a system label
                return label["id"]
        return self.service.users().labels().create(userId="me", body={"name": name}).execute()["id"]

    def _modify(self, ids, body):
        for start in range(0, len(ids), 1000):          # the API's batch limit
            self.service.users().messages().batchModify(
                userId="me", body={"ids": list(ids[start:start + 1000]), **body}).execute()

    def label(self, ids, label):
        self._modify(ids, {"addLabelIds": [self._label_id(label)]})

    def archive(self, ids):
        self._modify(ids, {"removeLabelIds": ["INBOX"]})
