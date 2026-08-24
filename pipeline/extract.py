"""Extraction: hand one document to a vision-capable model and get a record back.

Two things matter here beyond calling the model.

Isolation. Each call runs in its own temporary directory containing exactly one
file: the document. The model runs as a headless agent with file-reading tools
of its own, so if it were started inside the project it could read the ground
truth and the evaluation would be silently worthless. The isolation directory
also means no permission prompt can stall a headless run, without resorting to
switching permission checks off.

Failing closed. Every failure mode -- timeout, crash, unparseable output,
refusal -- produces a record marked for human review with the raw output kept.
A document is never dropped and a missing value is never quietly defaulted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from schema import THRESHOLDS  # noqa: E402

FIELD_GUIDE = """\
  "doc_type":        one of "invoice", "order", "confirmation", "other".
                     Use "other" for anything that is not a commercial order,
                     invoice or booking confirmation.
  "sender_name":     the organisation that issued the document.
  "doc_id":          the document's own reference number.
  "doc_date":        the date the document was issued.
  "advertiser":      the party whose product or service is being advertised.
  "agency":          the intermediary buying on the advertiser's behalf, if any.
  "flight_start":    first date of the advertising schedule.
  "flight_end":      last date of the advertising schedule.
  "gross_total":     the headline total for the whole document, before any
                     commission or discount is deducted.
  "net_total":       the amount payable after commission is deducted, if the
                     document states one.
  "currency":        three-letter ISO currency code.
  "line_item_count": how many individual charge lines the document lists.\
"""

LINE_ITEM_GUIDE = """\
Also include a "line_items" key: a list (NOT confidence-wrapped) of the
individual charge lines, each an object with "description", "quantity",
"unit_rate" and "amount". Use null for any part you cannot read. Give an empty
list if the document has no itemised charges.\
"""

PROMPT = f"""\
Read the file {{filename}} in the current directory. It is a single business
document received from an external sender. It may be a clean digital file, or a
scan, photograph or fax of a printed page.

Extract these fields:

{FIELD_GUIDE}

{LINE_ITEM_GUIDE}

Output ONLY a JSON object. No prose, no explanation, no markdown fences.

Every field must be an object of the form
  {{{{"value": <value or null>, "confidence": "confident" | "uncertain" | "not_present"}}}}

Use the confidence values precisely:
  "confident"   - you can read the value clearly and are sure it is the field asked for.
  "uncertain"   - you can see something but cannot read it reliably, OR several
                  numbers on the page could be the one asked for and you cannot
                  tell which.
  "not_present" - the document genuinely does not show this field.

Never invent a plausible-looking value for something you cannot read. Guessing
is worse than saying "uncertain".

Normalise values: dates as YYYY-MM-DD; amounts as plain decimal numbers with a
dot as the decimal separator and no thousands separators or currency symbols;
currency as a three-letter code.

If the page is too degraded to read at all, output exactly:
  {{{{"unreadable": true, "reason": "<short reason>"}}}}

You may also add an "issues" key: a list of short strings describing anything
about the document that a human should know -- internal inconsistencies,
ambiguous labels, parts of the page missing or cut off.
"""

PROMPT_HASH = hashlib.sha256(PROMPT.encode()).hexdigest()[:12]

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def _parse_model_json(text: str):
    """Pull a JSON object out of the model's reply, tolerating fences and chatter."""
    if not text:
        return None
    cleaned = _FENCE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _run_cli(doc_path: Path, timeout: int):
    """Run one extraction in an isolated directory. Returns (envelope, error)."""
    workdir = Path(tempfile.mkdtemp(prefix="extract-"))
    try:
        local = workdir / doc_path.name
        shutil.copy2(doc_path, local)
        cmd = [
            "claude", "-p", PROMPT.format(filename=doc_path.name),
            "--allowedTools", "Read",
            "--output-format", "json",
        ]
        model = os.environ.get("EXTRACTION_MODEL")
        if model:
            cmd += ["--model", model]
        try:
            proc = subprocess.run(
                cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return None, ("timeout", f"no response within {timeout}s")
        if proc.returncode != 0:
            return None, ("error", (proc.stderr or proc.stdout or "")[-800:])
        try:
            return json.loads(proc.stdout), None
        except json.JSONDecodeError:
            return None, ("error", f"CLI did not return JSON: {proc.stdout[-400:]}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def extract_one(doc_path: Path, doc_id: str, timeout: int = None) -> dict:
    """Extract one document, retrying once before giving up."""
    timeout = timeout or THRESHOLDS["timeout_seconds"]
    attempts, last_error, started = 0, None, time.time()
    cost, turns, raw_text = 0.0, 0, ""

    while attempts < THRESHOLDS["max_attempts"]:
        attempts += 1
        envelope, err = _run_cli(doc_path, timeout)
        if err:
            last_error = err
            continue
        cost += float(envelope.get("total_cost_usd") or 0)
        turns += int(envelope.get("num_turns") or 0)
        raw_text = envelope.get("result") or ""
        parsed = _parse_model_json(raw_text)
        if parsed is None:
            last_error = ("parse_failure", "model output was not JSON")
            continue
        return _record(doc_id, doc_path, "ok", parsed, raw_text, attempts,
                       started, cost, turns)

    kind, detail = last_error or ("error", "unknown")
    return _record(doc_id, doc_path, kind, None, raw_text, attempts,
                   started, cost, turns, detail=detail)


def _record(doc_id, doc_path, status, parsed, raw_text, attempts, started,
            cost, turns, detail=None):
    fields, issues, unreadable, line_items = {}, [], False, []
    if parsed:
        unreadable = bool(parsed.get("unreadable"))
        raw_issues = parsed.get("issues") or []
        issues = [str(i) for i in raw_issues] if isinstance(raw_issues, list) else [str(raw_issues)]
        if unreadable and parsed.get("reason"):
            issues.append(str(parsed["reason"]))
        raw_lines = parsed.get("line_items")
        if isinstance(raw_lines, list):
            # The prompt asks for plain values inside line items, but the model
            # often wraps them in {value, confidence} to match the fields above.
            # Unwrap rather than trust: a dict reaching the arithmetic checks
            # stringifies into nonsense and fires a false mismatch.
            line_items = [
                {k: (v.get("value") if isinstance(v, dict) and "value" in v else v)
                 for k, v in li.items()}
                for li in raw_lines if isinstance(li, dict)
            ]
        for key, val in parsed.items():
            if key in {"unreadable", "reason", "issues", "line_items"}:
                continue
            if isinstance(val, dict) and ("value" in val or "confidence" in val):
                fields[key] = {
                    "value": val.get("value"),
                    "confidence": val.get("confidence", "uncertain"),
                }
            else:
                # A bare value with no confidence wrapper: accept it, but never
                # treat an unlabelled value as a confident one.
                fields[key] = {"value": val, "confidence": "uncertain"}
    return {
        "doc_id": doc_id,
        "input_file": doc_path.name,
        "status": "unreadable" if unreadable else status,
        "fields": fields,
        "line_items": line_items,
        "issues": issues,
        "error_detail": detail,
        "raw_response": raw_text,
        "meta": {
            "attempts": attempts,
            "duration_s": round(time.time() - started, 1),
            "cost_usd": round(cost, 4),
            "turns": turns,
            "prompt_hash": PROMPT_HASH,
        },
    }
