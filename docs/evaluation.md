# Evaluation

How the agent is measured, what the numbers are, and how far to trust them. Every figure links to a saved output under
[`results/`](../results/README.md). Re-run with `uv run evals` (needs a Fireworks key).

## Method

- **Strict scorer** ([`eval/compare.py`](../eval/compare.py)). A result matches the reference only if columns match by position,
  strings match exactly (case and whitespace count), integers match exactly, floats match within one tolerance (absolute 1e-6,
  relative 1e-9), and rows match as a multiset, in order only when the reference SQL has a top-level `ORDER BY`. A reordered or extra
  column is a miss, reported as `shape_differs`; a case may list `alternates` when another shape is also right. There is no LLM judge.
  27 adversarial scorer cases are judged correctly (the first version of the scorer passed 5 of them wrongly), and re-scoring 432
  saved agent outputs under the strict scorer changed 0 verdicts ([`f07_scorer_tests.txt`](../results/after_batch_3/f07_scorer_tests.txt),
  [`f07_rescore.txt`](../results/after_batch_3/f07_rescore.txt)).
- **Question sets.**

  | Set | Size | Purpose |
  |---|---|---|
  | Development set | 10 | provided with the original exercise; not redistributed here. Used while building |
  | `eval/cases/heldout_sql.jsonl` | 22 | written by me with the schema in view and used while iterating, so **not** unbiased |
  | [`eval/independent/blind_30.json`](../eval/independent/blind_30.json) | 33 (30 answerable + 3 ambiguous) | written without seeing any agent output and never tuned against: the unbiased measure |
  | [`eval/independent/blind_15.json`](../eval/independent/blind_15.json) | 15 (14 answerable) | a second blind set, run once |
  | [`eval/independent/behavior_cases.json`](../eval/independent/behavior_cases.json) | 18 + 8 + 10 + 10 | scope decline, plain answers, ambiguity, no-clarify controls |
  | [`eval/cases/showcase_10.jsonl`](../eval/cases/showcase_10.jsonl) | 10 (tiers 1 to 3) | written fresh and frozen before the first run (hash recorded in the output), reference SQL verified, no wording shared with any other set (a test checks); run once, results in [`answers.json`](../results/showcase/answers.json) |
  | `eval/cases/{scope,clarify,conversations}.jsonl` | 9 / 4 / 22 turns | self-written; the conversation scripts assert topic-switch behaviour |
  | `eval/data/shop.sqlite` + `shop_sql.jsonl` | 12 | a second, synthetic schema (coded status column, reserved-word table, many-to-many) |
  | [`eval/independent/*cases*.json`](../eval/independent/) | various | write attempts, not-in-database concepts, proxy concepts, borderline "why" pairs |

  Two blind questions (`a19`, `a22`) reuse short natural phrases from development questions ("clients live in districts where the",
  "accounts are located in the Prague region"); this is ordinary phrasing for the same schema, but it is disclosed.
- **Ladder of presets.** `b0, b1, s_ddl, s_desc, s_values, retry, nodesc, autodesc` each change one thing, so a gain is attributable
  to that change. `b0` is the naive prompt "Convert this question to SQL: {question}"; `b1` adds the DDL.
- **Repeats and drift.** Each configuration runs 3 times. The model is deterministic at temperature 0 inside a day, so repeats mostly
  agree; across days the provider's behaviour drifts. Same-day paired runs against a frozen copy of earlier code are used whenever a
  change is claimed to help or hurt ([`ab_paired/`](../results/after_batch_3/ab_paired_summary.txt)).

## Results

gpt-oss-120b, medium reasoning, final code, strict scorer
([`ladder.txt`](../results/after_batch_3/readme_refresh/ladder.txt), [`shop.txt`](../results/after_batch_3/readme_refresh/shop.txt)).

| Step | Dev /10 | Held-out /22 |
|---|---|---|
| Naive prompt (no schema) | 0 | 0 |
| Naive prompt + DDL | 1 | 6 |
| Structured call + DDL only | 1 | 7 |
| + column descriptions | 6 | 16 |
| + profiled values | 8 | 21 |
| + error retry (never needed) | 8 | 21 |
| No column descriptions | 6 | 18 |
| LLM-drafted descriptions | 6 | 17 |

