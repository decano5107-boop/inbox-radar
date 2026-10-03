"""From one message and its issuer rule to typed fields.

A value is taken only from the window that follows one of the rule's anchor phrases, and only if
the rule's confirm phrase (when it has one) appears in the lines just before that anchor — the
label of the section the anchor belongs to. Occurrences are tried in document order and the
first one that yields a value wins, so a lookalike block placed earlier in the message is
skipped: it can copy the anchor, but not the section it sits in.
"""
from . import text as T
from .model import Movement, Obligation

BEFORE = 80      # characters before an anchor in which a confirm phrase must appear
AFTER = 120      # characters after an anchor in which its value must appear
MAX_TEXT = 200_000   # characters of a message considered; bills are short


def _occurrences(folded, phrases):
    found = []
    for phrase in phrases:
        start = folded.find(phrase)
        while start != -1:
            found.append((start, phrase))
            start = folded.find(phrase, start + 1)
    return sorted(found)


def _anchored(source, folded, anchors, confirm, read):
    """The first value `read` finds after an anchor whose surroundings contain a confirm phrase."""
    for start, phrase in _occurrences(folded, anchors):
        end = start + len(phrase)
        if confirm and not any(c in folded[max(0, start - BEFORE):start] for c in confirm):
            continue
        values = read(_value_zone(source[end:end + AFTER]))
        if values:
            return values[0]
    return None


def _value_zone(segment):
    """Where an anchor's value may be: the rest of the anchor's line or, when the anchor ends its
    line (a label above its value), the next non-empty line. Never further: an anchor with no
    value next to it must not borrow the next figure in the message."""
    first, _, rest = segment.partition("\n")
    if first.strip(" \t:–—-"):
        return first
    for line in rest.split("\n"):
        if line.strip():
            return line
    return ""


def extract(message, rule):
    """An Obligation, a Movement, or None when the message is not a bill from this issuer."""
    source = f"{message.subject}\n{message.body}\n{message.attachment_text}"[:MAX_TEXT]
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    folded = T.fold(source)
    if rule.signal and not any(s in folded for s in rule.signal):
        return None

    def read_amount(segment):
        return [(value, currency) for _pos, value, currency in T.amounts(segment, rule.number_format)
                if currency == rule.currency]

    amount = _anchored(source, folded, rule.amount_anchors, rule.amount_confirm, read_amount)
    four = T.last_four(source)

    if rule.movement:
        if amount is not None:
            return Movement(message.id, rule.issuer, amount[0], rule.currency,
                            message.received.date(), four)
        if not _occurrences(folded, rule.amount_anchors):
            return None
        # The charge sentence is there but its figure is not readable in the declared format:
        # keep it for review instead of dropping a charge that may be large.
        return Obligation(message.id, rule.issuer, rule.concept, rule.currency, None, None, four, "low")

    due = None
    if rule.date_anchors:
        found = _anchored(source, folded, rule.date_anchors, rule.date_confirm,
                          lambda segment: [d for _pos, d in T.dates(segment, rule.date_order)])
        due = found
    inferred = False
    if due is None and rule.no_date_means_now and amount is not None:
        due, inferred = message.received.date(), True
    confidence = "high" if amount is not None and due is not None else "low"
    return Obligation(message.id, rule.issuer, rule.concept, rule.currency,
                      amount[0] if amount else None, due, four, confidence, inferred)
