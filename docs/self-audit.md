# Self-audit and fixes

After the agent worked, I audited my own work as a skeptical reviewer would: 12 phases from requirements and security to
evaluation validity, UX and generalisation, with a rule that every claim needs a saved output behind it. The audit produced 33
numbered findings (F-01 to F-33; the numbering skips F-02) ranked from blocker to nit. I fixed them in three batches, test first,
with a before and after measurement for each, and kept the evidence in [`results/`](../results/README.md). Decision rows for each
batch are in [`fix-logs/`](fix-logs/).

What the audit found that mattered most:

- A broken function in the explanation checker (`NameError`) that no test covered (F-01).
- Questions about data the database does not have were answered with a proxy or got the wrong refusal message (F-05); a "silent
  proxy" problem that remains only partly solved.
- The topic-switch rule disagreed with its own design and misfired after a clarification (F-03, F-04).
- Date columns in unfamiliar formats were silently mishandled (F-12).
- A scorer that passed 5 of 27 wrong answers (F-07), and a model-call token cap that truncated reasoning models and looked like
  flaky accuracy (found earlier; every experiment was re-run after fixing it).
- Unbounded waits: a hung endpoint blocked for 60 s and quitting after Ctrl-C took 11 s (F-08).

Not every fix improved accuracy, and the report says so: see "What did not improve" below.

## Status of every finding

| ID | Status | Where | Test | Evidence |
|---|---|---|---|---|
| F-01 | Fixed | `src/explain.py:84` (`check_consistency`) | `tests/test_explain_consistency.py` (4) | 4 failing tests -> pass; 0/32 contradictions in `results/after_batch_1/explain_exp.txt`, so the AST redesign was not triggered |
| F-03 | Fixed | `src/session.py:92,110` | `tests/test_session.py` | false switches 2/17 -> 0/17, missed 1/5 -> 0/5 same day (`results/after_batch_2/f03_convos_*.txt`) |
| F-04 | Fixed | same rule | `tests/test_session.py` | no false notice after a clarification (c9, c11) |
| F-05 | Fixed, with a measured limit | `src/agent.py` prompt, `src/render.py:46` | `tests/test_reasons.py` | missing concept 0/8 -> 7/8; proxy handling 0/4 -> 1/4 (`results/after_batch_2/f05_reasons_*.txt`). Both class sets were used for prompt selection, so these are optimistic |
| F-06 | Fixed | `src/cli.py:52` | `tests/test_description_warning.py` (9) | silent -> warns in 4/4 bad cases |
| F-07 | Fixed | `eval/compare.py:18` | `tests/test_compare.py` (14) | scorer false passes 5 -> 0 of 27; 0 of 432 saved verdicts changed (`results/after_batch_3/f07_*`) |
| F-08 | Fixed | `src/cli.py:59`, `src/llm.py:17` | `tests/test_latency_and_retry.py`, `eval/e2e_terminal.py` | hung endpoint 60.4 s -> 30.4 s; runaway query 20.2 s -> 10.2 s; exit after Ctrl-C 16.3 s -> 0.5 s |
| F-09 | Fixed | `src/agent.py:27` | `tests/test_agent.py` | 5 failure modes `clarify` -> `error` |
| F-10 | Fixed | `src/agent.py:178` | `tests/test_latency_and_retry.py` | identical retry SQL no longer re-run |
| F-11 | Fixed | `src/schema.py` (`_strip_format_hint`) | `tests/test_schema_dates.py` | CSV format hint dropped when the profile exists |
| F-12 | Fixed | `src/schema.py:159` | `tests/test_schema_dates.py` (21) | clinic 12/15 -> 13/15 same day (`results/after_batch_2/f12_clinic_paired.txt`); m09 flipped (`f12_m09_isolation.txt`) |
| F-13 | Fixed | `README.md` Requirements (rewritten) | documentation consistency test (not in this repo) | 346,207,293 bytes (`results/after_batch_1/f13_f28_facts.txt`) |
| F-14 | Fixed | `tests/test_cli_repl.py`, `test_llm_and_render.py`, `test_eval_runner.py` | those files | app-code coverage 66% -> 94%; `cli.py` 0 -> 81%; `eval/run.py` 0 -> 96% |
| F-15 | Fixed | `pyproject.toml` | documentation consistency test (not in this repo) | ruff 68 -> 0, `ruff format` drift 34 files -> 0, mypy 19 -> 0, rules pinned |
| F-16 | Fixed | `pyproject.toml` | `tests/test_dependencies.py` | lockfile 49 -> 37 packages |
| F-17 | Fixed | `src/turnlog.py:54` | `tests/test_turnlog.py` (9) | `results/after_batch_3/f17_sample_turns.jsonl` |
| F-18 | Fixed | `eval/e2e_terminal.py` | wording test (not in this repo) | wording test fails -> passes |
| F-19 | Disclosed (cannot be undone) | README | documentation consistency test (not in this repo) | only `audit/heldout.json` (30) is unbiased |
| F-20 | Fixed | `tests/conftest.py` | autouse guard | 31 -> 0 unclosed DB warnings; also fixed leaks in `repl()` and the eval runner |
| F-21 | Fixed | README | documentation consistency test (not in this repo) | counts instead of "9/9" |
| F-22 | Fixed | `src/session.py:19`, `src/cli.py:34` | `tests/test_session.py` | unknown `/command` answered locally; `exit.` works |
| F-23 | Fixed and re-tested | `src/schema.py:71` | `tests/test_schema.py` | malicious value in prompt True -> False, 4/4 still answered (`results/after_batch_3/f23_value_injection.txt`) |
| F-24 | Fixed (plus the optional audit) | `src/render.py`, `src/explain.py:67` | `tests/test_reasons.py`, `tests/test_answer_audit.py` (15) | `Assumed:` line; the after-the-answer audit catches about half of bare proxy questions (`results/after_batch_2b/audit_*.txt`) |
| F-25 | Disclosed | `eval/cases/taxonomy.json`, README, docs | documentation consistency test (not in this repo) | `a15` misses 3/3 in every snapshot |
| F-26 | Fixed | `src/agent.py:88` | `tests/test_prompt_rules.py` | no-join DISTINCT on own key 9 -> 6, remaining legitimate (`results/after_batch_3/f26_*`) |
| F-29 | Fixed | `eval/run.py:66` | `tests/test_eval_registry.py` (6) | a 5-question suite = 1 file + 1 line, 5 s (`results/after_batch_3/f29_demo5_suite.txt`); unknown suite name is now an error |
| F-31 | Disclosed | README Known limits | documentation consistency test (not in this repo) | 225 tables: 33,510 tokens, P50 2.96 s, about $0.0050 uncached (`results/after_batch_3/f31_generalization_live.txt`) |
| F-32 | Fixed | `eval/explain_exp.py:92` | `tests/test_explain_consistency.py` | importing no longer fires API calls |
| F-33 | **Not done** (NIT) | | | SQL pretty-printing and "first 5" wording; listed under open items |


