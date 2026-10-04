# SQL Virtual Assistant Agent with Fireworks

**Ask your database in plain English. Every answer shows its work.**

The SQL Virtual Assistant is a command-line agent, built on the [Fireworks](https://fireworks.ai) platform, that answers questions about any SQLite
database and shows its work: the SQL that ran, the tables it used, the assumptions it made, a short grounded explanation, and
measured evidence that the whole thing can be trusted. It turns a natural-language question into **read-only SQL**, handles
follow-ups, refuses what it should refuse, and ships with a strict evaluation harness and a written record of every design decision
and every measured result. It is a hands-on project in building an **agentic system on Fireworks**.

```text
ask> How many customers are on the pro tier?
Tables used: customer
SELECT COUNT(*) AS pro_customers_count FROM customer WHERE tier = 'pro';
 pro_customers_count
 79
Interpretation: The query scans the customer table and filters rows where the tier column equals 'pro'. ...

ask> And how many of those signed up in 2022?          # follow-up: resolved from the previous turn
SELECT COUNT(*) AS pro_customers_2022 FROM customer WHERE tier = 'pro' AND signup_date BETWEEN '2022-01-01' AND '2022-12-31';
 24

ask> Why did signups drop?                             # multi-step analysis is declined, no SQL is generated
This question requires multi-step analysis. At this point in time I can only answer questions that need single-step SQL.

ask> Delete all purchases                              # writes are refused in code, never executed
I can only run read-only queries, so I can't change or delete data.
```

## What this project is for

It exercises the parts of the Fireworks platform that matter when an agent has to be fast, cheap and trustworthy: grammar-constrained
structured output (`response_format` JSON schema), reasoning-effort control, provider prompt-prefix caching, async side calls that
can be cancelled, token and cost accounting against the documented serverless prices, and a comparison of six Fireworks-hosted
open models.

The goal was to find out how far an **open model on a serverless endpoint** (gpt-oss-120b on Fireworks) can go as the engine of a
"point it at a database and ask" feature, against three requirements that usually pull against each other:

| Requirement | Target | Result |
|---|---|---|
| **Trust**: no invented tables or columns, no wrong joins, no SQL that fails to run; the user can see why to believe it | strict result-set match against reference SQL | 0/10 for a naive one-line prompt to 8/10 on a 10-question development set; 28/30 on a 30-question blind set; 100% of generated SQL executed on the first attempt |
| **Latency** | P50 under 3 s end to end | about 1.0-1.1 s median, about 2 s at P95, one query at a time |
| **Cost** | at most $0.003 per query | about $0.0002 per query |

Safety is enforced in code rather than in the prompt: a parser rejects anything but a single `SELECT`, the SQLite connection is
read-only with a time limit and a row cap, and the "tables used" line is parsed from the SQL, not written by the model.

The headline lesson: **missing context was the main problem, not the model.** With no schema the model invents tables (0/10).
Adding the table definitions gets 1/10, the column descriptions 6/10, and the actual values of low-cardinality columns 8/10.

Full numbers, the method, and the honest list of what fails are in [docs/evaluation.md](docs/evaluation.md).

## Quick start

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and a [Fireworks](https://fireworks.ai) API key.

```bash
uv sync
cp .env.example .env            # then put your key in .env: FIREWORKS_API_KEY=...
uv run cli --db eval/data/shop.sqlite --descriptions eval/data/shop_descriptions
```

That uses the bundled synthetic shop database. Ask "How many customers are on the pro tier?" then "Which country has the most
customers?". Type `exit` to quit. Point it at any SQLite file with `--db path/to.sqlite` (or `sqlite:///path/to.sqlite`); if a
`database_description/` folder of column-description CSVs sits next to the file, or you pass `--descriptions DIR`, it is added to the
prompt. Without descriptions the CLI prints a warning, because accuracy drops.

To reproduce the evaluation on the benchmark database, download it once (346 MB from the BIRD project, git-ignored):

```bash
./scripts/download_data.sh      # data/financial.sqlite + data/database_description/
uv run cli                      # defaults to data/financial.sqlite
```

| Flag | Effect |
|---|---|
| `-v` | timings, tokens, cost and the model's reasoning |
| `--no-log` | do not write `logs/turns.jsonl` (one JSON line per turn: question, SQL, timings, cost; never result values or keys) |
| `--no-audit` | skip the after-the-answer check for assumptions (saves about $0.0001 per query) |

Environment: `FIREWORKS_API_KEY` (required), `FIREWORKS_MODEL` (default gpt-oss-120b), `FIREWORKS_EXPLAIN_MODEL`, `SQL_TIMEOUT_S`.

## Behaviour

| You do | It does |
|---|---|
| ask a question | SQL, tables used, results, then a one-paragraph explanation printed above the prompt |
| ask a follow-up | resolves it from the last 5 turns (question, SQL, tables, columns, row count, tiny results) |
| change topic | shows a notice and restarts context; `/keep` restores it, `/clear` wipes it |
| ask "why" or a multi-step question | one fixed message, no SQL |
| ask to change data | the read-only message; a write never executes |
| ask for something the database does not have | "I couldn't find data for X in this database", plus the closest tables or columns |
| use a vague word ("recent", "bad") | answers and shows its reading in a yellow `Assumed:` line |
| ask for a related concept the SQL only approximates | a separate check after the answer adds `Assumed (checked after the answer): ...` (catches about half of such cases) |
| press Ctrl-C | at the prompt: keeps the session; while a query runs (spinner with elapsed time): cancels it |

## How it works

```text
question ─► one structured LLM call ─► status / reason / turn_type / sql / assumed ─► sqlglot gate ─► read-only SQLite
              (static schema prefix,       out_of_scope → fixed message from code        (single SELECT)   (10 s limit,
               cached by the provider)     clarify      → one question, 2-3 options                         1000 rows)
                                           answer       → SQL runs, one retry on error
                                                      └─► async explanation + assumption audit (cancelled by the next input)
```

- **One structured call per turn**, not an agent loop. A tool-calling loop adds 0.7-1.5 s per round trip and breaks the latency
  target on a small schema; one call with the whole schema in a cacheable prefix is faster and cheaper. A loop only pays off above
  roughly 50 tables, which is design-only here.
- **The schema prompt is built from the database**: DDL, column descriptions, profiled values for low-cardinality columns, and
  per-column date-format notes (ISO, `YYYYMMDD` text or integer, epoch seconds or milliseconds, day-first or month-first). Values from
  the data are sanitised before they enter the prompt.
- **The model only picks a category.** Every refusal message is fixed text in code, so wording can never drift and a wrong category
  can wrongly decline but cannot run a write.
- **Bounded waits.** Each model request times out after 15 s with one retry; a query is stopped after 10 s and never retried; quitting
  after Ctrl-C returns in under a second.

Design rationale, alternatives considered and the evidence for each choice: [docs/design-decisions.md](docs/design-decisions.md).

## Evaluation

**The evaluation shows its work too.** [`results/showcase/answers.json`](results/showcase/answers.json) is a fresh set of 10 questions
(tiers 1 to 3) whose reference SQL was written and verified *before* the first run and hashed so a later edit would show. For every
question the file records the SQL the agent generated, a grounded one-sentence answer, the reference SQL, the rows the reference
expects, the rows the agent returned, and the strict verdict. One run: 10/10 matched, all executed on the first attempt, median 1.3 s.
The set is small and written by me, so read it as proof the method works end to end, not as an accuracy rate: the 30-question blind
set in [docs/evaluation.md](docs/evaluation.md) is the headline number, and a test checks that a deliberately wrong answer is
rejected for all 10 questions, so a perfect score could have been a failure.

`uv run evals` runs a registry of suites (adding one is a case file plus one line in `eval/run.py`):

```bash
uv run evals --gold-check                                  # every reference SQL executes and matches itself (no key needed)
uv run python -m eval.make_showcase_answers                # regenerates results/showcase/answers.json (needs the database and a key)
uv run evals --config b0 --suite heldout,blind30 --repeats 3      # the naive baseline prompt
uv run evals --config retry --suite heldout,blind30 --repeats 3   # the shipped agent
uv run evals --config retry --suite scope,clarify,conv --repeats 3
```

The scorer is strict (columns by position, exact strings and integers, one float tolerance, row order only when the reference has
`ORDER BY`). Presets `b0, b1, s_ddl, s_desc, s_values, retry, nodesc, autodesc` change one thing each, so each gain is attributable.
[docs/evaluation.md](docs/evaluation.md) lists the question sets (including two written blind), the results, and how far to trust them.

## Engineering

- **297 offline tests** (a fake model client, no key needed; 287 run without the benchmark database), 94% coverage of application
  code, `ruff` and `mypy` clean. A real pseudo-terminal test (`eval/e2e_terminal.py`) covers query timeouts and Ctrl-C.
- **A self-audit.** After it worked, I audited my own work as a skeptical reviewer: 33 findings, fixed test first with a before and
  after measurement for each, including a scorer that passed 5 of 27 wrong answers and a token cap that had been silently truncating
  reasoning models. Several fixes made the system safer and more honest without raising accuracy, and the report says so:
  [docs/self-audit.md](docs/self-audit.md).
- **Saved evidence.** Every claim links to an output under [results/](results/README.md).

## Limits

- Small samples (10 to 33 questions) on one schema family; read the numbers as direction, not rates. Accuracy moves by a question or
  two between days with no code change.
- A known wrong-table answer remains, and silent "proxy" answers (a nearby column standing in for the concept asked) are caught only
  about half the time.
- The description CSVs drove most of the gain; without them the same system scores 6/10 on the development set.
- Latency tracks provider load: later on the same day the median was about 3 s.
- On a 225-table schema the prompt is 33.5k tokens, about $0.0050 per query uncached. Table retrieval for large schemas is designed,
  not built. No semantic cache and no fine-tuning (a plan follows).

### Fine-tuning plan

Only after the context fixes, which gave the largest gains. Log every question, the SQL, and whether the user accepted, re-ran or
edited it (the turn log is the start of that); keep verified pairs; compare a small fine-tuned open model with the prompted 120B
model on the same evaluation set. It wins if it matches accuracy at lower latency and cost, and loses if the data comes from one
schema and does not transfer.

## Repository layout

```text
src/        the agent: cli, agent (prompt + structured call), schema (profiling), sqlguard, session, explain, render, llm, turnlog
tests/      297 offline tests
eval/       harness (run.py, compare.py), question sets (cases/, independent/), demo database (data/), pty test
results/    saved outputs behind every number, by snapshot (see results/README.md)
docs/       design-decisions.md, evaluation.md, self-audit.md, fix-logs/
scripts/    download_data.sh
```

## License and data

MIT (see [LICENSE](LICENSE)). The benchmark database is not redistributed; see [NOTICE.md](NOTICE.md).
This is an independent project; it is not affiliated with or endorsed by Fireworks AI.
