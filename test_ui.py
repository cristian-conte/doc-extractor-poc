"""Check the demo UI cannot drift away from the evaluated pipeline.

Starts the server, replays all 21 corpus documents through the HTTP API, and
asserts each verdict matches that document's row in runs/final/results.jsonl --
tier, primary reason, summary and the full flag list.

The demo exists to be shown to people. If it ever disagreed with the numbers in
the README it would be worse than not having it, so this is the one test worth
having.

  python3 test_ui.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 5099
BASE = f"http://127.0.0.1:{PORT}"


def wait_for_server(proc, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early:\n{proc.stdout.read()}")
        try:
            urllib.request.urlopen(f"{BASE}/api/status", timeout=1).read()
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    raise RuntimeError("server did not come up")


def replay(doc_id):
    req = urllib.request.Request(
        f"{BASE}/api/jobs",
        data=json.dumps({"doc_id": doc_id}).encode(),
        headers={"Content-Type": "application/json"},
    )
    job = json.loads(urllib.request.urlopen(req).read())
    with urllib.request.urlopen(f"{BASE}/api/jobs/{job['job_id']}/events") as stream:
        for raw in stream:
            line = raw.decode().strip()
            if line.startswith("data: ") and line[6:] != "{}":
                event = json.loads(line[6:])
                if event["stage"] == "verdict":
                    return event
                if event["stage"] == "error":
                    raise AssertionError(f"{doc_id}: {event['message']}")
            if line == "event: done":
                break
    raise AssertionError(f"{doc_id}: stream ended with no verdict")


def main():
    committed = {}
    for line in (ROOT / "runs" / "final" / "results.jsonl").read_text(
            encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            committed[record["doc_id"]] = record["triage"]

    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "serve.py"), "--port", str(PORT)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    failures = []
    try:
        wait_for_server(proc)
        for doc_id, want in sorted(committed.items()):
            got = replay(doc_id)
            mismatches = [
                name for name, a, b in (
                    ("tier", got["tier"], want["tier"]),
                    ("primary_reason", got["primary_reason"], want["primary_reason"]),
                    ("summary", got["summary"], want["summary"]),
                    ("flags", got["flags"], want["flags"]),
                ) if a != b
            ]
            status = "ok" if not mismatches else "DRIFT: " + ", ".join(mismatches)
            print(f"  {doc_id}  {got['tier']:<5} {status}")
            if mismatches:
                failures.append((doc_id, mismatches))
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    print()
    if failures:
        print(f"FAILED: {len(failures)} document(s) disagree with the pipeline")
        return 1
    print(f"All {len(committed)} replays match runs/final/results.jsonl exactly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
