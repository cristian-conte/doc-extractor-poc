# Document extraction prototype

Seven people read documents that arrive from several hundred senders in no
common format, and retype the contents into a system. This is a working
prototype of the thing that takes most of that typing away, plus the evidence
for how much of it can safely go unattended.

It is a two-and-a-half hour build. It is meant to be argued with, not deployed.

```
./run.sh corpus     # build 21 documents from 7 senders, with ground truth
./run.sh extract    # read them  (makes model calls, ~$2.60 for the corpus)
./run.sh eval       # score, tune, render the review queue  (free, uses cache)
```

Setup is at the bottom under [Running it locally](#running-it-locally). The
short version: `./run.sh eval` replays the committed model responses and
reproduces every number below offline, free, with no API key.

## The number

**67% of documents went straight through with no human review. One of them
carried a wrong value, and it is an interesting one.**

| | |
|---|---|
| Straight through, nobody looks at it | **67%** (14 of 21) |
| 95% interval on that rate | 45% – 83% |
| **Wrong data that got past the gate** | **1** (D09) |
| Straight through *and* fully correct | 62% (13 of 21) |
| Caught what needed a human | 5 of 6 |
| Flagged documents that genuinely needed it | 5 of 7 |
| Cost | $0.13 per document, 52s each |

The third row is the one that matters. Straight-through rate on its own is
meaningless — a gate that flags nothing scores 100% — so it is only ever
reported next to the rate at which wrong data escapes.

**The escape is worth understanding before anything else here.** D09 is a
scanned page headed *STATION ADVERTISING CONTRACT*. Every field was read
correctly. The reader classified it as a `confirmation`; my ground truth says
`order`. Nothing was misread — the document does not say which of those two
words it is, and I never defined the boundary anywhere the reader could see it.
That is a specification failure of mine that the metric correctly counts as a
wrong record, because downstream it would route to the wrong place.

It is also the clearest evidence in the run for something else: **an earlier
extraction of this same document, from the same file, classified it correctly.**
Nothing changed but the run. That is the variance the interval is trying to
express, and it is why "67%" without "45–83%" would be a misleading number.

### How it was derived

The generator writes the ground truth before any extraction happens, so every
field has a known correct answer. `evaluate.py` is the only module that can
read those answers; nothing upstream of it can see them. For each document it
compares every extracted field against truth after normalising both sides
(dates to ISO, amounts to `Decimal`, names case- and suffix-folded), then:

- **straight-through rate** = documents the gate passed ÷ all documents
- **escaped error** = a document the gate passed that has at least one *critical*
  field wrong or invented
- **review recall** = of the documents that genuinely needed a human, how many
  the gate stopped
- **review precision** = of the documents the gate stopped, how many genuinely
  needed it

Critical fields are the ones that would cause a wrong payment or a wrong record
downstream: sender, document number, date, counterparty, flight dates, total,
currency, document type. An error in the agency name is recorded but counted
separately — it is not the same kind of mistake.

**At n=21 the interval is 45–83%, and that is the honest width of the claim.**
Anyone quoting "67%" without it is overstating what 21 documents can support.

---

## How it says who needs a human

Three outcomes, decided by an ordered rule table in `pipeline/triage.py`.
First match wins, so every document carries one primary reason plus its full
flag list.

| | What it means | What the queue shows |
|---|---|---|
| **GREEN** | Every check passed and the reader was confident about every critical field. | Nothing. It is booked. |
| **AMBER** | Something specific is in doubt. | The record, pre-filled, with the doubtful fields highlighted and a reason next to each. The reviewer confirms two fields; they do not retype the document. |
| **RED** | Could not be read, or is not a document we handle. | The page, and why. Handled by hand. |

Two rules do most of the work:

**Confidence can only ever demote.** A model saying "confident" is never why a
document passes — passing requires the deterministic checks to agree. A model
saying "uncertain" always stops it. This makes the reader's self-assessment
useful without trusting it.

**Everything fails closed.** Timeout, crash, unparseable output, refusal — all
land in RED with the raw output kept. No document is dropped and no missing
value is quietly defaulted to zero.

The flags are enumerated codes (`SUM_MISMATCH`, `MISSING_REQUIRED`,
`NET_GROSS_MISMATCH`, `DATE_RANGE_INVALID`, `LOW_CONFIDENCE`, `OUT_OF_SCOPE`, …)
rather than free text, so precision can be measured per reason and the
thresholds have somewhere obvious to be tuned. All of them live in one dict in
`schema.py`.

Open `runs/final/report.html` to see the queue as a reviewer would.

---

## The corpus

21 documents, 7 senders, 14 of them (67%) scans, photographs or faxes rather
than clean files, 5 planted to be caught. `corpus/MANIFEST.md` lists every one.

**These are synthetic, and that is a real limitation.** This sandbox has no
route to the FCC political file, SROIE, or any other real corpus — outbound
network is allow-listed to the model API and package registries. So the
generator stands in for the real thing. What that costs and what was done about
it:

- The layouts are seven separate renderers, not one template with the fonts
  swapped: a ruled grid invoice, a typewriter order form with dot leaders, a
  dense daypart rate table, a letter where every fact is inside a sentence, a
  boxed two-column contract, a minimal receipt, and a German rep firm invoicing
  in EUR with `1.234,56` separators.
- The degradations are physical and model-independent — resolution loss, skew,
  sensor noise, JPEG artefacts, 1-bit fax dithering with dropout streaks,
  perspective warp and shadow gradients from a phone camera. They degrade any
  reader equally.
- Several documents are traps whose correct answer is *"flag this"*, not any
  particular value: identifiers formatted like money, a prior-balance figure
  that is not the total, line items that do not sum to the printed total, a
  total photographed out of frame, a letter that is not an order at all. These
  test calibration rather than recognition, which is the part a synthetic
  corpus can still test honestly.
- The extraction prompt names the target fields only. It never sees the sender
  vocabulary or the template families.

What it still cannot tell you: how the reader copes with handwriting, coffee
stains, staples, multi-page documents, or the specific weirdness of any real
sender. **The 67% should be read as an upper bound and a demonstration of
method, not as evidence about a real inbox.**

### Ground truth cannot leak

The reader is a headless agent with file-reading tools of its own. Started
inside this project it could simply read `corpus/ground_truth/` and the whole
evaluation would be silently worthless. So every extraction runs in its own
temporary directory containing exactly one file — the document — with tools
restricted to reading. The same mechanism stops a permission prompt stalling a
headless run, without switching permission checks off.

---

## Where it breaks

Everything below is from the run in `runs/final/`, not from speculation.

**A category boundary I never defined cost more than any misreading.** The one
escape, D09, is not an OCR failure at all — see above. The general shape of it
matters more than the instance: a large fraction of real extraction error is not
the machine failing to read the page, it is the machine and the specification
disagreeing about what a field means. Those errors are invisible to every
validator in this system, because the extracted value is perfectly legible and
internally consistent. They are only findable by comparing against labelled
data, which is an argument for the shadow period rather than for a better model.

**The scan degradations do not bite.** The severity sweep — the same document
rendered at three levels of scan damage — was supposed to show accuracy falling
as damage rises. It is flat: all three passed with every critical field correct.
Either the reader is genuinely robust to resolution loss and skew, or my scan
transform is too gentle. Faxes *do* hurt (89% critical-field accuracy against
97% for scans), so the corpus has difficulty in it — but this specific control
did not do its job, and I would not claim the scan track is a hard test.

**The same document does not always read the same way.** D09 was extracted
twice in the course of building this, from an identical file, and classified
differently each time. Nothing in the pipeline is doing sampling deliberately.
Any threshold tuned on a single pass over 21 documents is fitting noise as much
as signal — which is the main reason the sensitivity table below is framed as
hypotheses rather than as an improvement.

**The only misread that mattered was a rotated page.** D20 is scanned upside
down. Everything came back correct except the station name: `WQRM` read as
`WORM`, a Q flipping to an O. No arithmetic check can catch that — it is a
perfectly plausible station name. The only thing that caught it was the reader
saying it was unsure about that field. That single document is the entire
argument for keeping self-reported confidence in the gate, and the sensitivity
table below prices it exactly.

**A confident reader misread a fax, and arithmetic caught it.** On D17 the reader
reader returned a total with "confident" attached and was wrong — the fax
dithering had eaten the digits. The line items did not sum to the total it
reported, so it never got near the straight-through path. This is the case the
deterministic validators exist for, and the reason confidence alone is not
allowed to promote anything.

**The worst bug in the run was mine, not the model's.** The prompt asks for
plain values inside line items; the model wrapped each one in
`{value, confidence}` anyway, to match the fields above it. My normaliser
stringified the dict and turned `'17600.20'` into `1760020`, which fired a false
SUM_MISMATCH and sent a perfectly good document to review. It cost 5 points of
straight-through rate. It is fixed in three places, and the shape of it — a
schema violation that produced a plausible wrong number instead of an error —
is exactly what I would expect to keep happening at volume.

**Two documents are flagged for a reason nobody would thank us for.** D01 and
D12 print `$` with no ISO currency code, so the reader marks currency
"uncertain" and the gate stops them. That is epistemically correct and
operationally useless: `$` on a document from an Ohio television station is USD.

**The by-condition table is misleading and I am not going to fix it.** It shows
clean digital files with the *worst* straight-through rate (3 of 7). That is
purely because most of the traps are digital documents — the reader is not
worse on clean files, the corpus just concentrates hard cases there. A table
that needs a paragraph of explanation is a bad table; I have left it in rather
than quietly drop the inconvenient slice.

---

## What moving the gate costs

The straight-through rate is a property of the gate, not of the reader.
`sensitivity.py` re-runs the gate over the same cached responses under different
settings, so the trade is visible instead of asserted.

| Gate | Straight through | Wrong data through | Review recall | Review precision |
|---|---|---|---|---|
| **as shipped** | **67%** (14/21) | **1** (D09) | 83% | 71% |
| currency not critical | 71% (15/21) | 1 (D09) | 83% | 83% |
| doc_type + currency not critical | 76% (16/21) | 1 (D09) | 83% | 100% |
| ignore the reader's own confidence | 81% (17/21) | **2** (D09, D20) | 67% | 100% |
| validators only, currency not critical | 81% (17/21) | **2** (D09, D20) | 67% | 100% |

Two things to read off this.

Dropping currency and document type from the critical set buys 9 points and
changes nothing that escapes. **I have not shipped that**, because choosing it
on the strength of 21 documents — one of which has already been shown to read
differently between runs — is fitting noise. It is a hypothesis to check against
real data, not a result. (Note also that D09's escape *is* a `doc_type` error,
so relaxing that field is a less obviously free move than the table makes it
look: the flag would not have caught it either way, but the direction of travel
is wrong.)

Turning off the reader's self-reported confidence buys 14 points and lets a
wrong station name through. That is the price of D20 stated exactly: 14 points
of throughput against one silently wrong record. Which side of that trade is
right is a business decision about what a wrong record costs, and it is not mine
to make — but the system should make the trade legible, which is what this table
is for.

---

## What I deliberately did not build

1. **Any validation on real documents.** The single largest gap. Everything
   here is method; none of it is evidence about a real inbox.
2. **A second independent extraction pass.** Two reads that disagree is the
   strongest available signal for the errors that survive here — plausible but
   wrong strings, which no arithmetic check can catch. The seam is in
   `extract.py`; it roughly doubles cost and latency, so it wants justifying
   against measured escapes rather than switching on by default.
3. **Multi-page and multi-document files.** Everything is one page. Real intake
   has 40-page PDFs with three invoices inside them, and page splitting is its
   own problem.
4. **A real review interface and the correction loop behind it.** The HTML queue
   is a read-only demonstration. The valuable half is capturing what reviewers
   change, because that is what tunes the thresholds and tells you the escape
   rate in production, where there is no ground truth.
5. **Sender identification.** With hundreds of recurring senders, recognising
   "this is that station again" and applying what was learned last time is
   probably where most of the remaining accuracy lives. Nothing here has any
   memory between documents.
6. **Duplicate detection.** The same invoice arriving by email and by post is a
   routine and expensive failure.
7. **Any control over how a document gets read.** The file is handed to the
   reader exactly as it arrived and the reader decides what to do with it —
   nothing here rasterises a PDF or strips its text layer. So on the seven
   born-digital PDFs it may be reading the text layer rather than looking at
   the page, and this pipeline can neither choose that nor observe which
   happened. Worth knowing before comparing cost or latency across containers,
   and the thing to fix first if either mattered.
8. **Any compliance posture.** Documents go to an external API. No PII
   handling, no retention policy. Provenance is a prompt hash, a cost figure and
   the reader's identity per record — better than nothing, nowhere near
   audit-grade. For most organisations with this problem, that list is the
   actual blocker, not accuracy.

---

## Before this went near a real client file

- **A real validation set**, several hundred documents, labelled by the people
  doing the job now, stratified by sender and by condition. Rebuild every number
  in this README on it. Expect them to be worse.
- **An agreed escape budget.** "How many wrong records per thousand is
  acceptable, and what does one cost when it happens?" Everything else is
  downstream of that answer, including where the thresholds sit. It is a
  business question and it has to be answered before the gate can be tuned
  rather than guessed.
- **Per-sender calibration.** A gate tuned on the whole population is wrong for
  every sender in it. With hundreds of senders and a long tail, the top twenty
  by volume deserve their own thresholds and their own measured accuracy.
- **A shadow period.** Run alongside the seven people, on live post, extracting
  everything and booking nothing. Compare against what they key. That measures
  the escape rate on real documents, which is the only number that actually
  governs the decision.
- **A defined failure path.** What happens when the model API is down, or slow,
  or has changed under you between Tuesday and Wednesday. Right now the answer
  is "everything goes RED", which is safe and useless.
- **Somewhere to put the reviewers.** Two thirds unattended does not mean four
  of seven people are redundant; it means the work changes shape, toward the
  hard documents and toward checking the machine. That should be planned
  deliberately rather than discovered.

---

## How it fits together

```
corpus/generate.py    21 documents + ground truth, fixed seed
   |
pipeline/ingest.py    what arrived; rasterise
pipeline/extract.py   one document -> one record, in an isolated directory
pipeline/validate.py  arithmetic, calendar and completeness checks -> reason codes
pipeline/triage.py    ordered rules -> GREEN / AMBER / RED
   |
evaluate.py           the only module that may read ground truth
sensitivity.py        what moving the gate costs
report.py             the review queue as a human would see it
```

`corpus/` and `pipeline/` never import each other. `schema.py` is the shared
vocabulary — fields, criticality, reason codes, thresholds, normalisers — and
is the first file to read.

Model responses are cached per document, so iterating on the validators and the
gate (where the design decisions actually live) costs nothing and takes seconds.
`runs/final/` holds the raw model output behind every number quoted here.

Reading is done by a vision-capable model through the local `claude` CLI, which
must be installed and authenticated. There is no classical OCR in this sandbox
to cross-check against, which is itself a limitation: an independent OCR pass
disagreeing with the model would be a strong, cheap error signal.

### Reproducibility

Same seed, same documents: 19 of the 21 files are byte-identical between runs.
The two image-only PDFs carry a library-generated identifier that changes each
time; their rendered content is identical. Extraction is not deterministic —
the same document can be read slightly differently on different runs, which is
part of why the interval on the headline number matters.

---

## Running it locally

Tested on Linux and written to be portable; macOS should be identical. Needs
Python 3.9 or newer.

```bash
git clone https://github.com/cristian-conte/Tools.git
cd Tools
git checkout claude/document-extraction-prototype-2zq1aq
cd Document-Extraction-Prototype

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

The virtual environment is not optional on a recent Mac: Homebrew and system
Pythons are marked externally managed, and a bare `pip install` fails with
`error: externally-managed-environment`.

**If `./run.sh` dies with `env: bash\r: No such file or directory`**, your git
has `core.autocrlf=true` and rewrote the line endings on checkout. The
`.gitattributes` here prevents that, but only for clones made after it landed.
To fix an existing clone:

```bash
git pull
git rm --cached -r . >/dev/null && git reset --hard   # re-checkout under the new rules
git config --global core.autocrlf input               # stop it happening again
```

Worth doing rather than working around: the same setting silently corrupts seven
of the corpus PDFs, because reportlab output is mostly ASCII and git's
text/binary heuristic misclassifies it. Tolerant readers repair the damage and
render identically, so nothing visibly breaks — which is exactly why it is worth
correcting rather than ignoring.

**Start here — reproduce every number in this README. Offline, free, seconds.**
The raw model responses for all 21 documents are committed in `runs/final/raw/`,
so scoring and reporting replay them. No API key, no network, no cost.

```bash
./run.sh eval
open runs/final/report.html      # the review queue
```

You should get exactly the numbers quoted above: 67% straight through, one
escape (D09). If you do not, something is wrong and I would like to know.

**Rebuild the documents.** Regenerates all 21 files and their ground truth from
the fixed seed. Still no model calls.

```bash
./run.sh corpus
```

**Actually re-read the documents.** This is the only step that needs the CLI and
the only one that costs anything — about $2.60 and ten minutes for the corpus at
five workers.

```bash
npm i -g @anthropic-ai/claude-code   # if you do not already have it
claude                               # once, to sign in
./run.sh extract && ./run.sh eval
```

If the CLI is installed somewhere unusual (nvm, fnm, volta), point at it
directly rather than fighting PATH:

```bash
CLAUDE_BIN=/full/path/to/claude ./run.sh extract
```

Expect the headline to move a little between runs. The same document does not
always read the same way — D09 has already been observed classifying correctly
on one run and incorrectly on another, from an identical file. That variance is
what the 45–83% interval is expressing.

### Which reader

Reading is done by a vision-capable model reached through the local `claude`
CLI — not the API SDK, which is why there is no Python client dependency. The
model is handed a *filename* and its own file-reading tool rather than an image,
so each extraction is a small agent loop (the records show three or four turns),
not a single vision call.

Whichever model the CLI defaults to is used unless you say otherwise:

```bash
EXTRACTION_MODEL=<model-id> ./run.sh extract
```

Each run prints its reader, and every record now carries `meta.model`.

**The committed run in `runs/final/` predates that and does not carry it.** So
the 67% in this README cannot be attributed to a specific model from the
evidence trail — only re-extracting will produce records that can. That is a
real limitation of the committed numbers rather than a cosmetic one: a
straight-through rate detached from the reader that earned it is not a claim
anyone should lean on, and if the CLI's default shifts, a re-run would move the
figures with nothing in the old records to explain why.

Useful extras:

```bash
python3 sensitivity.py --run final          # what moving the gate costs
python3 run_pipeline.py --only D17,D20 --run scratch   # work on a subset
```

### The demo UI

```bash
./run.sh ui        # http://127.0.0.1:5000
```

Drop a document on the page and watch it move through ingest, the reader, the
checks and the gate, ending on GREEN / AMBER / RED with the specific reasons.
The sidebar lists the 21 corpus documents; clicking one **replays its committed
response** — no CLI, no API key, no cost, about a second — which is what to
click first when showing someone. Dropping your own file is a real read: it
needs the CLI, takes about 30 seconds and costs around $0.13.

The page imports the pipeline rather than reimplementing it, so a replayed
verdict is identical to that document's row in `runs/final/results.jsonl`, flag
list included. There is a test for exactly that, because a demo that disagreed
with the evaluated pipeline would be worse than no demo.

Worth doing deliberately in front of someone: drop something that is not an
order at all. It should come back RED / `OUT_OF_SCOPE` rather than inventing a
total.

One thing the page will not tell you: whether it was *right*. An uploaded
document has no ground truth, so the demo shows what the gate decided and why,
never an accuracy figure. Accuracy is only measurable over the labelled corpus,
which is what `./run.sh eval` is for.

It binds to localhost, caps uploads at 20 MB, allows only document file types,
and runs at most two live reads at once so a double-click cannot fan out into
money. There is no authentication, because it is a local demo and pretending
otherwise would be theatre.

---
