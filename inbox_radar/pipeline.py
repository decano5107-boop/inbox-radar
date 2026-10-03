"""One pass over a mailbox: extract, triage, store. Returns counts and ids, never text."""
from collections import Counter
from dataclasses import dataclass, field

from . import extract, store, triage


@dataclass
class Scan:
    buckets: dict = field(default_factory=dict)          # message id -> bucket
    obligations: int = 0
    movements: int = 0
    review: int = 0

    def counts(self):
        return dict(Counter(self.buckets.values()))


def scan(source, rules, db):
    result = Scan()
    prefs = store.overrides(db)
    for message in source.messages(rules.all_senders()):
        rule = rules.for_sender(message.address)
        found = extract.extract(message, rule) if rule else None
        result.buckets[message.id] = triage.classify(message, rules, found, prefs)
        if found is None:
            continue
        store.save(db, found)
        if hasattr(found, "due"):
            result.obligations += 1
            result.review += found.confidence != "high"
        else:
            result.movements += 1
    return result
