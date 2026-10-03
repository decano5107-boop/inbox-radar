"""Reading numbers and dates out of text, in a format the caller declares.

Nothing here guesses a format. An amount is parsed in the rule's number format or not at all,
and a numeric date is read in the rule's day/month order or not at all, because a wrong reading
of `1.234` or `03/06` produces a confident, wrong reminder.
"""
import datetime as dt
import re
import unicodedata
from decimal import Decimal, InvalidOperation

NUMBER_FORMATS = {
    # thousands separator, decimal separator
    "1,234.56": (",", "."),
    "1.234,56": (".", ","),
}
CURRENCY_MARKS = {"$": "USD", "US$": "USD", "USD": "USD", "€": "EUR", "EUR": "EUR"}

MONTHS = {
    "jan": 1, "january": 1, "ene": 1, "enero": 1,
    "feb": 2, "february": 2, "febrero": 2,
    "mar": 3, "march": 3, "marzo": 3,
    "apr": 4, "april": 4, "abr": 4, "abril": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "june": 6, "junio": 6,
    "jul": 7, "july": 7, "julio": 7,
    "aug": 8, "august": 8, "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "september": 9, "septiembre": 9, "setiembre": 9,
    "oct": 10, "october": 10, "octubre": 10,
    "nov": 11, "november": 11, "noviembre": 11,
    "dec": 12, "december": 12, "dic": 12, "diciembre": 12,
}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
RX_DATE_DAY_FIRST = re.compile(rf"\b([0-9]{{1,2}})(?:\s+de)?\s+({_MONTH})\.?(?:\s+de|,)?\s+([0-9]{{4}})\b")
RX_DATE_MONTH_FIRST = re.compile(rf"\b({_MONTH})\.?\s+([0-9]{{1,2}}),?\s+([0-9]{{4}})\b")
RX_DATE_NUMERIC = re.compile(r"(?<![0-9/.-])([0-9]{1,2})[/.-]([0-9]{1,2})[/.-]([0-9]{4})(?![0-9/.-])")


def fold(text):
    """Lower case without accents, one output character per input character, so a position
    found in the folded text is the same position in the original."""
    out = []
    for ch in text:
        base = unicodedata.normalize("NFKD", ch)[:1] or ch
        low = base.lower()
        out.append(low if len(low) == 1 else ch)
    return "".join(out)


def _number_pattern(fmt):
    thousands, decimal = NUMBER_FORMATS[fmt]
    t, d = re.escape(thousands), re.escape(decimal)
    # ASCII digits only (a digit from another script can look like a different one), and at most
    # nine integer digits: no bill is a billion, and a card number written as money is not a bill.
    return re.compile(rf"(?<![0-9{t}{d}/])([0-9]{{1,3}}(?:{t}[0-9]{{3}}){{1,2}}|[0-9]{{1,9}})(?:{d}([0-9]{{2}}))?"
                      rf"(?![0-9/]|{t}[0-9]|{d}[0-9])", re.ASCII)


_PATTERNS = {fmt: _number_pattern(fmt) for fmt in NUMBER_FORMATS}
RX_MARK = re.compile(r"(US\$|USD|EUR|\$|€)")


RX_MARK_BEFORE = re.compile(r"(US\$|USD|EUR|(?<![A-Za-z])\$|€) ?$")   # not CA$, MX$, R$
RX_MARK_AFTER = re.compile(r"^ ?(€|EUR|USD)(?![A-Za-z])(?! ?[-−–(]?[\d$€])")   # not a prefix of the next figure
RX_NEGATIVE = re.compile(r"[-−–—(]\s*$")
RX_CREDIT_AFTER = re.compile(r"^\s*(?:€|EUR|USD)?\s*(?:CR\b|credit\b|a favor\b|abono\b)", re.I)


def amounts(text, fmt):
    """[(start, Decimal, currency)] for every figure in `text` written as money: a currency mark
    directly before it (`$1,234.56`, `USD 54.32`) or a code or euro sign directly after it
    (`12,34 €`). Bare numbers are not amounts — card suffixes, reference numbers and years are
    bare numbers — and negative figures (credits, refunds) are not amounts owed."""
    found = []
    for m in _PATTERNS[fmt].finditer(text):
        before = text[max(0, m.start() - 6):m.start()]
        after = text[m.end():m.end() + 12]
        mb, ma = RX_MARK_BEFORE.search(before), RX_MARK_AFTER.search(after)
        if mb:
            mark, lead = mb.group(1), before[:mb.start()]
        elif ma:
            mark, lead = ma.group(1), before
        else:
            continue
        if RX_NEGATIVE.search(lead) or RX_CREDIT_AFTER.search(text[m.end():m.end() + 16]):
            continue
        whole = m.group(1).replace(NUMBER_FORMATS[fmt][0], "")
        try:
            value = Decimal(whole + "." + (m.group(2) or "00"))
        except InvalidOperation:
            continue
        found.append((m.start(), value, CURRENCY_MARKS[mark]))
    return found


def _date(year, month, day):
    try:
        return dt.date(int(year), int(month), int(day))
    except ValueError:
        return None


def dates(text, order):
    """[(start, date)] for every valid date in `text`. `order` is "DMY" or "MDY" and applies only
    to all-numeric dates; dates with a month name are unambiguous."""
    folded = fold(text)
    found = []
    for m in RX_DATE_DAY_FIRST.finditer(folded):
        d = _date(m.group(3), MONTHS[m.group(2)], m.group(1))
        if d:
            found.append((m.start(), d))
    for m in RX_DATE_MONTH_FIRST.finditer(folded):
        d = _date(m.group(3), MONTHS[m.group(1)], m.group(2))
        if d:
            found.append((m.start(), d))
    for m in RX_DATE_NUMERIC.finditer(folded):
        a, b, year = m.groups()
        d = _date(year, b, a) if order == "DMY" else _date(year, a, b)
        if d:
            found.append((m.start(), d))
    return sorted(found)


RX_LAST_FOUR = re.compile(
    r"\b(?:card|account|tarjeta|cuenta)\b[^.\n]{0,24}?"
    r"\b(?:ending in|ending|terminada en|acabada en|termina en)\s*[*x•.]*\s*(\d{4})(?![\d/.-]\d|\d)")


def last_four(text):
    m = RX_LAST_FOUR.search(fold(text))
    return m.group(1) if m else None


def money(amount, currency):
    """`$1,234.56` or `1.234,56 €` — the way each currency is usually written."""
    whole, cents = f"{amount:,.2f}".split(".")
    if currency == "EUR":
        return f"{whole.replace(',', '.')},{cents} €"
    if currency == "USD":
        return f"${whole}.{cents}"
    return f"{whole}.{cents} {currency}"
