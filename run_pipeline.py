"""Run the corpus through the pipeline.

  python run_pipeline.py                 extract, validate, triage, write results
  python run_pipeline.py --from-cache    re-run validate and triage on cached
                                         model responses, no model calls
  python run_pipeline.py --only D03,D17  work on a subset

Model responses are cached per document so that iterating on the validators and
the gate -- which is where the design decisions actually live -- costs nothing
and takes seconds.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pipeline import ingest, triage as triage_mod, validate  # noqa: E402
from pipeline.extract import extract_one  # noqa: E402

CORPUS = ROOT / "corpus"
INPUTS = CORPUS / "inputs"
RUNS = ROOT / "runs"


def load_manifest():
    return json.loads((CORPUS / "manifest.json").read_text())


def process(entry, run_dir, from_cache, timeout):
    doc_id = entry["doc_id"]
    path = INPUTS / entry["file"]
    cache = run_dir / "raw" / f"{doc_id}.json"

    if from_cache and cache.exists():
        record = json.loads(cache.read_text())
    else:
        record = extract_one(path, doc_id, timeout=timeout)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(record, indent=2))
        print(f"  {doc_id}  {record['status']:<12} "
              f"{record['meta']['duration_s']:>5.1f}s  ${record['meta']['cost_usd']:.3f}",
              flush=True)

    flags = validate.check(record)
    tier, primary, flags = triage_mod.triage(record, flags)
    record["ingest"] = ingest.describe(path)
    record["triage"] = {
        "tier": tier,
        "primary_reason": primary,
        "flags": flags,
        "summary": triage_mod.summarise(tier, primary, flags),
    }
    record["corpus"] = {
        k: entry[k] for k in
        ("sender_family", "container", "condition", "severity", "trap", "expected_outcome")
    }
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", action="store_true",
                    help="reuse cached model responses; run validators only")
    ap.add_argument("--only", help="comma-separated document ids")
    ap.add_argument("--run", default="final", help="run directory name")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=None)
    args = ap.parse_args()

    entries = load_manifest()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        entries = [e for e in entries if e["doc_id"] in wanted]

    run_dir = RUNS / args.run
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)

    mode = "cached responses" if args.from_cache else f"{args.workers} workers"
    print(f"Processing {len(entries)} documents ({mode})\n")

    if args.from_cache:
        records = [process(e, run_dir, True, args.timeout) for e in entries]
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            records = list(pool.map(
                lambda e: process(e, run_dir, False, args.timeout), entries))

    records.sort(key=lambda r: r["doc_id"])
    out = run_dir / "results.jsonl"
    with out.open("w") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")

    # Fail-closed invariant: every input must have produced a record.
    assert len(records) == len(entries), "a document was dropped"

    tiers = {}
    for record in records:
        tiers[record["triage"]["tier"]] = tiers.get(record["triage"]["tier"], 0) + 1
    total_cost = sum(r["meta"]["cost_usd"] for r in records)
    print(f"\n  GREEN {tiers.get('GREEN', 0)}   AMBER {tiers.get('AMBER', 0)}   "
          f"RED {tiers.get('RED', 0)}")
    if not args.from_cache:
        print(f"  extraction cost ${total_cost:.2f} for {len(records)} documents")
    print(f"  wrote {out}")


if __name__ == "__main__":
    main()
