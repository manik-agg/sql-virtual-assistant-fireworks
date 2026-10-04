# Design decisions

How the agent is designed, why, what was measured, and what would change the decision. The log is chronological by topic; each
row has the options considered, the choice, the reason, a trigger for revisiting it, and the evidence (files under `results/`).
Finding ids such as F-07 refer to the self-audit in [self-audit.md](self-audit.md).

## Goal

A command-line agent that turns a natural-language question into correct, executed, read-only SQL against a database it has
not seen, and shows why the answer can be trusted: the tables used, the SQL that ran, the assumptions made, and a short grounded
explanation. It is the "plug in a database with only a connection string" experience, built to be fast and cheap enough to be a
platform feature.

## Targets

| Dimension | Target | Where it is measured |
|---|---|---|
| Correctness | strict result-set match with reference SQL; a clear lift over a naive one-line prompt | `eval/run.py`, [evaluation.md](evaluation.md) |
| Executability | 100% after at most one retry, at least 90% on the first attempt | same |
| Scope | "why" and multi-step questions get one fixed message and no SQL; no legitimate question is declined | independent behaviour set |
| Latency | P50 under 3 s end to end (to rendered results; the async explanation is excluded), P95 under 6 s | `uv run cli -v`, eval summaries |
| Cost | at most $0.003 per query (about 30,000 queries a day at the intended scale) | token counts times documented prices |
| Safety | only a single SELECT ever runs; enforced in code, not in the prompt | [tests/test_sqlguard.py](../tests/test_sqlguard.py) |

## Behaviour that shaped the design

- **Scope guard.** A "why", causal or multi-step question gets exactly: "This question requires multi-step analysis. At this point
  in time I can only answer questions that need single-step SQL." It is a status field in the same structured output as the SQL,
  not a separate call, so it costs no latency. Every refusal message is fixed text in code; the model only picks the category.
- **Transparency.** Tables used are parsed from the SQL with sqlglot (zero LLM cost, cannot be hallucinated). The interpretation is
  generated asynchronously after the result renders, grounded only in the executed SQL and result columns, and cancelled if the
  user types the next question. Reasoning is shown only with `-v`.
- **Session context.** A sliding window of 5 turn records (question, SQL, tables, columns, row count, tiny results; never full
  result sets). Each question is labelled `follow_up`, `same_tables_new_question` or `new_topic` in the same call. A topic change
  shows a non-blocking notice and `/keep` restores the previous context; `/clear` wipes it.
- **Caching.** In scope: session context reuse and provider prompt-prefix caching (static schema first, dynamic content last). Out of
  scope, design only: a cross-question semantic cache. Two questions can be close in embedding space and differ in a detail that
  changes the answer (year, top-N, filter value), and a wrong cache hit costs more trust than a miss costs time. A safe design would
  cache the SQL, never the result, re-execute every time, require a very high similarity and a matching schema hash, and verify the
  match before reuse.
- **Assumptions.** Read-only SQLite; the agent asks one clarifying question with 2-3 options only when the ambiguity would change the
  result, otherwise it picks the likeliest reading and shows it.

## Non-goals

No web UI; no database other than SQLite (the design does not preclude one); no multi-step or "why" analysis; no write queries; no
chain-of-thought by default; no semantic cache; no fine-tuning run (a plan is in the README).

## Decision log
Model: gpt-oss-120b unless stated. Dev = a 10-question development set (not redistributed), held-out = 22 questions I wrote, 3 repeats unless stated.
The baseline is the same open model on a naive prompt, not a proprietary frontier model. Prices: docs.fireworks.ai/serverless/pricing (Standard tier, 2026-09-30).
All results below were produced AFTER the max_tokens fix (see row 20); the final-code runs are in `results/after_batch_3/`.

