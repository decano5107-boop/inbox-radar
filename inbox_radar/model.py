"""The values that move between stages. None of them has a field for message text."""
import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal

RX_ANGLE = re.compile(r"(?s)(.*)<([^<>@\s]+@[^<>@\s]+)>\s*")
RX_BARE = re.compile(r"[^<>@\s,;\"]+@[^<>@\s,;\"]+")


def parse_sender(header):
    """(display name, address) from a From header, strictly. The standard library's parseaddr
    reads some ambiguous headers differently across Python versions (CVE-2023-27043): on some,
    `"bills@bank.example" <someone@else.example>` yields the bank's address. Here an angle
    address is the address, a bare address must be the whole header, and anything else — two
    addresses, brackets in the name — gives no address at all."""
    text = str(header or "").strip()
    m = RX_ANGLE.fullmatch(text)
    if m:
        name = m.group(1).strip()
        unquoted = re.sub(r'"[^"]*"', "", name)          # a comma inside quotes is part of the name
        if any(c in unquoted for c in "<>,;") or name.count('"') % 2:
            return "", ""
        return name.strip('"').strip(), m.group(2).lower()
    if RX_BARE.fullmatch(text):
        return "", text.lower()
    return "", ""


@dataclass
class Message:
    """One email as a source delivers it. It lives in memory for one run and is never stored."""
    id: str
    sender: str
    subject: str
    received: dt.datetime
    headers: dict = field(default_factory=dict)
    body: str = ""
    attachment_text: str = ""

    @property
    def address(self):
        return parse_sender(self.sender)[1]

    @property
    def display_name(self):
        return parse_sender(self.sender)[0]

    @property
    def domain(self):
        return self.address.rpartition("@")[2]


@dataclass(frozen=True)
class Obligation:
    message_id: str
    issuer: str
    concept: str
    currency: str
    amount: Decimal = None
    due: dt.date = None
    last_four: str = None
    confidence: str = "high"          # "high" or "low"; low is listed for review, never announced
    inferred_due: bool = False        # due date is the send date of a past-due notice
    stored_key: str = None            # set by the store when it merged this with an earlier notice

    @property
    def key(self):
        """The same bill announced by two emails is one obligation (the store also merges repeated
        past-due notices, which have no date of their own)."""
        if self.stored_key:
            return self.stored_key
        return f"{self.issuer}|{self.concept}|{self.amount}|{self.currency}|{self.due}"


@dataclass(frozen=True)
class Movement:
    message_id: str
    issuer: str
    amount: Decimal
    currency: str
    received: dt.date
    last_four: str = None