## Before and after

Before = `results/before_fixes/SUMMARY.md`; after = `results/after_batch_3/snapshot/SUMMARY.md`.
Provider drift is real: on the same day the frozen pre-fix code scored the same as the current code on dev (8/10 both), so a raw
before/after on accuracy mixes my changes with the day. The paired same-day column is the honest comparison.

| Measure | Before | After Batch 3 | Same-day pre-fix code (paired) |
|---|---|---|---|
| Tests | 55 | 315 in the original working repository, which also had documentation-consistency and packaging tests; 297 in this repository (287 without the benchmark database) | |
| App-code coverage (`cli.py`, `eval/run.py`) | 66% (0%, 0%) | 94% (81%, 96%) | |
| ruff / `ruff format` drift / mypy | 68 / 25 files / 19 | 0 / 0 / 0 | |
| Dev /10 (3 reps) | 9 | 8 (`q_004`, `q_008`) | 8 (`q_004`, `q_008`) |
| `blind_30.json` answerable /30 | 28 | 28 (`a12`, `a15`) | 29 (`a15`) |
| `blind_15.json` answerable /14 | 14 | 13 (`v12`) | not run |
| Ambiguous questions clarified (per run of 3) | 3/3 in each of 3 runs | 2/3 in each of 3 runs | 3/3 in each of 2 paired runs |
| Scope: declined "why"/multi-step | 17/18 | 17/18 (18/18 on the previous snapshot and in a rerun with the old wording) | 17/18 |
| Scope: legitimate answered | 18/18 | 18/18 | 18/18 |
| `clarify_should` (genuinely ambiguous, asked) | 5/8 | 3/8 | 5/8 (live code with the old COUNT wording: 3/8) |
| Conversation scripts asserted checks | 47/48 and 9/11 | 49/50 and 10/11 (the miss is the same borderline "bad loans" question) | |
| False topic-switch notices / missed switches | 2/18 and 1/5 | 0/20 and 0/5 | |
| Executability, first attempt | 100% | 100% | |
| Latency P50 / P95 (30-question set) | 3.72 s / 10.01 s (a busy day) | 1.10 s / 1.84 s | |
| Cost per query | $0.00028 | $0.00018 | |
| Value injection reaches the prompt | yes | no | |
| Missing-concept questions given the right message | 0/8 | 7/8 (same-day class run) | |

**What did not improve, stated plainly**
- Dev accuracy is not higher than before (9 -> 8); the pre-fix code also scores 8 today, so this is drift, not a regression I can attribute to a change.
- Today the current code is one question lower than the pre-fix code on held-out (`a12`) and 1 question lower on ambiguous clarification (2/3 vs 3/3 per run, `x03`),
  and 2 lower on `clarify_should` (3/8 vs 5/8). Investigated afterwards (`results/after_batch_3/clarify_*.txt`): temperature 0 makes the outcome deterministic (5/5 runs agree per question),
  the old COUNT wording is not the cause, and the cause is the Batch 2 sentence in the answer rule that tells the model to state a chosen reading in `assumed`: removing it brings "bad loans" and "recent"
  back to a clarifying question (15/24 vs 9/24). The 4 questions now answered instead of asked carry an `Assumed:` line; only "average balance" is answered silently, as before.
- The after-the-answer audit catches about half of bare proxy questions (4/8 pooled, 0/3 on the fresh set).
- `a15` is a wrong-table answer every time.


## Open items

- F-33 (SQL pretty-printing, "first 5" wording).
- `clarify_should` trade-off: DECIDED, answer with a visible `Assumed:` line (design decision 31). Original question: whether "answer with a visible `Assumed:` line" is acceptable for vague words like bad/active/recent; the ablation shows a one-sentence prompt change can reverse it, at a cost not yet measured on the other suites.
- Dev `q_004` (unrounded average) and `q_008` remain misses; `a15` and `a12` on held-out.
- Not built: table retrieval above about 50 tables, dedicated-deployment test, semantic cache.
- Held-out v2 was run once (n=14); do not read it as a rate.
- Provider drift: every accuracy figure belongs to one day and one prompt version.
