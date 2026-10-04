# Snapshot summary: audit/results/after_batch_3/snapshot

- tests: 308 passed, 0 failed | coverage total 94%, app code excl. provided utils.py 94% | cli.py 81% | eval/run.py 96% | explain.py 99%
- ruff findings: 0 | mypy errors (src): 0
- dev: [8, 8, 8]/10 per rep | heldout.json answerable: [28, 28, 28]/30 per rep | ambiguous clarified: 6/9
  - [heldout.json] executed 100%, first attempt ok 100% | latency P50 1.10s P95 1.84s | cost/query $0.00018 | tokens in/cached/out 2516/2504/241
  - [heldout.json] misses (id: reps missed of 3): {'q_004': 3, 'q_008': 3, 'a12': 3, 'a15': 3}
- dev: [8]/10 per rep | heldout_v2 answerable: [13]/14 per rep | ambiguous clarified: 1/1
  - [heldout_v2] executed 100%, first attempt ok 100% | latency P50 1.12s P95 1.96s | cost/query $0.00018 | tokens in/cached/out 2517/2512/239
  - [heldout_v2] misses (id: reps missed of 1): {'q_004': 1, 'q_008': 1, 'v12': 1}
- project suites: retry/scope: 9/9 | retry/clarify: 3/4 | retry/conv: 21/22 | turn_type accuracy 16/17
- behavior (independent sets): >>> scope_decline: 17/18 as expected (out_of_scope); >>> scope_answer_plain: 8/8 as expected (answer); >>> scope_answer_contains_why_reason_cause: 10/10 as expected (answer); >>> clarify_should: 3/8 as expected (clarify); >>> clarify_should_not: 10/10 as expected (answer)
- conversations c1..c11: asserted ['49/50', '10/11'] | false switches on turns that must NOT switch: 0/20 | missed switches (notice=yes expected): 0/5
