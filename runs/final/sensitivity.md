## What moving the gate costs

Same 21 cached model responses, different gate settings.

| Gate | Straight through | Wrong data through | Review recall | Review precision |
|---|---|---|---|---|
| as shipped | 67% (14/21) | **1** (D09) | 83% | 71% |
| currency not critical | 71% (15/21) | **1** (D09) | 83% | 83% |
| doc_type + currency not critical | 76% (16/21) | **1** (D09) | 83% | 100% |
| ignore the reader's own confidence | 81% (17/21) | **2** (D09, D20) | 67% | 100% |
| validators only, currency not critical | 81% (17/21) | **2** (D09, D20) | 67% | 100% |
