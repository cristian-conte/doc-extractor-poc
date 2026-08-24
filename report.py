"""Build the review queue: one static HTML page showing what a human would see.

The point of the page is the AMBER case. A reviewer should never have to re-key
a document that the system already read -- they should see the extracted record
with the doubtful fields called out, next to the document itself, and confirm or
correct only those fields.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import sys
from pathlib import Path

import pymupdf as fitz
from PIL import Image

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from schema import ALL_FIELDS, CRITICAL_FIELDS  # noqa: E402

INPUTS = ROOT / "corpus" / "inputs"
TIER_ORDER = {"RED": 0, "AMBER": 1, "GREEN": 2}


def thumbnail(path: Path, width=520) -> str:
    if path.suffix.lower() == ".pdf":
        doc = fitz.open(path)
        pix = doc[0].get_pixmap(dpi=110)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        doc.close()
    else:
        img = Image.open(path).convert("RGB")
    ratio = width / img.width
    img = img.resize((width, int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=72)
    return base64.b64encode(buf.getvalue()).decode()


CSS = """
:root { color-scheme: light dark;
  --bg:#fbfbfa; --fg:#1a1a18; --muted:#6b6b66; --line:#e2e2dd; --card:#fff;
  --green:#1c7d4d; --amber:#9a6100; --red:#a11d1d;
  --green-bg:#e8f5ee; --amber-bg:#fdf3e0; --red-bg:#fdeaea; }
@media (prefers-color-scheme: dark) { :root:not([data-theme=light]) {
  --bg:#16161a; --fg:#e8e8e4; --muted:#9a9a94; --line:#2e2e34; --card:#1e1e23;
  --green:#5fcf95; --amber:#e3aa4e; --red:#f08a8a;
  --green-bg:#16301f; --amber-bg:#332612; --red-bg:#331a1a; } }
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system,
  BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif; }
.wrap { max-width:1080px; margin:0 auto; padding:40px 24px 80px }
h1 { font-size:26px; margin:0 0 4px; letter-spacing:-.02em }
.sub { color:var(--muted); margin:0 0 28px }
.counts { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:32px }
.count { flex:1 1 150px; border:1px solid var(--line); border-radius:10px;
  padding:14px 16px; background:var(--card) }
.count b { display:block; font-size:26px; letter-spacing:-.02em }
.count span { color:var(--muted); font-size:12.5px; text-transform:uppercase;
  letter-spacing:.06em }
.doc { border:1px solid var(--line); border-radius:12px; background:var(--card);
  margin-bottom:18px; overflow:hidden }
.head { display:flex; align-items:center; gap:12px; padding:14px 18px;
  border-bottom:1px solid var(--line); flex-wrap:wrap }
.tier { font-weight:700; font-size:11.5px; letter-spacing:.09em; padding:4px 9px;
  border-radius:5px }
.GREEN .tier { color:var(--green); background:var(--green-bg) }
.AMBER .tier { color:var(--amber); background:var(--amber-bg) }
.RED   .tier { color:var(--red);   background:var(--red-bg) }
.id { font-weight:650 }
.meta { color:var(--muted); font-size:13px; margin-left:auto; text-align:right }
.why { padding:11px 18px; font-size:13.5px; border-bottom:1px solid var(--line) }
.AMBER .why { background:var(--amber-bg) }
.RED .why { background:var(--red-bg) }
.body { display:grid; grid-template-columns:minmax(0,1fr) 300px; gap:20px; padding:18px }
@media (max-width:760px){ .body { grid-template-columns:1fr } }
table { width:100%; border-collapse:collapse; font-size:13.5px }
td { padding:5px 8px; border-bottom:1px solid var(--line); vertical-align:top }
td.f { color:var(--muted); width:36%; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:12.5px }
tr.flagged td { background:var(--amber-bg) }
tr.flagged td.f { color:var(--amber); font-weight:600 }
.note { font-size:11.5px; color:var(--amber); display:block; margin-top:2px }
img { width:100%; border:1px solid var(--line); border-radius:6px; display:block }
.scroll { max-height:420px; overflow:auto; border-radius:6px }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px }
"""


def build(run_dir: Path, out_path: Path):
    records = [json.loads(l) for l in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if l]
    records.sort(key=lambda r: (TIER_ORDER[r["triage"]["tier"]], r["doc_id"]))
    counts = {t: sum(1 for r in records if r["triage"]["tier"] == t)
              for t in ("GREEN", "AMBER", "RED")}
    total = len(records)

    parts = [
        '<meta charset="utf-8">',
        "<title>Extraction Review Queue</title>",
        f"<style>{CSS}</style>",
        "<div class=wrap>",
        "<h1>Extraction review queue</h1>",
        f"<p class=sub>{total} documents. Green went straight through with nobody "
        "looking at them. Amber is pre-filled and needs specific fields confirmed. "
        "Red needs handling by hand.</p>",
        "<div class=counts>",
        f"<div class=count><b>{counts['GREEN']}</b><span>straight through</span></div>",
        f"<div class=count><b>{counts['AMBER']}</b><span>targeted review</span></div>",
        f"<div class=count><b>{counts['RED']}</b><span>manual</span></div>",
        f"<div class=count><b>{counts['GREEN'] / total:.0%}</b><span>automated</span></div>",
        "</div>",
    ]

    for r in records:
        tier = r["triage"]["tier"]
        flagged = {f["field"]: f for f in r["triage"]["flags"] if f["field"]}
        c = r["corpus"]
        parts += [
            f"<div class='doc {tier}'>",
            "<div class=head>",
            f"<span class=tier>{tier}</span>",
            f"<span class=id>{r['doc_id']}</span>",
            f"<span style='color:var(--muted);font-size:13px'>{html.escape(c['sender_family'])}"
            f" · {c['condition']}{' sev' + str(c['severity']) if c['severity'] else ''}"
            f" · {html.escape(r['input_file'])}</span>",
            f"<span class=meta>{r['meta']['duration_s']:.0f}s · "
            f"${r['meta']['cost_usd']:.3f}</span>",
            "</div>",
        ]
        if tier != "GREEN":
            parts.append(f"<div class=why><b>{r['triage']['primary_reason'] or ''}</b> — "
                         f"{html.escape(r['triage']['summary'])}</div>")
        parts.append("<div class=body><div><table>")
        for field in ALL_FIELDS:
            entry = r["fields"].get(field) or {}
            value = entry.get("value")
            conf = entry.get("confidence", "—")
            shown = "—" if value in (None, "") else html.escape(str(value))
            if conf == "uncertain":
                shown += " <span class=note>reader unsure</span>"
            row_class = " class=flagged" if field in flagged else ""
            note = ""
            if field in flagged:
                note = f"<span class=note>{html.escape(str(flagged[field]['detail'] or ''))}</span>"
            star = "*" if field in CRITICAL_FIELDS else ""
            parts.append(f"<tr{row_class}><td class=f>{field}{star}</td>"
                         f"<td>{shown}{note}</td></tr>")
        parts.append("</table>")
        if r["issues"]:
            parts.append("<div style='margin-top:10px;font-size:12.5px;color:var(--muted)'>"
                         + "<br>".join("· " + html.escape(i) for i in r["issues"][:4]) + "</div>")
        parts.append("</div><div class=scroll>"
                     f"<img src='data:image/jpeg;base64,{thumbnail(INPUTS / r['input_file'])}'>"
                     "</div></div></div>")

    parts += ["<p class=sub style='margin-top:24px;font-size:12.5px'>"
              "* critical field — doubt about any of these blocks straight-through processing."
              "</p></div>"]
    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="final")
    args = ap.parse_args()
    run_dir = ROOT / "runs" / args.run
    build(run_dir, run_dir / "report.html")
