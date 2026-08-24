"""The gate: decide what a human sees.

Three outcomes.

  GREEN  straight through, nobody looks at it.
  AMBER  targeted review: the record is pre-filled and specific fields are
         flagged with a reason. The reviewer confirms or corrects those fields
         rather than re-keying the document.
  RED    full manual handling: the system could not read the document, or does
         not believe it is the kind of document it handles.

The rules are ordered and the first match wins, so every document carries a
single primary reason as well as its full flag list.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from schema import CRITICAL_FIELDS, THRESHOLDS, Reason  # noqa: E402

GREEN, AMBER, RED = "GREEN", "AMBER", "RED"

# Flags that indicate the reader could not do its job, as opposed to flags
# about what it read.
_HARNESS = {
    "timeout": Reason.EXTRACTOR_TIMEOUT,
    "error": Reason.EXTRACTOR_ERROR,
    "parse_failure": Reason.PARSE_FAILURE,
    "unreadable": Reason.ILLEGIBLE,
}


def triage(record, flags):
    """Return (tier, primary_reason, flags). Fails closed on anything unexpected."""
    status = record.get("status", "error")

    # 1. The reader failed. Never silently drop the document.
    if status in _HARNESS:
        return RED, _HARNESS[status], flags

    # 2. Not a document we handle. Real intake contains post that is not an order.
    doc_type = (record.get("fields", {}).get("doc_type") or {}).get("value")
    if str(doc_type).strip().lower() in {"other", "none", "null"}:
        return RED, Reason.OUT_OF_SCOPE, flags

    # 3. Too much is wrong to be worth a targeted review; re-key it.
    flagged_critical = {f["field"] for f in flags if f["field"] in CRITICAL_FIELDS}
    if len(flagged_critical) >= THRESHOLDS["full_manual_flag_count"]:
        return RED, Reason.FULL_MANUAL, flags

    # 4. Anything at all raised: targeted review.
    if flags:
        return AMBER, flags[0]["code"], flags

    # 5. Nothing raised anywhere: straight through.
    return GREEN, None, flags


def summarise(tier, primary, flags):
    """One line a human can act on, for the queue view."""
    if tier == GREEN:
        return "No checks failed."
    if primary in (Reason.EXTRACTOR_TIMEOUT, Reason.EXTRACTOR_ERROR, Reason.PARSE_FAILURE):
        return "The reader failed on this document; it has not been extracted."
    if primary == Reason.ILLEGIBLE:
        return "The reader reported the page as too degraded to read."
    if primary == Reason.OUT_OF_SCOPE:
        return "Not an order, invoice or confirmation."
    if primary == Reason.FULL_MANUAL:
        fields = sorted({f["field"] for f in flags if f["field"]})
        return f"{len(fields)} fields in doubt ({', '.join(fields)}); re-key rather than review."
    parts = []
    for f in flags[:4]:
        parts.append(f"{f['field']}: {f['detail']}" if f["field"] else f["detail"])
    more = f" (+{len(flags) - 4} more)" if len(flags) > 4 else ""
    return "; ".join(p for p in parts if p) + more
