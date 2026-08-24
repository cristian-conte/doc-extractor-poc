"""Shared vocabulary for the pipeline: target fields, criticality, reason codes,
thresholds and value normalisers.

Every other module imports its definitions from here so that the corpus, the
extractor, the validators and the evaluator all agree on what a field is called
and when two values count as the same value.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# --------------------------------------------------------------------------
# Target record
# --------------------------------------------------------------------------

DOC_TYPES = ["invoice", "order", "confirmation", "other"]

# Critical fields are the ones that would cause a wrong payment or a wrong
# record downstream if they were extracted incorrectly. Doubt about any of
# these blocks straight-through processing.
CRITICAL_FIELDS = [
    "doc_type",
    "sender_name",
    "doc_id",
    "doc_date",
    "advertiser",
    "flight_start",
    "flight_end",
    "gross_total",
    "currency",
]

# Optional fields are captured and reported, but uncertainty about them does
# not by itself force a human to look at the document.
OPTIONAL_FIELDS = [
    "net_total",
    "agency",
    "line_item_count",
]

ALL_FIELDS = CRITICAL_FIELDS + OPTIONAL_FIELDS

DATE_FIELDS = {"doc_date", "flight_start", "flight_end"}
AMOUNT_FIELDS = {"gross_total", "net_total"}
INT_FIELDS = {"line_item_count"}

CONFIDENCE_LEVELS = ["confident", "uncertain", "not_present"]


# --------------------------------------------------------------------------
# Reason codes
#
# Enumerated rather than free text so that the evaluation can report review
# precision per reason, and so that thresholds have an obvious tuning surface.
# --------------------------------------------------------------------------

class Reason:
    EXTRACTOR_TIMEOUT = "EXTRACTOR_TIMEOUT"
    EXTRACTOR_ERROR = "EXTRACTOR_ERROR"
    PARSE_FAILURE = "PARSE_FAILURE"
    ILLEGIBLE = "ILLEGIBLE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    FULL_MANUAL = "FULL_MANUAL"
    MISSING_REQUIRED = "MISSING_REQUIRED"
    SUM_MISMATCH = "SUM_MISMATCH"
    NET_GROSS_MISMATCH = "NET_GROSS_MISMATCH"
    DATE_INVALID = "DATE_INVALID"
    DATE_RANGE_INVALID = "DATE_RANGE_INVALID"
    AMOUNT_INSANE = "AMOUNT_INSANE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    DISAGREEMENT = "DISAGREEMENT"


# --------------------------------------------------------------------------
# The dial. Every tuneable number the triage gate depends on lives here.
# --------------------------------------------------------------------------

THRESHOLDS = {
    # sum(line items) vs stated gross, in currency units
    "sum_tolerance": Decimal("0.02"),
    # standard advertising agency commission; net is normally gross x (1 - c)
    "agency_commission": Decimal("0.15"),
    # allowed relative drift when checking net against gross
    "net_gross_rel_tolerance": Decimal("0.01"),
    # plausible document date window, in years either side of today
    "date_window_years": 3,
    # implausible money
    "amount_min": Decimal("0"),
    "amount_max": Decimal("10000000"),
    # number of flagged critical fields that tips a document from targeted
    # review (AMBER) into full manual re-keying (RED)
    "full_manual_flag_count": 3,
    # extractor harness
    "timeout_seconds": 240,
    "max_attempts": 2,
}


# --------------------------------------------------------------------------
# Normalisers
#
# The extractor is asked to emit normalised values, but it is not trusted to.
# Everything is re-normalised here before comparison so that "$12,375.00",
# "12375", and "12.375,00" all reduce to the same Decimal.
# --------------------------------------------------------------------------

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_US = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$")


def norm_date(value):
    """Return an ISO date string, or None if the value is not a usable date."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if not s:
        return None
    m = _ISO.match(s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            return None
    m = _US.match(s)
    if m:
        mm, dd, yy = int(m[1]), int(m[2]), int(m[3])
        if yy < 100:
            yy += 2000
        try:
            return date(yy, mm, dd).isoformat()
        except ValueError:
            return None
    for fmt in ("%d %B %Y", "%B %d, %Y", "%b %d, %Y", "%d %b %Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def norm_amount(value):
    """Return a Decimal, or None. Handles US and European separator styles."""
    if value is None:
        return None
    # A confidence-wrapped value can reach here if an upstream unwrap is missed.
    # Stringifying the dict would silently yield a plausible wrong number, so
    # unwrap explicitly.
    if isinstance(value, dict):
        return norm_amount(value.get("value"))
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s or s in {"-", ".", ","}:
        return None
    # Decide which separator is the decimal point by looking at the last one.
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    if last_dot > last_comma:
        s = s.replace(",", "")
    elif last_comma > last_dot:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def norm_text(value):
    """Casefold, strip punctuation and collapse whitespace for fuzzy comparison."""
    if value is None:
        return None
    s = str(value).strip().lower()
    s = re.sub(r"\b(inc|llc|ltd|co|corp|company|gmbh|the)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def norm_field(field, value):
    """Normalise a value according to what kind of field it is."""
    if field in DATE_FIELDS:
        return norm_date(value)
    if field in AMOUNT_FIELDS:
        return norm_amount(value)
    if field in INT_FIELDS:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None
    if field == "doc_id":
        # Document numbers differ across senders in punctuation only.
        if value is None:
            return None
        s = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
        return s or None
    if field == "currency":
        if value is None:
            return None
        return str(value).strip().upper()[:3] or None
    return norm_text(value)


def values_match(field, a, b):
    """True when two raw values mean the same thing for this field."""
    na, nb = norm_field(field, a), norm_field(field, b)
    if na is None or nb is None:
        return na is nb or (na is None and nb is None)
    if field in AMOUNT_FIELDS:
        return abs(na - nb) <= THRESHOLDS["sum_tolerance"]
    if field in {"sender_name", "advertiser", "agency"}:
        # Senders write their own names inconsistently; substring containment
        # in either direction counts as a match.
        return na == nb or na in nb or nb in na
    return na == nb
