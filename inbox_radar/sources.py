"""Where messages come from: a local fixture mailbox, or Gmail through its API.

A source yields `Message` objects. It fetches bodies and attachments only for senders that have
a rule; for everyone else it returns headers and an empty body, which is all triage needs. That
keeps the text of passcodes, personal correspondence and newsletters out of the process
altogether, not just out of its output.
"""
import base64
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import tempfile

from .model import Message

PDF_PAGES = 8
PDF_TIMEOUT = 30
MAX_ATTACHMENT = 15 * 1024 * 1024
MAX_PDF_TEXT = 256 * 1024          # bytes of extracted text kept; a bill is a page or two
MAX_BODY = 200_000                 # characters of a body kept
MAX_HTML = 400_000                 # characters of HTML considered
MAX_DEPTH = 30                     # MIME nesting levels walked


def pdf_text(data):
    """Text of the first pages of a PDF, via `pdftotext` (poppler). '' when it is not installed,
    the file is too large, or extraction fails: the message is then judged on its body alone."""
    if not shutil.which("pdftotext") or len(data) > MAX_ATTACHMENT:
        return ""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        try:
            proc = subprocess.Popen(["pdftotext", "-q", "-l", str(PDF_PAGES), f.name, "-"],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return ""
        try:
            out = proc.stdout.read(MAX_PDF_TEXT)      # a small file can expand to gigabytes of text
        finally:
            proc.kill()
            proc.stdout.close()
            try:
                proc.wait(timeout=PDF_TIMEOUT)
            except subprocess.SubprocessError:
                pass
        return out.decode("utf-8", "replace")


def _drop_blocks(raw, tag):
    """Remove <tag ...>...</tag> blocks in one linear pass; an unclosed block runs to the end."""
    lower, out, pos = raw.lower(), [], 0
    while True:
        start = lower.find(f"<{tag}", pos)
        if start == -1:
            out.append(raw[pos:])
            return "".join(out)
        out.append(raw[pos:start])
        end = lower.find(f"</{tag}", start)
        if end == -1:
            return "".join(out)
        close = lower.find(">", end)
        pos = len(raw) if close == -1 else close + 1


def html_to_text(raw):
    raw = raw[:MAX_HTML]
    for tag in ("script", "style"):
        raw = _drop_blocks(raw, tag)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", raw)
    return html.unescape(re.sub(r"<[^<>]*>", " ", raw))[:MAX_BODY]


class FixtureSource:
    """Reads the JSONL mailbox written by examples/mailbox/generate.py (or your own export in the
    same shape: id, from, subject, date, headers, body, attachments[{filename, path}])."""

    def __init__(self, folder):
        self.folder = os.path.realpath(folder)
        self.skipped = 0                   # malformed records, counted and never echoed

    def ids_from(self, sender):
        """Ids of every message from `sender` (an address), for drafting a plan."""
        sender = sender.lower()
        return [m.id for m in self.messages() if m.address == sender]

    def messages(self, ruled_senders=()):
        ruled = {s.lower() for s in ruled_senders}
        with open(os.path.join(self.folder, "messages.jsonl"), encoding="utf-8", errors="replace") as f:
            for line in f:
                message = self._parse(line, ruled)
                if message is None:
                    self.skipped += 1
                    continue
                yield message

    def _parse(self, line, ruled):
        """One record, or None when it is malformed. Nothing from a bad record is printed."""
        try:
            raw = json.loads(line)
            headers = raw.get("headers", {})
            message = Message(str(raw["id"]), str(raw["from"]), "",
                              dt.datetime.fromisoformat(str(raw["date"])),
                              {str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {})
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            return None
        if message.address not in ruled:
            return message                                  # headers only: no subject, no body
        message.subject = str(raw.get("subject", ""))[:500]
        message.body = str(raw.get("body", ""))[:MAX_BODY]
        texts = []
        attachments = raw.get("attachments", [])
        for attachment in attachments if isinstance(attachments, list) else []:
            if not (isinstance(attachment, dict) and str(attachment.get("filename", "")).lower().endswith(".pdf")):
                continue
            path = os.path.realpath(os.path.join(self.folder, str(attachment.get("path", ""))))
            if os.path.commonpath([path, self.folder]) != self.folder or not os.path.isfile(path):
                continue                                    # never outside the mailbox, symlinks included
            with open(path, "rb") as pdf:
                texts.append(pdf_text(pdf.read(MAX_ATTACHMENT + 1)))
        message.attachment_text = "\n".join(texts)
        return message


class GmailSource:
    """Gmail through the official API, read-only scope. Requires `google-api-python-client` and
    `google-auth`; see the README for creating the token. `service` can be any object with the
    same methods, which is how the tests run without a network."""

    SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

    def __init__(self, service, days=90, page_limit=20):
        self.service, self.days, self.page_limit = service, days, page_limit

    @classmethod
    def from_token(cls, token_path, **kwargs):
        from google.auth.transport.requests import Request          # optional dependency
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_authorized_user_file(token_path, [cls.SCOPE])
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
        return cls(build("gmail", "v1", credentials=creds, cache_discovery=False), **kwargs)

    def _ids(self, query):
        ids, token = [], None
        for _ in range(self.page_limit):
            page = self.service.users().messages().list(userId="me", q=query, pageToken=token).execute()
            ids += [m["id"] for m in page.get("messages", [])]
            token = page.get("nextPageToken")
            if not token:
                break
        return ids

    def _walk(self, part, plain, htmls, pdfs, depth=0):
        if depth > MAX_DEPTH or not isinstance(part, dict):
            return
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        if part.get("filename", "").lower().endswith(".pdf") and body.get("attachmentId"):
            pdfs.append(body["attachmentId"])
        elif mime == "text/plain" and body.get("data"):
            plain.append(_b64(body["data"]))
        elif mime == "text/html" and body.get("data"):
            htmls.append(_b64(body["data"]))
        for child in part.get("parts", []) or []:
            self._walk(child, plain, htmls, pdfs, depth + 1)

    TRIAGE_HEADERS = ["From", "List-Unsubscribe", "Precedence"]

    def _message(self, msg_id, full):
        if full:
            raw = self.service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        else:                       # headers triage needs, and nothing else: not even the subject
            raw = self.service.users().messages().get(userId="me", id=msg_id, format="metadata",
                                                      metadataHeaders=self.TRIAGE_HEADERS).execute()
        payload = raw.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        received = dt.datetime.fromtimestamp(int(raw.get("internalDate", "0")) / 1000)
        message = Message(raw["id"], headers.get("From", ""), headers.get("Subject", "") if full else "",
                          received, {k: v for k, v in headers.items() if k in ("List-Unsubscribe", "Precedence")})
        if full:
            plain, htmls, pdfs = [], [], []
            self._walk(payload, plain, htmls, pdfs)
            body = "\n".join(plain) or "\n".join(html_to_text(h) for h in htmls)
            message.body = body[:MAX_BODY]
            texts = []
            for attachment_id in pdfs:
                att = self.service.users().messages().attachments().get(
                    userId="me", messageId=msg_id, id=attachment_id).execute()
                if att.get("size", 0) <= MAX_ATTACHMENT:
                    texts.append(pdf_text(base64.urlsafe_b64decode(att.get("data", "") + "==")))
            message.attachment_text = "\n".join(texts)
        return message

    def ids_from(self, sender, limit_pages=50):
        """Ids of messages from `sender`, for drafting a plan. Metadata only; checks the exact
        address, since Gmail's from: also matches display names."""
        ids = self._ids(f"from:{sender}")[:limit_pages * 100]
        return [i for i in ids if self._message(i, full=False).address == sender.lower()]

    def messages(self, ruled_senders=(), recent_headers=True):
        """Full messages from ruled senders in the last `days`; headers only for everything else
        from the last week, for triage."""
        ruled = sorted({s.lower() for s in ruled_senders})
        if ruled:
            query = f"from:({' OR '.join(ruled)}) newer_than:{self.days}d"
            for msg_id in self._ids(query):
                head = self._message(msg_id, full=False)
                # Gmail's from: also matches display names; only an exact ruled address gets a body.
                yield self._message(msg_id, full=True) if head.address in ruled else head
        if recent_headers:
            seen_query = "newer_than:7d" + "".join(f" -from:{s}" for s in ruled)
            for msg_id in self._ids(seen_query):
                yield self._message(msg_id, full=False)


def _b64(data):
    return base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
