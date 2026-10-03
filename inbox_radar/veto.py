"""The last check before anything leaves: find what must never be sent, and block on any hit.

`audit(text)` returns the categories it found, or an empty list. It never rewrites text: a notice
with a hit is not sent at all, because a filter that edits what it does not understand produces
output nobody reviewed. Notices are built from typed fields, so a hit here means a rule or a
template let something through that it should not have — which is exactly what this catches.
"""
import re
import unicodedata

from .text import fold

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\ufeff\u00ad"))


def normalise(text):
    """What a reader would see: compatibility forms folded (full-width, ligatures, other digit
    scripts), invisible characters removed, non-breaking spaces made plain, and defanged links
    (`evil[.]com`, `evil。com`) put back together."""
    text = unicodedata.normalize("NFKC", text).translate(ZERO_WIDTH)
    text = "".join(str(unicodedata.digit(c)) if c.isdigit() and not c.isascii() else c for c in text)
    text = re.sub(r"[\u00a0\u2007\u202f\t]", " ", text)
    text = re.sub(r"[\u2010-\u2015\u2212\u2043\ufe58\ufe63\uff0d]", "-", text)   # every dash is a dash
    return re.sub(r"\[\.\]|\(\.\)|\{\.\}|\[dot\]|\(dot\)|[。｡]", ".", text, flags=re.I)


# Money exactly as the notice template writes it. Only this is exempt from the digit checks; a
# figure in any other shape ("$482913", "4821.55 EUR") is audited like any other digits.
RX_TEMPLATE_MONEY = re.compile(r"(?<![\w$])(?:US\$|\$)\d{1,3}(?:,\d{3}){0,2}\.\d{2}(?!\d|[.,]\d)"
                               r"|(?<![\d.,])\d{1,3}(?:\.\d{3}){0,2},\d{2} €")


_CODE_WORDS = re.compile(r"\b(?:code|codigo|codes|otp|pin|passcode|password|contrasena|clave|token|"
                         r"verification|verify|verificacion|security|seguridad|one[- ]time|"
                         r"un solo uso|2fa|mfa|login|sign[- ]in|acceso)\b")
# A run of 4+ digits, possibly split by up to three separator characters between digits.
RX_DIGIT_RUN = re.compile(r"(?<![\d])\d(?:[\s.\-/_,|·:]{0,3}\d){3,}(?![\d])")
# A short mixed letters-and-digits token, as alphanumeric passcodes are written.
RX_ALNUM_CODE = re.compile(r"\b(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{4,10}\b")
RX_CARD_SUFFIX_BEFORE = re.compile(r"(?:ending in|ending|terminada en|acabada en|termina en)\s*[*x•.]*\s*$")
RX_LINK = re.compile(
    r"(?:\b(?:https?|ftp|mailto|javascript|data|file|tel|sms):|\bwww\.)\S*"
    r"|\blocalhost\b|\b\d{1,3}(?:\.\d{1,3}){3}\b"
    r"|[\w-]+(?:\.[\w-]+)*\.(?:[^\W\d_]{2,24}|xn--[a-z0-9-]+)(?=[/:?#]|\s|$|[),.;])", re.I)
RX_IBAN = re.compile(r"\b[a-z]{2}\d{2}(?:[\s.\-]{0,2}[a-z0-9]){11,30}", re.I)
RX_GOV_ID = re.compile(
    r"\b\d{3}\s*[ .\-]\s*\d{2}\s*[ .\-]\s*\d{4}\b"             # US social security number
    r"|\b\d{1,2}\.?\d{3}\.?\d{3}[ \-]?[a-hj-np-tv-z]\b"          # Spanish DNI
    r"|\b[xyz][ \-]?\d{7}[ \-]?[a-hj-np-tv-z]\b"                  # Spanish NIE
    r"|\b[a-z]{2} ?\d{2} ?\d{2} ?\d{2} ?[a-d]\b"                # UK National Insurance number (any prefix)
    r"|\b[a-z][aeioux][a-z]{2}\d{6}[hmx][a-z]{5}[a-z\d]\d\b"        # Mexican CURP
    r"|\b(?:passport|pasaporte|dni|nie|ssn|cedula|tax id|nif|national id|id card)\b"
    r"(?:\W|no\b|number\b|num\b|is\b|es\b|numero\b){0,6}[a-z]{0,2}\d{6,}[a-z]?\b", re.I)
