# Snapshot summary: audit/results/before_fixes

- tests: 55 passed, 0 failed | coverage total 44%, app code excl. provided utils.py 66% | cli.py 0% | eval/run.py 0% | explain.py 62%
- ruff findings: 68 | mypy errors (src): 19
- dev: [9, 9, 9]/10 per rep | heldout.json answerable: [28, 28, 28]/30 per rep | ambiguous clarified: 9/9
  - [heldout.json] executed 100%, first attempt ok 100% | latency P50 3.72s P95 10.01s | cost/query $0.00028 | tokens in/cached/out 2338/1512/221
  - [heldout.json] misses (id: reps missed of 3): {'q_004': 3, 'a12': 3, 'a15': 3}
- dev: [9]/10 per rep | heldout_v2 answerable: [14]/14 per rep | ambiguous clarified: 1/1
  - [heldout_v2] executed 100%, first attempt ok 100% | latency P50 1.23s P95 3.16s | cost/query $0.00020 | tokens in/cached/out 2339/2130/226
  - [heldout_v2] misses (id: reps missed of 1): {'q_004': 1}
- project suites: retry/scope: 9/9 | retry/clarify: 4/4 | retry/conv: 21/22 | turn_type accuracy 16/17
- behavior (independent sets): >>> scope_decline: 17/18 as expected (out_of_scope); >>> scope_answer_plain: 8/8 as expected (answer); >>> scope_answer_contains_why_reason_cause: 10/10 as expected (answer); >>> clarify_should: 5/8 as expected (clarify); >>> clarify_should_not: 10/10 as expected (answer)
- conversations c1..c11: asserted ['47/48', '9/11'] | false switches on turns that must NOT switch: 2/18 | missed switches (notice=yes expected): 1/5