| Independent / other sets | Result |
|---|---|
| Blind set, 30 answerable questions, 3 repeats | 28/30 in every repeat of the latest snapshot ([`SUMMARY.md`](../results/after_batch_3/snapshot/SUMMARY.md)) |
| Blind set, 3 ambiguous questions (expect a clarifying question) | 2 of 3 asked in each repeat of the latest snapshot |
| Second blind set, 14 answerable questions, run once | 13/14 |
| Shop database, DDL only / + profiled values / + one documentation line | 9/12, 12/12, 11/12 |
| Scope: "why" and multi-step questions declined with the fixed message | 17/18 (18/18 on another run of the same code) |
| Scope: legitimate questions answered, including 10 that use "why", "reason" or "cause" as ordinary data words | 18/18 |
| Genuinely ambiguous questions | 3 of 8 asked, 4 answered with a visible `Assumed:` line, 1 answered silently |
| Clear questions not asked about | 10/10 |
| Conversation scripts: asserted checks, false topic-switch notices, missed switches | 49/50 and 10/11; 0/20; 0/5 |
| Showcase set of 10 new questions, one run | 10/10 strict matches, 10/10 executed on the first attempt, median 1.3 s ([`answers.json`](../results/showcase/answers.json)) |
| Executability, first attempt | 100% on all question sets |
| Latency, one query at a time, quiet endpoint | P50 about 1.0-1.1 s, P95 about 1.8-2.1 s |
| Cost per query | about $0.00018 (cached prefix) |
| Tests | 297 offline tests with the database present (287 without), ruff 0, mypy 0, 94% coverage of application code |

### The showcase set

Ten questions on the benchmark database, tiers 1 (single table) to 3 (multi-table joins, grouping, ranking, an intersection of two
tables), written by me after the blind sets and frozen before the first run: the cases file's SHA-256 is stored in the output. They
include a text column holding numbers (`district.A4`, inhabitants), a coded status (`loan.status = 'B'`), a reserved-word table
(`"order"`), a date taken from an ISO text column, and a percentage with rounding. The reference SQL was checked to return real,
non-zero answers and to have no ties under `ORDER BY`. A test confirms that the strict scorer rejects a perturbed reference for all
10, so the set can fail.

Result: 10/10. Treat it as a demonstration that the method works end to end (question, SQL, rows, verdict, all saved), not as an
accuracy estimate: the author knew the schema and earlier failure modes when writing the questions, n is 10, and the model's
behaviour drifts by a question or two between days. The blind set remains the headline figure.

## Known misses

Every miss is labelled in [`eval/cases/taxonomy.json`](../eval/cases/taxonomy.json).

- `a15` (blind set): a wrong-table answer every run. It filters transactions on a service-fee code instead of reading the statement
  frequency on the account. The real error rate is not zero.
- `a12` (blind set): missed in the latest snapshot, passed in some earlier ones.
- A development question where the reference rounds an average without being asked: the agent returns the correct, unrounded value.
  The metric is kept strict rather than loosened.
- A development question that returns the same rows as the reference with two tied groups in a different order: a scorer artifact of
  an ordered comparison against a reference with no tie-breaker.
- `h_08`: the right answer plus an extra column, a miss only under the strict scorer.

## How far to trust these numbers

- Samples are small (10 to 30 questions) and come from one schema family; three repeats are not independent samples. Read them as
  direction, not rates.
- Two prompt rules (the `COUNT(DISTINCT)` wording and an ISO-date rule) were written after reading the reference SQL of the
  development set, and the 22-question set was used while iterating. Only the blind sets are unbiased; the ISO-date rule was later
  replaced by per-column date-format notes.
- Latency tracks provider load: later on the same day the same code's median was about 3 s with identical output.
- Accuracy moves by one or two questions between days with no code change. After a round of fixes the same-day pre-fix code scored
  the same on the development set, so improvements from the fixes in this repository are mostly in safety, honesty and
  testability, not raw accuracy ([self-audit.md](self-audit.md)).
