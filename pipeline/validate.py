"""Deterministic checks over an extracted record.

None of these consult the model. They are arithmetic and calendar facts about
the extracted values, which makes them the part of the system that catches a
confident-sounding wrong answer. Every check returns an enumerated reason code
and names the field it concerns, so review is targeted rather than a re-read of
the whole document.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from schema import (  # noqa: E402
    CRITICAL_FIELDS, THRESHOLDS, Reason, norm_amount, norm_date,
)


def _flag(code, field=None, detail=None):
    return {"code": code, "field": field, "detail": detail}


def _unwrap(item):
    """Line item values sometimes arrive confidence-wrapped. Take the value."""
    if not isinstance(item, dict):
        return {}
    return {k: (v.get("value") if isinstance(v, dict) and "value" in v else v)
            for k, v in item.items()}


def _value(record, field):
    return (record.get("fields", {}).get(field) or {}).get("value")


def _confidence(record, field):
    return (record.get("fields", {}).get(field) or {}).get("confidence", "not_present")


def check(record) -> list:
    """Return the list of flags raised against this record."""
    flags = []
    fields = record.get("fields", {})

    # --- completeness -----------------------------------------------------
    for field in CRITICAL_FIELDS:
        entry = fields.get(field)
        if entry is None or entry.get("value") in (None, ""):
            flags.append(_flag(Reason.MISSING_REQUIRED, field, "not extracted"))
        elif entry.get("confidence") == "not_present":
            flags.append(_flag(Reason.MISSING_REQUIRED, field, "reported absent"))

    gross = norm_amount(_value(record, "gross_total"))
    net = norm_amount(_value(record, "net_total"))

    # --- arithmetic -------------------------------------------------------
    # The strongest check available: it does not depend on the reader being
    # honest about its own confidence, only on the numbers agreeing.
    lines = [_unwrap(li) for li in (record.get("line_items") or [])]
    amounts = [norm_amount(li.get("amount")) for li in lines]
    if lines and all(a is not None for a in amounts) and gross is not None:
        line_sum = sum(amounts, Decimal("0"))
        if abs(line_sum - gross) > THRESHOLDS["sum_tolerance"]:
            flags.append(_flag(
                Reason.SUM_MISMATCH, "gross_total",
                f"line items sum to {line_sum}, document states {gross}"))

    for i, li in enumerate(lines):
        qty, rate, amt = li.get("quantity"), norm_amount(li.get("unit_rate")), norm_amount(li.get("amount"))
        if qty is None or rate is None or amt is None:
            continue
        try:
            expected = rate * Decimal(str(int(qty)))
        except (ValueError, TypeError):
            continue
        if abs(expected - amt) > THRESHOLDS["sum_tolerance"]:
            flags.append(_flag(
                Reason.SUM_MISMATCH, f"line_items[{i}]",
                f"{qty} x {rate} = {expected}, line states {amt}"))

    if gross is not None and net is not None and gross != 0:
        expected = gross * (1 - THRESHOLDS["agency_commission"])
        if abs(net - expected) > abs(expected) * THRESHOLDS["net_gross_rel_tolerance"]:
            flags.append(_flag(
                Reason.NET_GROSS_MISMATCH, "net_total",
                f"net {net} is not {THRESHOLDS['agency_commission']:.0%} off gross {gross}"))

    # --- plausibility -----------------------------------------------------
    for field in ("gross_total", "net_total"):
        amount = norm_amount(_value(record, field))
        if amount is None:
            continue
        if amount <= THRESHOLDS["amount_min"] or amount > THRESHOLDS["amount_max"]:
            flags.append(_flag(Reason.AMOUNT_INSANE, field, f"value {amount}"))

    today = date.today()
    parsed_dates = {}
    for field in ("doc_date", "flight_start", "flight_end"):
        raw = _value(record, field)
        if raw in (None, ""):
            continue
        iso = norm_date(raw)
        if iso is None:
            flags.append(_flag(Reason.DATE_INVALID, field, f"unparseable: {raw!r}"))
            continue
        parsed_dates[field] = date.fromisoformat(iso)
        drift = abs((parsed_dates[field] - today).days) / 365.25
        if drift > THRESHOLDS["date_window_years"]:
            flags.append(_flag(Reason.DATE_INVALID, field,
                               f"{iso} is outside the plausible window"))

    if {"flight_start", "flight_end"} <= parsed_dates.keys():
        if parsed_dates["flight_end"] < parsed_dates["flight_start"]:
            flags.append(_flag(Reason.DATE_RANGE_INVALID, "flight_end",
                               "schedule ends before it starts"))

    # --- self-reported confidence ----------------------------------------
    # Deliberately last, and deliberately only able to demote. A model saying
    # "confident" never promotes a record; a model saying "uncertain" always
    # stops it.
    for field in CRITICAL_FIELDS:
        if _confidence(record, field) == "uncertain":
            flags.append(_flag(Reason.LOW_CONFIDENCE, field, "reader was unsure"))

    return flags
