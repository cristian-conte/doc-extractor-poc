"""What does moving the gate cost?

The straight-through rate is not a property of the reader, it is a property of
the gate in front of it. This re-runs the gate over the cached model responses
under several settings and reports what each one buys and what it risks. No
model calls, so it is free to run and quick to argue about.

  python sensitivity.py --run final
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import evaluate  # noqa: E402
from schema import CRITICAL_FIELDS, THRESHOLDS, Reason  # noqa: E402

TRUTH_DIR = ROOT / "corpus" / "ground_truth"

VARIANTS = [
    ("as shipped", dict()),
    ("currency not critical", dict(drop_critical={"currency"})),
    ("doc_type + currency not critical", dict(drop_critical={"currency", "doc_type"})),
    ("ignore the reader's own confidence", dict(ignore_codes={Reason.LOW_CONFIDENCE})),
    ("validators only, currency not critical",
     dict(drop_critical={"currency"}, ignore_codes={Reason.LOW_CONFIDENCE})),
]


def regate(record, drop_critical=frozenset(), ignore_codes=frozenset()):
    """Re-apply the triage rules with a modified critical set / flag filter."""
    critical = [f for f in CRITICAL_FIELDS if f not in drop_critical]
    flags = [
        f for f in record["triage"]["flags"]
        if f["code"] not in ignore_codes and f["field"] not in drop_critical
    ]
    status = record.get("status", "error")
    if status in {"timeout", "error", "parse_failure", "unreadable"}:
        return "RED", flags
    doc_type = (record.get("fields", {}).get("doc_type") or {}).get("value")
    if str(doc_type).strip().lower() in {"other", "none", "null"}:
        return "RED", flags
    flagged_critical = {f["field"] for f in flags if f["field"] in critical}
    if len(flagged_critical) >= THRESHOLDS["full_manual_flag_count"]:
        return "RED", flags
    return ("AMBER" if flags else "GREEN"), flags


def run(run_dir: Path):
    records = [json.loads(l) for l in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if l]
    scored = []
    for record in records:
        truth = json.loads((TRUTH_DIR / f"{record['doc_id']}.json").read_text(encoding="utf-8"))
        outcomes = evaluate.score_fields(record, truth)
        scored.append({
            "record": record,
            "outcomes": outcomes,
            "critical_errors": evaluate.critical_errors(outcomes),
            "needed_review": evaluate.needed_review(record, outcomes),
        })

    rows = []
    for label, kwargs in VARIANTS:
        tiers = [regate(s["record"], **kwargs)[0] for s in scored]
        green = [s for s, t in zip(scored, tiers) if t == "GREEN"]
        flagged = [s for s, t in zip(scored, tiers) if t != "GREEN"]
        escaped = [s for s in green if s["critical_errors"]]
        needed = [s for s in scored if s["needed_review"]]
        caught = [s for s, t in zip(scored, tiers) if s["needed_review"] and t != "GREEN"]
        rows.append({
            "variant": label,
            "straight_through": len(green) / len(scored),
            "green": len(green),
            "escaped": len(escaped),
            "escaped_ids": [s["record"]["doc_id"] for s in escaped],
            "recall": len(caught) / len(needed) if needed else 1.0,
            "precision": len([s for s in flagged if s["needed_review"]]) / len(flagged) if flagged else 1.0,
        })
    return rows, len(scored)


def render(rows, n):
    out = [
        "## What moving the gate costs",
        "",
        f"Same {n} cached model responses, different gate settings.",
        "",
        "| Gate | Straight through | Wrong data through | Review recall | Review precision |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        esc = f"**{r['escaped']}**" + (f" ({', '.join(r['escaped_ids'])})" if r["escaped_ids"] else "")
        out.append(
            f"| {r['variant']} | {r['straight_through']:.0%} ({r['green']}/{n}) | {esc} | "
            f"{r['recall']:.0%} | {r['precision']:.0%} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="final")
    args = ap.parse_args()
    run_dir = ROOT / "runs" / args.run
    rows, n = run(run_dir)
    text = render(rows, n)
    (run_dir / "sensitivity.md").write_text(text, encoding="utf-8")
    print(text)
