# Document extraction prototype

Extracts structured data from documents that share no common format, and decides
which ones a human still needs to look at.

The problem it was built against: an organisation receives documents from several
hundred senders, almost none using the same layout, a large share arriving as
scans or phone photographs rather than clean files. Seven people read them and
retype the contents into a downstream system. This takes most of that typing
away — and, more importantly, is honest about the part it cannot.

**On its 21-document test corpus: 67% of documents go straight through with no
human review. One of those carried a wrong value.** Both numbers matter, and the
second is the one to read first — see [Results](#results).

> A two-and-a-half hour prototype, built to be argued with rather than deployed.
> The test corpus is synthetic and the headline should be read as an upper bound.
> [Honest limitations](#honest-limitations) is not a footnote; it is the point.

---

## Quickstart

Needs Python 3.9+.

```bash
git clone https://github.com/cristian-conte/document-extraction-prototype.git
cd document-extraction-prototype

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

The virtual environment is not optional on a recent Mac — Homebrew and system
Pythons are marked externally managed, and a bare `pip install` fails with
`error: externally-managed-environment`.

**Reproduce every number below. Offline, free, seconds.** The raw model responses
for all 21 documents are committed in `runs/final/raw/`, so scoring replays them.
No API key, no network, no cost.

```bash
./run.sh eval
open runs/final/report.html      # the review queue
```

**Try it on your own documents.** Drag a file onto the page and watch it move
through the pipeline.

```bash
./run.sh ui                      # http://127.0.0.1:5000
```

| Command | What it does | Cost |
|---|---|---|
| `./run.sh eval` | Re-score and re-render from committed responses | free |
| `./run.sh ui` | Drop-a-document demo | free to replay, ~$0.13 per live read |
| `./run.sh corpus` | Rebuild the 21 test documents from a fixed seed | free |
| `./run.sh extract` | Actually re-read every document | ~$2.60 |

Only `extract`, and live reads in the UI, need the reader CLI:
`npm i -g @anthropic-ai/claude-code`, then run `claude` once to sign in. If it is
installed somewhere unusual, `CLAUDE_BIN=/full/path/to/claude ./run.sh ui`.

<details>
<summary><code>./run.sh</code> fails with <code>env: bash\r: No such file or directory</code></summary>

Your git has `core.autocrlf=true` and rewrote the line endings on checkout. The
committed `.gitattributes` prevents this for fresh clones. To repair an existing
one:

```bash
git rm --cached -r . >/dev/null && git reset --hard
git config --global core.autocrlf input
```

Worth fixing rather than working around: the same setting silently corrupts seven
of the corpus PDFs, because they are mostly ASCII and git's text/binary heuristic
misclassifies them as text. Tolerant readers repair the damage and render
identical pixels, so nothing visibly breaks.
</details>

---

## How it works

Two halves, and the split between them is the whole design.

```
document ─→ ingest ─→ READER (a vision model)  ─→ raw JSON text
                          │  non-deterministic, creative, untrusted
                          ▼
                      parse + normalise
                          │
                          ▼
                      VALIDATORS  ─→ flags ─→ GATE ─→ GREEN / AMBER / RED
                          deterministic Python, never consults the model
```

The **reader** is allowed to be creative, because the problem *is* format
variety. There is no template matching, no per-sender regex and no layout model
anywhere in this repo. Reading is done by a vision-capable model reached through
the local `claude` CLI — handed a filename and its own file-reading tool, so each
extraction is a small agent loop rather than a single vision call.

The **checker** gets no creativity at all. It is arithmetic and calendar facts:
do the line items sum to the stated total, does `qty × rate` match the line, is
net 15% off gross, do the dates parse and order correctly. This is what catches a
confidently-wrong read.

### The gate

| | Meaning | What a person sees |
|---|---|---|
| **GREEN** | Every check passed, reader confident on every critical field | Nothing. It is booked. |
| **AMBER** | Something specific is in doubt | The record, pre-filled, doubtful fields highlighted with a reason. They confirm two fields; they do not retype the document. |
| **RED** | Unreadable, or not a document we handle | The page, and why. Handled by hand. |

Two rules do most of the work.

**Confidence can only demote, never promote.** A model saying "confident" is
never why a document passes — passing requires the deterministic checks to agree.
A model saying "uncertain" always stops it. That makes the reader's
self-assessment useful without trusting it.

**Everything fails closed.** Timeout, crash, unparseable output, refusal — all
land in RED with the raw output kept. No document is dropped and no missing value
is quietly defaulted to zero.

Flags are enumerated codes (`SUM_MISMATCH`, `MISSING_REQUIRED`,
`NET_GROSS_MISMATCH`, `DATE_RANGE_INVALID`, `LOW_CONFIDENCE`, `OUT_OF_SCOPE`, …)
so review precision can be measured per reason. Every threshold lives in one dict
in `schema.py`.

### Layout

```
schema.py            fields, critical set, reason codes, thresholds, normalisers
pipeline/
  ingest.py          container, page count, text-layer flag (metadata only)
  extract.py         one document -> one record, in an isolated directory
  validate.py        deterministic checks -> flags
  triage.py          ordered rules -> tier
evaluate.py          scores against ground truth; the ONLY module that reads it
sensitivity.py       what moving the gate costs
report.py            static review queue
serve.py + ui/       the drop-a-document demo
corpus/generate.py   the 21 test documents and their ground truth
test_ui.py           asserts the demo agrees with the evaluated pipeline
```

`corpus/` and `pipeline/` never import each other. `schema.py` is the shared
vocabulary and the first file to read.

**Ground truth cannot leak.** The reader is an agent with file-reading tools of
its own; started inside this project it could read `corpus/ground_truth/` and the
evaluation would be silently worthless. Every extraction therefore runs in its
own temporary directory containing exactly one file — the document — with tools
restricted to reading.

---

## Results

| | |
|---|---|
| Straight through, no human review | **67%** (14 of 21) |
| 95% interval on that rate | 45% – 83% |
| **Wrong data that got past the gate** | **1** |
| Straight through *and* fully correct | 62% (13 of 21) |
| Caught what needed a human | 5 of 6 |
| Flagged documents that genuinely needed it | 5 of 7 |
| Cost and latency | $0.13 and ~52s per document |

Straight-through rate on its own is meaningless — a gate that flags nothing
scores 100% — so it is only ever reported next to the rate at which wrong data
escapes.

**At n=21 the interval is 45–83%.** Quoting "67%" without it overstates what 21
documents can support.

### The escape is the most useful thing here

D09 is a scanned page headed *STATION ADVERTISING CONTRACT*. Every field was read
correctly. The reader classified it `confirmation`; ground truth says `order`.
Nothing was misread — the document does not say which of those two words it is,
and the boundary was never defined anywhere the reader could see it. A
specification failure wearing an OCR failure's clothes, and invisible to every
validator here, because the extracted value is legible and internally consistent.

An earlier extraction of that same file classified it correctly. Nothing changed
but the run. That is what the interval is expressing.

### What moving the gate costs

`sensitivity.py` re-runs the gate over the same cached responses under different
settings, so the trade is visible rather than asserted.

| Gate | Straight through | Wrong data through | Review recall | Review precision |
|---|---|---|---|---|
| **as shipped** | **67%** | **1** | 83% | 71% |
| currency not critical | 71% | 1 | 83% | 83% |
| doc_type + currency not critical | 76% | 1 | 83% | 100% |
| ignore the reader's own confidence | 81% | **2** | 67% | 100% |

Relaxing two fields buys 9 points with no new escapes. **It is deliberately not
shipped** — choosing that on 21 documents, one of which has already flip-flopped
between runs, is fitting noise.

Turning off self-reported confidence buys 14 points and lets a wrong station name
through (`WQRM` read as `WORM` on an upside-down scan — a plausible name no
arithmetic can catch). That is the price stated exactly: 14 points of throughput
against one silently wrong record. Which side of that is right is a business
decision about what a wrong record costs.

---

## The test corpus

21 documents, 7 senders, 14 of them (67%) scans, photographs or faxes. Five are
planted to be caught rather than extracted. `corpus/MANIFEST.md` lists every one.

Seven separate layout renderers, not one template with the fonts swapped: a ruled
grid invoice, a typewriter order form, a dense daypart rate table, a letter where
every fact sits inside a sentence, a boxed two-column contract, a minimal
receipt, and a German rep firm invoicing in EUR with `1.234,56` separators.
Degradations are physical — resolution loss, skew, sensor noise, JPEG artefacts,
1-bit fax dithering with dropout streaks, perspective warp, shadow gradients.

Several documents are traps whose correct answer is *"flag this"* rather than any
particular value: identifiers formatted like money, a prior-balance figure that
is not the total, line items that do not sum to the printed total, a total
photographed out of frame, a letter that is not an order at all.

Reproducible from a fixed seed. Ground truth is written by the generator before
any extraction happens.

---

## Honest limitations

**The corpus is synthetic.** It was built in a sandbox with no route to the FCC
political file, SROIE or any other real corpus. The layouts, degradations and
judgment traps are real difficulty, but nothing here tells you how the system
copes with handwriting, coffee stains, staples, or the specific weirdness of any
real sender. **Read 67% as an upper bound and a demonstration of method.**

**The scan degradations do not bite.** The severity sweep — the same document at
three levels of scan damage — was meant to show accuracy falling as damage rises.
It is flat. Either the reader is genuinely robust to resolution loss and skew, or
the transform is too gentle. Faxes do hurt; the scan track is not a hard test.

**The committed run cannot be attributed to a model.** Records now carry
`meta.model`, but the committed extraction predates that, so the 67% cannot be
traced to a specific reader from the evidence trail. Only re-extracting fixes
that, and re-extracting would move the numbers.

**No control over how a document gets read.** The file is handed to the reader as
it arrived; nothing rasterises a PDF or strips its text layer. On born-digital
PDFs it may be reading the text layer rather than looking at the page, and this
pipeline can neither choose that nor observe which happened.

**Deliberately not built:** validation on real documents · a second independent
extraction pass (the seam is in `extract.py`) · multi-page and multi-document
files · a real review interface and the correction loop behind it · sender
identification and per-sender memory · duplicate detection · any compliance
posture — documents go to an external API, with no PII handling or retention
policy.

### Before this went near a real client file

- **A real validation set**, several hundred documents labelled by the people
  doing the job now, stratified by sender and condition. Rebuild every number
  here on it, and expect them to be worse.
- **An agreed escape budget.** How many wrong records per thousand is
  acceptable, and what does one cost when it happens? Everything else is
  downstream of that answer, and it is a business question rather than a
  technical one.
- **Per-sender calibration.** A gate tuned on the whole population is wrong for
  every sender in it.
- **A shadow period.** Run alongside the existing team on live post, extracting
  everything and booking nothing. That measures the escape rate on real
  documents, which is the only number that governs the decision.
- **A defined failure path** for when the API is slow, down, or has changed under
  you between Tuesday and Wednesday.
- **Somewhere to put the reviewers.** Two thirds unattended does not mean four of
  seven people are redundant; the work changes shape, toward the hard documents
  and toward checking the machine.

---

## Development

```bash
python3 test_ui.py                                     # demo vs pipeline agreement
python3 sensitivity.py --run final                     # gate trade-offs
python3 run_pipeline.py --only D17,D20 --run scratch   # work on a subset
```

`test_ui.py` replays all 21 corpus documents through the demo's HTTP API and
asserts each verdict matches its row in `runs/final/results.jsonl` — tier,
reason, summary and the full flag list. A demo that disagreed with the evaluated
pipeline would be worse than no demo.

Model responses are cached per document, so iterating on the validators and the
gate — where the design decisions actually live — costs nothing and takes
seconds.
