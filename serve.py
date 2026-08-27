"""A local demo: drop a document in and watch the pipeline decide about it.

  ./run.sh ui          then open http://127.0.0.1:5000

This is a *view over* the pipeline, not a second copy of it. Ingest, extraction,
the validators and the gate are all imported from the same modules that produced
the numbers in the README. If this page and `./run.sh eval` ever disagreed, the
page would be worthless, so there is deliberately no logic here that decides
anything about a document.

Two ways in, and the difference is labelled in the UI:

  Replay   -- click one of the 21 corpus documents. Reuses the committed model
              response from runs/final/raw/, re-runs the checks and the gate
              live. No CLI, no API key, no cost, about a second.
  Upload   -- a real extraction of whatever you drop. Needs the CLI, takes
              ~30 seconds, costs about $0.13.

One thing the page must never imply: for an uploaded document there is no ground
truth, so it can show what the gate decided and why, but not whether that was
right. Accuracy numbers only mean anything over the labelled corpus, which is
what `./run.sh eval` is for.
"""

from __future__ import annotations

import json
import queue
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import report  # noqa: E402  (thumbnail + shared colour tokens)
from pipeline import ingest, triage as triage_mod, validate  # noqa: E402
from pipeline.extract import CLAUDE_BIN, extract_one  # noqa: E402

CORPUS = ROOT / "corpus"
INPUTS = CORPUS / "inputs"
RUN_DIR = ROOT / "runs" / "final"
UI = ROOT / "ui"

ALLOWED = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
MAX_UPLOAD = 20 * 1024 * 1024
MAX_LIVE_JOBS = 2          # a stray double-click should not fan out into money

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD

_jobs: dict[str, queue.Queue] = {}
_live = threading.Semaphore(MAX_LIVE_JOBS)


# --------------------------------------------------------------------------
# Running one document
# --------------------------------------------------------------------------

def _emit(q, stage, **payload):
    q.put({"stage": stage, **payload})


def _finish(q, record, source):
    """Validate, gate and report -- identical for replayed and live documents."""
    flags = validate.check(record)
    _emit(q, "checking", flags=flags, count=len(flags))

    tier, primary, flags = triage_mod.triage(record, flags)
    summary = triage_mod.summarise(tier, primary, flags)

    _emit(
        q, "verdict",
        tier=tier,
        primary_reason=primary,
        summary=summary,
        flags=flags,
        fields=record.get("fields", {}),
        line_items=record.get("line_items", []),
        issues=record.get("issues", []),
        status=record.get("status"),
        error_detail=record.get("error_detail"),
        raw_response=(record.get("raw_response") or "")[:2000],
        meta=record.get("meta", {}),
        source=source,
    )


def run_replay(job_id, doc_id):
    """Re-gate a corpus document from its committed response. Free and instant."""
    q = _jobs[job_id]
    try:
        cache = RUN_DIR / "raw" / f"{doc_id}.json"
        entry = next((e for e in _manifest() if e["doc_id"] == doc_id), None)
        if not cache.exists() or entry is None:
            _emit(q, "error", message=f"No committed response for {doc_id}.")
            return
        path = INPUTS / entry["file"]

        _emit(q, "received", filename=entry["file"], replay=True,
              note="Replaying the committed response for this document. "
                   "The reading step already happened; the checks and the gate "
                   "are running now.")
        _emit(q, "preview", thumbnail=report.thumbnail(path, width=460))
        _emit(q, "inspected", **ingest.describe(path))

        record = json.loads(cache.read_text(encoding="utf-8"))
        _emit(q, "reading", replay=True, duration_s=record["meta"]["duration_s"],
              cost_usd=record["meta"]["cost_usd"])
        _finish(q, record, source="replay")
    except Exception as exc:                                  # noqa: BLE001
        _emit(q, "error", message=f"{type(exc).__name__}: {exc}")
    finally:
        q.put(None)


