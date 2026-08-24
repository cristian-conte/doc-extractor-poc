# Results

Corpus of 21 documents from 7 senders.

## The number

| | |
|---|---|
| **Straight through, no human review** | **67%** (14/21) |
| 95% interval on that rate | 45% – 83% |
| **Wrong data that got through the gate** | **1 of 14 green** (7%) |
| Documents both automated and correct | 13/21 (62%) |
| Review recall (documents needing review that were flagged) | 83% (5/6) |
| Review precision (flagged documents that genuinely needed it) | 71% (5/7) |

Escapes: D09.

Needed review but went straight through: D09.

Flagged without needing it: D01, D12.

## Planted documents

| Doc | Trap | Tier | Reason | Caught |
|---|---|---|---|---|
| D17 | illegible | RED | FULL_MANUAL | yes |
| D18 | cropped_total | AMBER | MISSING_REQUIRED | yes |
| D19 | inconsistent_total | AMBER | SUM_MISMATCH | yes |
| D20 | rotated_180 | AMBER | LOW_CONFIDENCE | yes |
| D21 | out_of_scope | RED | OUT_OF_SCOPE | yes |

## By source condition

| Condition | Docs | Straight through | Critical field accuracy |
|---|---|---|---|
| digital | 7 | 3/7 | 100% |
| fax | 2 | 1/2 | 89% |
| photo | 4 | 3/4 | 97% |
| scan | 8 | 7/8 | 97% |

## Severity sweep (same document, three degradation levels)

| Doc | Severity | Tier | Critical field accuracy |
|---|---|---|---|
| D15 | 1 | GREEN | 100% |
| D02 | 2 | GREEN | 100% |
| D16 | 3 | GREEN | 100% |

## Per field

| Field | Critical | Correct | Wrong | Missing | Invented | Accuracy |
|---|---|---|---|---|---|---|
| `doc_type` | yes | 19 | 2 | 0 | 0 | 90% |
| `sender_name` | yes | 19 | 1 | 0 | 0 | 95% |
| `doc_id` | yes | 20 | 0 | 0 | 0 | 100% |
| `doc_date` | yes | 19 | 1 | 0 | 0 | 95% |
| `advertiser` | yes | 20 | 0 | 0 | 0 | 100% |
| `flight_start` | yes | 19 | 1 | 0 | 0 | 95% |
| `flight_end` | yes | 20 | 0 | 0 | 0 | 100% |
| `gross_total` | yes | 19 | 0 | 0 | 0 | 100% |
| `currency` | yes | 20 | 0 | 0 | 0 | 100% |
| `net_total` |  | 15 | 0 | 1 | 0 | 94% |
| `agency` |  | 16 | 1 | 0 | 1 | 89% |
| `line_item_count` |  | 18 | 2 | 0 | 0 | 90% |

## Which checks fired

| Reason code | Times |
|---|---|
| `LOW_CONFIDENCE` | 11 |
| `MISSING_REQUIRED` | 6 |
| `SUM_MISMATCH` | 4 |

## Cost and latency

- $0.124 per document, $2.62 for the corpus
- 49s mean, 153s p95 per document
