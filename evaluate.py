"""Score a run against ground truth.

This is the only module that reads `corpus/ground_truth/`. Nothing upstream of
it can see the answers.

The headline is deliberately a pair of numbers, not one. Straight-through rate
on its own is trivially gamed -- a gate that flags nothing scores 100% -- so it
is always reported next to the rate at which wrong data got through the gate.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from schema import ALL_FIELDS, CRITICAL_FIELDS, OPTIONAL_FIELDS, values_match  # noqa: E402

TRUTH_DIR = ROOT / "corpus" / "ground_truth"


def wilson(successes, total, z=1.96):
    """Wilson score interval. At n=20 the interval is wide; that is the point."""
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def score_fields(record, truth):
    """Compare one record's fields to truth.

    Returns {field: outcome} where outcome is one of:
      correct         the value matches, or was correctly reported absent
      wrong           a value was produced and it is not the right one
      missing         a value exists on the page but was not produced
      correct_absent  the field is genuinely not on the page and was not invented
      invented        the field is not on the page but a value was produced
    """
    truth_fields = truth["fields"]
    not_visible = set(truth.get("not_visible_fields", []))
    outcomes = {}

    # A document that is not an order at all is scored on one question only:
    # did the system recognise that it does not belong here. Whatever it read
    # off the page is discarded with the document, so scoring those fields
    # would measure something nobody acts on.
    if truth_fields.get("doc_type") == "other":
        entry = record.get("fields", {}).get("doc_type") or {}
        got = str(entry.get("value")).strip().lower()
        return {"doc_type": "correct" if got == "other" else "wrong"}

    for field in ALL_FIELDS:
        entry = record.get("fields", {}).get(field) or {}
        got = entry.get("value")
        confidence = entry.get("confidence", "not_present")
        expected = truth_fields.get(field)

        produced = got not in (None, "") and confidence != "not_present"

        if field in not_visible:
            outcomes[field] = "correct_absent" if not produced else "invented"
            continue
        if expected in (None, ""):
            outcomes[field] = "correct_absent" if not produced else "invented"
            continue
        if not produced:
            outcomes[field] = "missing"
            continue
        outcomes[field] = "correct" if values_match(field, got, expected) else "wrong"

    return outcomes


def critical_errors(outcomes):
    """Critical fields that are actively wrong or invented (not merely missing)."""
    return [f for f in CRITICAL_FIELDS if outcomes.get(f) in {"wrong", "invented"}]


def needed_review(record, outcomes):
    """Would a human have had to touch this document for the record to be right?"""
    if record["corpus"]["expected_outcome"] == "review_expected":
        return True
    return any(outcomes.get(f) in {"wrong", "invented", "missing"} for f in CRITICAL_FIELDS)


def evaluate(run_dir: Path):
    records = [json.loads(l) for l in (run_dir / "results.jsonl").read_text().splitlines() if l]
    rows = []

    for record in records:
        truth = json.loads((TRUTH_DIR / f"{record['doc_id']}.json").read_text())
        outcomes = score_fields(record, truth)
        errs = critical_errors(outcomes)
        rows.append({
            "doc_id": record["doc_id"],
            "tier": record["triage"]["tier"],
            "primary_reason": record["triage"]["primary_reason"],
            "flags": record["triage"]["flags"],
            "summary": record["triage"]["summary"],
            "outcomes": outcomes,
            "critical_errors": errs,
            "clean": not errs and all(
                outcomes.get(f) in {"correct", "correct_absent"} for f in CRITICAL_FIELDS),
            "needed_review": needed_review(record, outcomes),
            "corpus": record["corpus"],
            "meta": record["meta"],
        })

    n = len(rows)
    green = [r for r in rows if r["tier"] == "GREEN"]
    flagged = [r for r in rows if r["tier"] in {"AMBER", "RED"}]

    escaped = [r for r in green if r["critical_errors"]]
    truly_clean_green = [r for r in green if r["clean"]]
    needed = [r for r in rows if r["needed_review"]]
    caught = [r for r in needed if r["tier"] in {"AMBER", "RED"}]
    justified = [r for r in flagged if r["needed_review"]]

    headline = {
        "documents": n,
        "straight_through": {
            "count": len(green),
            "rate": len(green) / n if n else 0,
            "wilson_95": wilson(len(green), n),
        },
        "escaped_critical_error": {
            "count": len(escaped),
            "rate_of_green": len(escaped) / len(green) if green else 0,
            "rate_of_all": len(escaped) / n if n else 0,
            "wilson_95_of_green": wilson(len(escaped), len(green)),
            "doc_ids": [r["doc_id"] for r in escaped],
        },
        "true_automation": {
            "count": len(truly_clean_green),
            "rate": len(truly_clean_green) / n if n else 0,
        },
        "review_recall": {
            "caught": len(caught), "needed": len(needed),
            "rate": len(caught) / len(needed) if needed else 1.0,
            "missed_doc_ids": [r["doc_id"] for r in needed if r["tier"] == "GREEN"],
        },
        "review_precision": {
            "justified": len(justified), "flagged": len(flagged),
            "rate": len(justified) / len(flagged) if flagged else 1.0,
            "over_flagged_doc_ids": [r["doc_id"] for r in flagged if not r["needed_review"]],
        },
    }

    # Per-field behaviour across the whole corpus.
    per_field = {}
    for field in ALL_FIELDS:
        counts = Counter(r["outcomes"].get(field) for r in rows)
        scored = counts["correct"] + counts["wrong"] + counts["missing"] + counts["invented"]
        per_field[field] = {
            "critical": field in CRITICAL_FIELDS,
            "correct": counts["correct"],
            "wrong": counts["wrong"],
            "missing": counts["missing"],
            "invented": counts["invented"],
            "correct_absent": counts["correct_absent"],
            "accuracy": (counts["correct"] / scored) if scored else None,
        }

    # Slices: which conditions actually cost accuracy.
    def slice_by(key):
        buckets = defaultdict(list)
        for r in rows:
            buckets[r["corpus"][key]].append(r)
        out = {}
        for name, group in sorted(buckets.items(), key=lambda kv: str(kv[0])):
            g = [r for r in group if r["tier"] == "GREEN"]
            crit_scored = crit_correct = 0
            for r in group:
                for f in CRITICAL_FIELDS:
                    if r["outcomes"].get(f) in {"correct", "wrong", "missing", "invented"}:
                        crit_scored += 1
                        crit_correct += r["outcomes"][f] == "correct"
            out[str(name)] = {
                "documents": len(group),
                "green": len(g),
                "straight_through_rate": len(g) / len(group) if group else 0,
                "critical_field_accuracy": crit_correct / crit_scored if crit_scored else None,
            }
        return out

    # The severity sweep: identical content, three degradation levels.
    sweep = {}
    for r in rows:
        if r["corpus"]["trap"] == "sweep" or r["doc_id"] in {"D02", "D15", "D16"}:
            crit_scored = sum(1 for f in CRITICAL_FIELDS
                              if r["outcomes"].get(f) in {"correct", "wrong", "missing", "invented"})
            crit_correct = sum(1 for f in CRITICAL_FIELDS if r["outcomes"].get(f) == "correct")
            sweep[r["doc_id"]] = {
                "severity": r["corpus"]["severity"],
                "tier": r["tier"],
                "critical_field_accuracy": crit_correct / crit_scored if crit_scored else None,
            }

    planted = [{
        "doc_id": r["doc_id"],
        "trap": r["corpus"]["trap"],
        "expected": r["corpus"]["expected_outcome"],
        "tier": r["tier"],
        "primary_reason": r["primary_reason"],
        "caught": r["tier"] in {"AMBER", "RED"},
        "critical_errors": r["critical_errors"],
    } for r in rows if r["corpus"]["expected_outcome"] == "review_expected"]

    reason_counts = Counter()
    for r in rows:
        for f in r["flags"]:
            reason_counts[f["code"]] += 1

    durations = [r["meta"]["duration_s"] for r in rows]
    costs = [r["meta"]["cost_usd"] for r in rows]
    economics = {
        "cost_usd_total": round(sum(costs), 3),
        "cost_usd_mean": round(statistics.mean(costs), 4) if costs else 0,
        "latency_s_mean": round(statistics.mean(durations), 1) if durations else 0,
        "latency_s_p95": round(
            sorted(durations)[min(len(durations) - 1, math.ceil(0.95 * len(durations)) - 1)], 1
        ) if durations else 0,
    }

    return {
        "headline": headline,
        "per_field": per_field,
        "by_condition": slice_by("condition"),
        "by_container": slice_by("container"),
        "by_sender": slice_by("sender_family"),
        "severity_sweep": sweep,
        "planted": planted,
        "reason_codes": dict(reason_counts.most_common()),
        "economics": economics,
        "rows": rows,
    }


def render_markdown(m):
    h = m["headline"]
    st, esc = h["straight_through"], h["escaped_critical_error"]
    lo, hi = st["wilson_95"]
    out = [
        "# Results",
        "",
        f"Corpus of {h['documents']} documents from {len(m['by_sender'])} senders.",
        "",
        "## The number",
        "",
        "| | |",
        "|---|---|",
        f"| **Straight through, no human review** | **{st['rate']:.0%}** "
        f"({st['count']}/{h['documents']}) |",
        f"| 95% interval on that rate | {lo:.0%} – {hi:.0%} |",
        f"| **Wrong data that got through the gate** | **{esc['count']} of "
        f"{st['count']} green** ({esc['rate_of_green']:.0%}) |",
        f"| Documents both automated and correct | {h['true_automation']['count']}"
        f"/{h['documents']} ({h['true_automation']['rate']:.0%}) |",
        f"| Review recall (documents needing review that were flagged) | "
        f"{h['review_recall']['rate']:.0%} "
        f"({h['review_recall']['caught']}/{h['review_recall']['needed']}) |",
        f"| Review precision (flagged documents that genuinely needed it) | "
        f"{h['review_precision']['rate']:.0%} "
        f"({h['review_precision']['justified']}/{h['review_precision']['flagged']}) |",
        "",
    ]
    if esc["doc_ids"]:
        out += [f"Escapes: {', '.join(esc['doc_ids'])}.", ""]
    if h["review_recall"]["missed_doc_ids"]:
        out += [f"Needed review but went straight through: "
                f"{', '.join(h['review_recall']['missed_doc_ids'])}.", ""]
    if h["review_precision"]["over_flagged_doc_ids"]:
        out += [f"Flagged without needing it: "
                f"{', '.join(h['review_precision']['over_flagged_doc_ids'])}.", ""]

    out += ["## Planted documents", "",
            "| Doc | Trap | Tier | Reason | Caught |", "|---|---|---|---|---|"]
    for p in m["planted"]:
        out.append(f"| {p['doc_id']} | {p['trap']} | {p['tier']} | "
                   f"{p['primary_reason'] or '–'} | {'yes' if p['caught'] else 'NO'} |")

    out += ["", "## By source condition", "",
            "| Condition | Docs | Straight through | Critical field accuracy |",
            "|---|---|---|---|"]
    for name, s in m["by_condition"].items():
        acc = f"{s['critical_field_accuracy']:.0%}" if s["critical_field_accuracy"] is not None else "–"
        out.append(f"| {name} | {s['documents']} | {s['green']}/{s['documents']} | {acc} |")

    if m["severity_sweep"]:
        out += ["", "## Severity sweep (same document, three degradation levels)", "",
                "| Doc | Severity | Tier | Critical field accuracy |", "|---|---|---|---|"]
        for doc_id, s in sorted(m["severity_sweep"].items(), key=lambda kv: kv[1]["severity"]):
            acc = f"{s['critical_field_accuracy']:.0%}" if s["critical_field_accuracy"] is not None else "–"
            out.append(f"| {doc_id} | {s['severity']} | {s['tier']} | {acc} |")

    out += ["", "## Per field", "",
            "| Field | Critical | Correct | Wrong | Missing | Invented | Accuracy |",
            "|---|---|---|---|---|---|---|"]
    for field, s in m["per_field"].items():
        acc = f"{s['accuracy']:.0%}" if s["accuracy"] is not None else "–"
        out.append(f"| `{field}` | {'yes' if s['critical'] else ''} | {s['correct']} | "
                   f"{s['wrong']} | {s['missing']} | {s['invented']} | {acc} |")

    out += ["", "## Which checks fired", "", "| Reason code | Times |", "|---|---|"]
    for code, count in m["reason_codes"].items():
        out.append(f"| `{code}` | {count} |")

    e = m["economics"]
    out += ["", "## Cost and latency", "",
            f"- ${e['cost_usd_mean']:.3f} per document, ${e['cost_usd_total']:.2f} for the corpus",
            f"- {e['latency_s_mean']:.0f}s mean, {e['latency_s_p95']:.0f}s p95 per document", ""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="final")
    args = ap.parse_args()
    run_dir = ROOT / "runs" / args.run

    metrics = evaluate(run_dir)
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    md = render_markdown(metrics)
    (run_dir / "metrics.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
