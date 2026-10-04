# Results

Saved outputs behind the numbers in the README and docs. Each folder is a measurement point; the same measurement script was
used at each (`SUMMARY.md` reduces a snapshot to one comparable page).

| Folder | What it is |
|---|---|
| `before_fixes/` | the system before the self-audit fixes: baseline snapshot |
| `after_batch_1/` | evidence for the first batch of fixes (explanation checker, documentation facts) |
| `after_batch_2/` | second batch: topic-switch rule, timeouts and retries, date formats, refusal reasons, assumptions; includes same-day A/B runs against the earlier code and the prompt-selection experiments |
| `after_batch_2b/` | the write-intent word check and the after-the-answer assumption audit |
| `showcase/` | `answers.json`: 10 new questions, generated SQL, answer, reference SQL, expected and returned rows, verdict; `run_output.txt` is the console output |
| `after_batch_3/` | the final snapshot (`snapshot/SUMMARY.md`), strict-scorer re-scoring, suite registry, turn log, prompt wording, large-schema measurement, the clarify-versus-assume investigation, and the final ladder (`readme_refresh/`) |

[`showcase/`](showcase/answers.json) holds the fresh 10-question evaluation, one run, per-question evidence.

Start with [`after_batch_3/snapshot/SUMMARY.md`](after_batch_3/snapshot/SUMMARY.md) and
[`after_batch_3/readme_refresh/ladder.txt`](after_batch_3/readme_refresh/ladder.txt).

Raw per-question run files (JSON) are not included; they contain the text of a development question set that is not redistributed.
Summaries derived from them are. These are real outputs from one project, so they include earlier-code runs, failed experiments
and borderline results on purpose. Dates and file names such as `eval_agent_...` come from the harness.