def run_upload(job_id, path: Path, original_name: str):
    """Really read an uploaded document. Costs money, can fail."""
    q = _jobs[job_id]
    acquired = False
    try:
        _emit(q, "received", filename=original_name, replay=False)
        _emit(q, "preview", thumbnail=report.thumbnail(path, width=460))
        _emit(q, "inspected", **ingest.describe(path))

        if not CLAUDE_BIN:
            _emit(q, "error", message=(
                "The `claude` CLI was not found on PATH, so this document cannot "
                "be read.\n\n"
                "  npm i -g @anthropic-ai/claude-code   then run `claude` once to sign in\n"
                "  or start the server with CLAUDE_BIN=/full/path/to/claude\n\n"
                "The corpus documents on the left still work — they replay "
                "committed responses and need no CLI."))
            return

        if not _live.acquire(blocking=False):
            _emit(q, "error", message="Two documents are already being read. "
                                      "Wait for one to finish and try again.")
            return
        acquired = True

        _emit(q, "reading", replay=False)
        started = time.time()
        record = extract_one(path, job_id[:8])
        _emit(q, "read_done", duration_s=round(time.time() - started, 1),
              cost_usd=record["meta"]["cost_usd"], status=record["status"],
              model=record["meta"].get("model"))
        _finish(q, record, source="live")
    except Exception as exc:                                  # noqa: BLE001
        _emit(q, "error", message=f"{type(exc).__name__}: {exc}")
    finally:
        if acquired:
            _live.release()
        shutil.rmtree(path.parent, ignore_errors=True)
        q.put(None)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

def _manifest():
    return json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))


def _committed_tiers():
    results = RUN_DIR / "results.jsonl"
    if not results.exists():
        return {}
    out = {}
    for line in results.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["doc_id"]] = r["triage"]["tier"]
    return out


@app.get("/")
def index():
    html = (UI / "index.html").read_text(encoding="utf-8")
    return Response(html.replace("/*SHARED_CSS*/", report.CSS), mimetype="text/html")


@app.get("/api/corpus")
def corpus():
    tiers = _committed_tiers()
    return jsonify([
        {
            "doc_id": e["doc_id"],
            "file": e["file"],
            "sender": e["sender_family"],
            "condition": e["condition"],
            "severity": e["severity"],
            "trap": e["trap"],
            "tier": tiers.get(e["doc_id"]),
        }
        for e in _manifest()
    ])


@app.get("/api/status")
def status():
    return jsonify({"cli": bool(CLAUDE_BIN), "cli_path": CLAUDE_BIN})


@app.post("/api/jobs")
def create_job():
    job_id = uuid.uuid4().hex
    _jobs[job_id] = queue.Queue()

    if request.is_json and (request.json or {}).get("doc_id"):
        doc_id = str(request.json["doc_id"])
        threading.Thread(target=run_replay, args=(job_id, doc_id), daemon=True).start()
        return jsonify({"job_id": job_id, "mode": "replay"})

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "No file supplied."}), 400
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in ALLOWED:
        return jsonify({
            "error": f"{suffix or 'that file type'} is not supported. "
                     f"Accepted: {', '.join(sorted(ALLOWED))}"
        }), 400

    workdir = Path(tempfile.mkdtemp(prefix="ui-job-"))
    # Keep the extension (the reader uses it) but not the caller's path.
    path = workdir / f"upload{suffix}"
    upload.save(path)
    threading.Thread(target=run_upload, args=(job_id, path, upload.filename),
                     daemon=True).start()
    return jsonify({"job_id": job_id, "mode": "live"})


@app.get("/api/jobs/<job_id>/events")
def events(job_id):
    q = _jobs.get(job_id)
    if q is None:
        return jsonify({"error": "unknown job"}), 404

    def stream():
        while True:
            item = q.get()
            if item is None:
                yield "event: done\ndata: {}\n\n"
                break
            yield f"data: {json.dumps(item)}\n\n"
        _jobs.pop(job_id, None)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": f"File is larger than {MAX_UPLOAD // (1024 * 1024)} MB."}), 413


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--host", default="127.0.0.1")   # localhost only, on purpose
    args = ap.parse_args()

    print(f"\n  Document extraction demo -> http://{args.host}:{args.port}")
    print(f"  reader: {CLAUDE_BIN or 'NOT FOUND (replay still works)'}\n")
    app.run(host=args.host, port=args.port, threaded=True)
