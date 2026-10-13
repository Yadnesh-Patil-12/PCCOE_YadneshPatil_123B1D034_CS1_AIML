# Evaluation report

Generated: 2026-10-11 10:51:07  
Configuration: hashing-tfidf + numpy + LLM: extractive

* Questions: 43 (37 answerable, 6 out-of-scope)
* Retrieval hit@1/3/5: 0.676 / 0.838 / 0.973, MRR 0.783
* Answer pass rate: 0.973 | citation accuracy: 0.973 | false refusals: 0.0
* Out-of-scope refusal accuracy: 1.0
* Extraction macro P/R/F1: 1.0 / 1.0 / 1.0
* Seeded issue recall: 1.0 (12/12); precision on high/medium findings: 1.0
* Latency mean / p95: 11.1 ms / 15.1 ms

## Failed or weak cases

* P09 - Which unit measures the current flowing through the battery pack? -> gold section not cited; fact recall 0.00