| # | Decision | Options considered | Chose | Why | Revisit if | Evidence |
|---|---|---|---|---|---|---|
| 1 | Evaluation | strict tuples / normalized multiset + gold alternates / LLM judge | Strict multiset by column position (strings exact, one float tolerance, integers exact), ordered only if gold has ORDER BY, reordered or extra columns are misses unless the case lists `alternates` (F-07), no LLM judge in the score, plus 22 held-out questions | Cheap, no judge noise, the reference answers are fixed | Open-ended answers need a judge | `eval/compare.py`, 7 unit tests, gold self-check on all dev, held-out and shop cases |
| 2 | Baseline | raw prompt / raw + DDL | Report both, so the lift is not a strawman | The baseline prompt has no schema | A proprietary frontier model is available for the comparison | Raw prompt 0/10 and 0/22; raw + DDL 1/10 and 6/22 |
| 3 | Schema context | DDL / +descriptions / +profiled values / retrieval | Full schema + descriptions + profiled low-cardinality values, as a static prompt prefix | Each step measured alone | More than ~50 tables (retrieval, design only) | DDL only 1/10, 7/22. +descriptions 6/10, 16/22. +values 8/10, 21/22 (re-measured on the final code with the strict scorer, `results/after_batch_3/readme_refresh/`) |
| 4 | Failure handling | gate only / error retry / reflection / tool loop | sqlglot read-only gate + execute with timeout and row cap + one error retry (none after a query timeout or when the retry repeats the same SQL); model requests time out after 15 s with one retry | Meets executability at no P50 cost | Silent wrong answers (the dominant error) need reflection or a bigger eval | 100% executable, first attempt 100% on dev and held-out, so the retry never fired. Retry logic covered by unit tests (14 agent tests, 9 SQL-gate tests) |
| 5 | Structured output | grammar `response_format` / function call / prompt-only | Flat JSON schema via `response_format` (now also `reason`, `assumed`, `concept`), lenient parse, one repair call, truncation detected; unusable output is `status=error`, never `clarify` | One call carries status, turn_type, SQL, clarification | Model rejects json_schema | 14 agent tests: parse, repair, retry, truncation, API failures |
| 6 | Model | gpt-oss-120b, deepseek-v4p1-flash, glm-5p3, glm-5p3-flash, minimax-m3, kimi-k3, inkling | gpt-oss-120b, medium reasoning | Ties for best accuracy at lowest cost. kimi-k2p6, glm-5p2 and gpt-oss-20b returned 404 on the account used | Cost/latency change or a model beats it on a larger eval | Dev: gpt-oss, deepseek, kimi-k3, inkling 9/10; glm-5p3 9/10 but held-out 20/22; minimax-m3 8/10. Sequential latency P50: gpt-oss 1.3-1.7s, deepseek 2.1-2.5s, inkling 1.1-1.9s. Est. cost/query: gpt-oss ~$0.0002, deepseek ~$0.0002, glm-5p3 ~$0.0017, kimi-k3 ~$0.0036 (over budget) |
| 7 | Reasoning effort | default / low / medium | medium | Low is 2x faster but loses two questions deterministically | A larger eval shows low is safe | Low: P50 0.7-0.9s, dev 8/10 (q_008 wrong join 3/3), held-out 21/22 (h_07 3/3). Medium: dev 9/10, held-out 21-22/22, P50 1.3-1.7s (measured before Batch 1-3; the final code gives dev 8/10, held-out 21/22, P50 about 1.0-1.1s) |
| 8 | q_004 | strict vs rounding-tolerant compare | Keep strict; label as presentation / bad gold | Gold applies ROUND(,2) unasked; do not loosen the metric to raise the score | A user wants rounded output | Every model tested returns the unrounded (correct) average |
| 9 | Explanation | async grounded (chosen) vs inline ~30 tokens | Async, gpt-oss-120b, plain text, cancel on Enter | Async: ~$0.00007/query, 0.5s P50, off the critical path. Inline: P50 1.76s vs 1.14s, same accuracy (30/32 both) | Explanation must appear with the result | `eval/explain_exp.py`; 0/32 deterministic problems; 10/10 dev explanations read by hand |
| 10 | Session topic switch | model label only / sqlglot overlap only / either signal / both signals | Show the notice when the model says `new_topic` OR the new SQL's tables (ignoring FK hub tables, and counting a hard-coded value from a recent result as overlap) do not overlap the window. If the window has no tables (empty, or only a clarification) skip both signals, so answering a clarifying question never triggers it | A missed switch is silent and can carry a stale filter into a new question; a false one is visible and `/keep` undoes it, so favour recall. Overlap alone had missed a real switch ("clients per district" after "accounts per district"), and the clarification case showed a false one | Same-table false switches exceed about 10%: then require BOTH signals | c1..c11 same-day A/B (`results/after_batch_2/f03_convos_frozen.txt` -> `f03_convos_live.txt`): false switches 2/17 -> 0/17, missed switches 1/5 -> 0/5, asserted checks 47/50 -> 50/50; 7 session tests (the old test that asserted the opposite was rewritten) |
| 11 | Regenerate on disagreement | re-ask without context when label and overlap disagree / do not | Do not (removed) | It threw away the context a real follow-up needed | Contamination shows up in the switch tests | Bug found in testing; reproduced, fixed, unit-tested |
| 12 | Caching | prefix cache now / semantic cache | Prefix cache: static schema first, dynamic last. Semantic cache: design only | Cached tokens 2228 of 2385 on the second call | Per-tenant traffic too low to keep prefixes warm | CLI -v output |
| 13 | Scope guard | separate call / status field | status + `reason` fields in the same call; one fixed refusal text per reason printed by code (multi-step, not-in-database, read-only); unknown `/commands` answered locally | No extra latency; exact wording guaranteed | Borderline set grows | Independent set (`eval/independent/behavior_cases.json`): scope 17/18 declined and 18/18 legitimate answered; clarify 5/8 of ambiguous questions asked about, 10/10 of clear ones not asked (`results/before_fixes/behavior_transcript.txt`); write requests declined; write SQL from the model never executes (tested) |
| 14 | API error handling | let it raise / friendly message | Friendly one-line message; session unchanged; REPL never dies on one bad turn | Error handling should be explicit and tested | | Bad key, 429 and network error each tested |
| 15 | Description-free databases | nothing / LLM-drafted descriptions / human data dictionary | Ask for a data dictionary; do not rely on LLM-drafted ones | Financial DB without its CSVs: dev 6/10, held-out 18/22 (final code). LLM-drafted descriptions gave no gain (6/10, 17/22) because the missing meanings (A11 = average salary, loan status codes) are not in the data | A user can supply docs | `nodesc`, `autodesc` presets; drafted descriptions honestly mark A5-A16 "unclear" |
| 16 | Second schema | none / synthetic shop DB | Built one (12 questions, coded status column, reserved-word table, many-to-many) | A single schema would not show the design generalises | | DDL only 9/12; +values 12/12; +one line of docs 11/12 (the miss is gold ambiguity: model excluded cancelled/refunded purchases); re-measured on the final code |
| 17 | Follow-up labels | strict 3-way label | Keep, prompt tightened | | | turn_type 17/17 (was 8/11) |
| 18 | Prices | guessed / documented | Documented Standard-tier prices in `src/llm.py` | Guessed cached price was 5x too high | Prices change | `gpt-oss-120b`: $0.15 in / $0.015 cached / $0.60 out per 1M |
| 19 | Terminal rendering | rich Live / prompt_toolkit patch_stdout | prompt_toolkit patch_stdout + rich | Explanation prints above the prompt without garbling it | | Driven through a real pseudo-terminal: all line endings CRLF, explanation cancelled on next input, clean exit |
| 21 | Ctrl-C and query timeout | leave defaults / handle both | Ctrl-C mid-turn cancels and discards the turn (generation counter, so a late thread cannot change context); Ctrl-C at the prompt keeps the session; 10s query limit with a plain message | Error handling should be explicit and tested | | `eval/e2e_terminal.py` 10/10, plus unit tests. Test-harness lesson: the pseudo-terminal waits ~1s for a cursor reply, so my first mid-turn Ctrl-C arrived before the turn started |
| 23 | Latency under provider load | trust the morning numbers / re-measure | Re-measured: median 2.8-3.6s later the same day, with identical output tokens (262 mean) and a trivial call 2-3x slower | The 3s target depends on endpoint load, so the README says so | A firm SLA is needed: test a dedicated deployment or the priority tier | Same code, same-day re-measure |
| 24 | F-07 comparator strictness | keep lenient / strict plus `alternates` | Strict: columns by position, exact strings, one float tolerance (abs 1e-6, rel 1e-9; the requested rel 1e-6 would accept 1000000.4 for 1000000.0, which the scorer test must reject), ints exact; extra/reordered columns are misses reported as `shape_differs` | The lenient rule passed 5 of 27 adversarial cases that are wrong | A gold case has several valid shapes: add `alternates` | Scorer tests false passes 5 -> 0 of 27; re-scoring 432 saved outputs changed 0 verdicts (`results/after_batch_3/f07_*`) |
| 25 | F-29 suite registry | edit three functions per suite / one registry | `SUITES` in `eval/run.py` plus `uv run evals`; a suite is a case file and one line | Adding a suite meant editing `load_sql_cases`, `gold_check` and `main` | More than one database per run is needed | A 5-question suite added and run in 5 s (`f29_demo5_suite.txt`); 7 tests |
| 26 | F-17 turn log | no log / JSONL file | `logs/turns.jsonl`, one line per turn, no result values, no reasoning, key scrubbed, `--no-log`; ignored by git | Debugging and cost review need a record; result values may be private | Logs must go to a service | 9 tests, real-CLI sample in `f17_sample_turns.jsonl` |
| 27 | F-26 COUNT(DISTINCT) wording | keep / tighten | "COUNT(*); COUNT(DISTINCT col) only when a join can multiply rows; never on the primary key of the counted table" (one line, schema block unchanged at the start) | The old rule produced needless DISTINCT on keys | The model starts under-counting after joins | No-join DISTINCT on own key 9 -> 6 (remaining 2 legitimate); accuracy identical same-day with old wording (`f26_*`) |
| 28 | F-14/F-15 quality gates | ad hoc / pinned | Coverage scope and ruff rules pinned in `pyproject.toml`; non-application scripts are excluded from coverage and say why | A user-level ruff config added rules and made counts differ | A rule is added | 297 tests in this repository, app-code coverage 94%, ruff 0, mypy 0 (`results/after_batch_3/snapshot/`) |
| 29 | F-31 large-schema limit | claim "any database" / document | Document 225 tables: 33,510 prompt tokens, P50 2.96s, about $0.0050 uncached vs $0.0005 cached; retrieval above about 50 tables is designed, not built | Uncached cost exceeds the $0.003 budget | A customer has more than 50 tables | `f31_generalization_live.txt` |
| 30 | Write-verb check and after-the-answer audit | see `docs/fix-logs/FIX_LOG_batch2b.md` | Cue-based write-verb check (12/12 fresh writes caught, 3/12 look-alike false flags) and an optional `--no-audit` check that catches about half of bare proxy questions | | | `results/after_batch_2b/` |
| 31 | Clarify vs assumed | ask more often / answer with a visible `Assumed:` line | Answer and show the reading for vague words; ask only when no reasonable default exists. Prompt unchanged | Chosen after the ablation: asking costs a turn, and a stated assumption is checkable | Users expect more clarification, or silent answers (1 of 8) matter more | Asked 5/8 -> 3/8, assumed 4/8, silent 1/8; ablation 9/24 vs 15/24 clarified without the `assumed` sentence (`results/after_batch_3/clarify_*.txt`) |
| 20 | Process lesson | | Every agent call had been capped at 600 tokens (reasoning counts toward it), which truncated reasoning models mid-JSON and looked like flaky accuracy | Found when a "malformed output" reproduced 6/6 with 600 completion tokens each | | Fixed (3000), truncation now detected and reported, regression test added, ALL evals re-run. |