RX_MEDICAL_UNIT = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:mg/dl|ng/ml|mmol/l|g/dl|µg|mcg|iu/l|u/l|mg|ml)\b"
                             r"|\b\d{2,3}/\d{2,3}\s?(?:mmhg)?\b(?=.{0,20}\b(?:pressure|presion))", re.I)
MEDICAL_WORDS = ("glucose", "glucosa", "cholesterol", "colesterol", "creatinine", "creatinina",
                 "thyroid", "tiroides", "hba1c", "diagnosis", "diagnostico", "biopsy", "biopsia",
                 "prescription", "receta medica", "positive for", "negativo para", "positivo para",
                 "lab values", "test result", "hiv", "vih", "cancer", "tumor", "blood pressure",
                 "presion arterial", "pregnan", "embarazo", "dosage", "dosis", "tested positive",
                 "tested negative", "insulin", "insulina", "hemoglobin", "hemoglobina", "chemotherapy",
                 "quimioterapia", "mammogra", "mamografia", "psychiatr", "psiquiatr", "antibiotic")


RX_MEDICAL_WORDS = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in MEDICAL_WORDS) + r")")


def _digit_runs(text):
    """Digit runs, except money written as the template writes it and a card's last four."""
    text = RX_TEMPLATE_MONEY.sub(lambda m: " " * len(m.group()), text)
    for m in RX_DIGIT_RUN.finditer(text):
        raw = m.group()
        before = text[max(0, m.start() - 20):m.start()]
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 4 and RX_CARD_SUFFIX_BEFORE.search(fold(before)):
            continue
        yield m.start(), m.end(), digits


def _passcode(text, folded):
    words = [(w.start(), w.end()) for w in _CODE_WORDS.finditer(folded)]

    def near(start, end):
        return any(ws - 40 <= end and start <= we + 40 for ws, we in words)
    for m in RX_ALNUM_CODE.finditer(folded):
        if near(m.start(), m.end()):
            return True
    for start, end, digits in _digit_runs(text):
        if len(digits) == 6:
            return True
        if 4 <= len(digits) <= 8 and near(start, end):
            return True
    return False


def _account(text):
    if any(len(d) >= 9 for _s, _e, d in _digit_runs(text)):
        return True
    return any(sum(c.isdigit() for c in m.group()) >= 10 for m in RX_IBAN.finditer(text))


def audit(text):
    """Categories found in `text`: passcode, link, account_number, government_id, medical."""
    if not isinstance(text, str):
        return ["not_text"]
    text = normalise(text)
    folded = fold(text)
    found = []
    if _passcode(text, folded):
        found.append("passcode")
    if RX_LINK.search(folded):
        found.append("link")
    if _account(text):
        found.append("account_number")
    if RX_GOV_ID.search(folded):
        found.append("government_id")
    if RX_MEDICAL_UNIT.search(folded) or RX_MEDICAL_WORDS.search(folded):
        found.append("medical")
    return found


TRUSTED_FIELDS = ("issuer", "concept")
# Fields whose type the radar guarantees; audited only if they do not have that shape.
TYPED_FIELDS = {"amount": r"\d{1,9}\.\d{2}", "currency": r"[A-Z]{3}", "due": r"\d{4}-\d{2}-\d{2}",
                "date": r"\d{4}-\d{2}-\d{2}", "last_four": r"\d{4}"}


def audit_notice(notice):
    """Audit the text and every field value. The issuer and concept come from the rules file the
    owner wrote, so they are taken out of the text before auditing — otherwise a pharmacy called
    "Prescription Plan" could never be reminded about — and every other field is audited."""
    text = notice.text
    for key in TRUSTED_FIELDS:
        value = notice.fields.get(key)
        if value:
            text = text.replace(str(value), " · ")
    found = set(audit(text))
    for key, value in notice.fields.items():
        if value is None or key in TRUSTED_FIELDS:
            continue
        if key in TYPED_FIELDS and re.fullmatch(TYPED_FIELDS[key], str(value)):
            continue
        found.update(audit(str(value)))
    return sorted(found)
