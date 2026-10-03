"""Three buckets — urgent, important, ignore — from sender, headers and what extraction found.

No model and no subject-line guessing. When the evidence is thin the message goes to the
quieter bucket: a false alarm costs the reader's trust in every later alert, a missed newsletter
costs nothing.
"""
import re

from .model import Movement, Obligation
from .text import fold

URGENT, IMPORTANT, IGNORE = "urgent", "important", "ignore"

# Local parts that name a function, not a person.
ROLE_WORDS = {
    "accounts", "admin", "alerts", "avisos", "baja", "billing", "boletin", "bookings", "bounce",
    "care", "clientes", "cobros", "contact", "customer", "customers", "orders", "pedidos",
    "reservas", "reservations", "sales", "shop", "store", "ventas", "cuentas", "deals", "donotreply", "events", "extractos", "facturas", "hello",
    "help", "info", "mailer", "marketing", "membership", "news", "newsletter", "no", "noreply",
    "notices", "notifications", "noticias", "offers", "ofertas", "promos", "records", "reply",
    "results", "security", "seguridad", "service", "statements", "support", "team", "unsubscribe",
    "updates",
}


def _listed(message, entries):
    return message.address in entries or message.domain in entries


def is_person(message):
    """A sender that looks like an individual: a non-role address, a first-and-last name, and an
    address that contains part of that name. It is a heuristic, not a verdict: spam that dresses
    up as a person passes it, which is what the mail provider's spam filter is for."""
    local = message.address.partition("@")[0]
    if set(re.split(r"[._+\-]+", local)) & ROLE_WORDS:
        return False
    names = [n for n in re.findall(r"[^\W\d_]+", fold(message.display_name)) if len(n) >= 2]
    if len(names) < 2:
        return False
    address = fold(message.address)
    return any(n in address for n in names)


def is_bulk(message):
    headers = {k.lower(): str(v).lower() for k, v in message.headers.items()}
    return "list-unsubscribe" in headers or headers.get("precedence") in ("bulk", "list", "junk")


def classify(message, rules, found=None, overrides=None):
    """`found` is what extraction produced for this message (an Obligation, a Movement or None).
    `overrides` maps an address or a domain to the bucket the user chose for it."""
    overrides = overrides or {}
    for key in (message.address, message.domain):
        if key in overrides:
            return overrides[key]
    if _listed(message, rules.ignore):
        return IGNORE
    if isinstance(found, Movement):
        return URGENT if found.amount >= rules.thresholds.get(found.currency, float("inf")) else IGNORE
    if isinstance(found, Obligation):
        rule = rules.for_sender(message.address)
        if rule and rule.no_date_means_now and found.amount is not None:
            return URGENT
        return IMPORTANT
    if _listed(message, rules.important):
        return IMPORTANT
    if is_bulk(message):
        return IGNORE
    return IMPORTANT if is_person(message) else IGNORE