### Failure taxonomy (misses of the shipped config)
Labels live in `eval/cases/taxonomy.json` and are printed next to every miss.
- `q_004` bad gold / presentation (unrounded average, correct value). Every model.
- `h_07` wrong filter value (`type='VYBER' AND operation='VYBER'`). Intermittent earlier (22/22 or 21/22); passes in the final-code run.
- `h_08` extra column (right region plus its average; a miss only because the scorer is strict). `q_008` tie order (same rows, two tied districts swapped; a scorer artifact).
- Shop `p_06`, `p_12`: ambiguous question / gold (only when status codes are documented).
- Without descriptions: wrong filter values (status codes), hallucinated column (`A10` for salary), wrongly declined single-step questions.

### Open risks
- The 22 held-out questions were written by me and are mostly easy; do not quote 22/22 as general accuracy.
- Without a data dictionary accuracy drops (dev 6/10). This is the main production risk.
- P95 is about 2.1s sequentially today (3.2-3.7s earlier in the project) and reached 5-6s under parallel load.
- Overfitting: two prompt rules (COUNT(DISTINCT id) wording, "dates are ISO text") were written after I read the dev set's reference SQL, and the first 22 held-out questions were used while iterating. Only the 30-question set in `eval/independent/blind_30.json` (written without seeing agent output, never tuned against) is unbiased.
- Known wrong-table answer (`a15`) stays; real error rate is not zero.
- Silent proxy answers: when the schema only approximates what was asked, the model flags it with `Assumed:` only some of the time (3 of 9 questions refused or flagged; five prompt wordings tried, none better without a regression elsewhere).
- Prompt sensitivity: small wording changes flip borderline answers in both directions; totals were equal before and after Batch 2 (37 correct per run) while single questions moved. Every figure belongs to one prompt version.
- All accuracy figures are small samples on one schema family (n=10, 22 or 30). The scope/clarify figures come from an independent set (18 + 8 + 10 + 10 questions); the conversation scripts are self-written.
- Prices for glm-5p3-flash and inkling are not on the pricing page; their costs are overestimates.
