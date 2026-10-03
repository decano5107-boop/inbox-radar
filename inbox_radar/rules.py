"""Issuer rules and triage lists, loaded from one JSON file and validated on the way in.

A rule that does not validate is dropped with a message naming the field, never half-applied:
a rule with a typo in its number format would otherwise read every amount wrong.
"""
import json
from dataclasses import dataclass, field

from .text import CURRENCY_MARKS, NUMBER_FORMATS, fold

CURRENCIES = set(CURRENCY_MARKS.values())


@dataclass(frozen=True)
class Rule:
    issuer: str
    senders: tuple
    concept: str
    currency: str
    number_format: str = "1,234.56"
    date_order: str = "MDY"
    amount_anchors: tuple = ()
    amount_confirm: tuple = ()
    date_anchors: tuple = ()
    date_confirm: tuple = ()
    signal: tuple = ()
    no_date_means_now: bool = False
    movement: bool = False


@dataclass
class Rules:
    issuers: list = field(default_factory=list)
    important: tuple = ()          # addresses or domains whose mail is always worth a look
    ignore: tuple = ()             # addresses or domains whose mail is never worth a look
    thresholds: dict = field(default_factory=lambda: {"USD": 100, "EUR": 100})

    def for_sender(self, address):
        for rule in self.issuers:
            if address in rule.senders:
                return rule
        return None

    def all_senders(self):
        return sorted({s for rule in self.issuers for s in rule.senders})


_LISTS = ("senders", "amount_anchors", "amount_confirm", "date_anchors", "date_confirm", "signal")
_STRINGS = ("issuer", "concept", "currency", "number_format", "date_order")
_BOOLS = ("no_date_means_now", "movement")


def _rule(raw, index, problems):
    where = f"issuers[{index}]"
    if not isinstance(raw, dict):
        problems.append(f"{where}: not an object")
        return None
    unknown = set(raw) - set(_LISTS) - set(_STRINGS) - set(_BOOLS)
    if unknown:
        problems.append(f"{where}: unknown keys {sorted(unknown)}")
        return None
    for key in ("issuer", "senders", "concept", "currency"):
        if not raw.get(key):
            problems.append(f"{where}: '{key}' is required")
            return None
    values = {}
    for key in _STRINGS:
        if key in raw and not isinstance(raw[key], str):
            problems.append(f"{where}.{key}: should be a string")
            return None
        if key in raw:
            values[key] = raw[key]
    for key in _LISTS:
        if key in raw:
            if not (isinstance(raw[key], list) and all(isinstance(v, str) and v.strip() for v in raw[key])):
                problems.append(f"{where}.{key}: should be a list of non-empty strings")
                return None
            values[key] = tuple(v.strip().lower() if key == "senders" else fold(v.strip())
                                for v in raw[key])
    for key in _BOOLS:
        if key in raw:
            if not isinstance(raw[key], bool):
                problems.append(f"{where}.{key}: should be true or false")
                return None
            values[key] = raw[key]
    if values["currency"] not in CURRENCIES:
        problems.append(f"{where}.currency: one of {sorted(CURRENCIES)} (amounts are read by their currency mark)")
        return None
    if values.get("number_format", "1,234.56") not in NUMBER_FORMATS:
        problems.append(f"{where}.number_format: one of {sorted(NUMBER_FORMATS)}")
        return None
    if values.get("date_order", "MDY") not in ("DMY", "MDY"):
        problems.append(f"{where}.date_order: 'DMY' or 'MDY'")
        return None
    if not values.get("movement") and not values.get("amount_anchors"):
        problems.append(f"{where}: a bill rule needs amount_anchors")
        return None
    return Rule(**values)


def parse(data):
    """Return (Rules, problems). Bad entries are skipped and named in `problems`."""
    problems = []
    if not isinstance(data, dict):
        return Rules(), ["the rules file should be a JSON object"]
    issuers = []
    for i, raw in enumerate(data.get("issuers", [])):
        rule = _rule(raw, i, problems)
        if rule:
            issuers.append(rule)
    seen = {}
    for rule in issuers:
        for sender in rule.senders:
            if sender in seen:
                problems.append(f"sender {sender} appears in two rules ({seen[sender]}, {rule.issuer})")
            seen[sender] = rule.issuer
    triage = data.get("triage", {})
    lists = {}
    for key in ("important", "ignore"):
        value = triage.get(key, []) if isinstance(triage, dict) else []
        if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            problems.append(f"triage.{key}: should be a list of strings")
            value = []
        lists[key] = tuple(v.lower() for v in value)
    thresholds = {"USD": 100, "EUR": 100}
    raw_thresholds = data.get("thresholds", {})
    if isinstance(raw_thresholds, dict):
        for currency, value in raw_thresholds.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                thresholds[currency] = value
            else:
                problems.append(f"thresholds.{currency}: should be a positive number")
    return Rules(issuers, lists["important"], lists["ignore"], thresholds), problems


def load(path):
    with open(path, encoding="utf-8") as f:
        return parse(json.load(f))
