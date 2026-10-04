"""F-08 / F-10: bounded waits, no wasted retries, a visible spinner, and a fast exit after Ctrl-C."""

import subprocess
import sys
import textwrap
import time

import pytest
from conftest import answer

from src import llm
from src.agent import Agent, AgentConfig

RUNAWAY = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT COUNT(*) FROM r"


def make(conn):
    return Agent(conn, AgentConfig(desc_dir=None), client=object())


# ---- request timeout --------------------------------------------------------------------------------------------------
def test_sdk_requests_time_out_after_15_seconds_with_one_retry(monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    for client in (llm.get_client(), llm.get_async_client()):
        assert client.timeout == llm.REQUEST_TIMEOUT_S == 15.0
        assert client.max_retries == llm.MAX_RETRIES == 1


# ---- no retry on a query timeout --------------------------------------------------------------------------------------
def test_a_query_timeout_is_not_retried(conn, fake_llm, monkeypatch):
    monkeypatch.setattr("src.db.TIMEOUT_S", 0.1)
    fake = fake_llm([answer(RUNAWAY), answer(RUNAWAY)])
    r = make(conn).turn("count forever")
    assert r.result is None and "longer than" in r.error
    assert len(fake.calls) == 1 and r.retries == 0


# ---- no retry when the retried SQL is identical -----------------------------------------------------------------------
def test_identical_retry_sql_is_not_executed_twice(conn, fake_llm, monkeypatch):
    import src.agent as agent_mod

    executed = []
    real = agent_mod.run_sql
    monkeypatch.setattr(agent_mod, "run_sql", lambda c, sql: executed.append(sql) or real(c, sql))
    fake = fake_llm(
        [answer("SELECT nope FROM loan"), answer("select  nope   from loan;")]
    )  # same query, different spelling
    r = make(conn).turn("q")
    assert r.result is None and "no such column" in r.error
    assert len(executed) == 1 and len(fake.calls) == 2  # the repeat is detected before it runs again
    assert "same" in r.detail.lower()


def test_a_different_retry_sql_still_runs(conn, fake_llm):
    fake_llm([answer("SELECT nope FROM loan"), answer("SELECT COUNT(*) FROM loan")])
    r = make(conn).turn("q")
    assert r.result.rows == [(2,)] and r.retries == 1


# ---- spinner ------------------------------------------------------------------------------------------------------------
def test_spinner_text_shows_elapsed_seconds():
    from src.render import ElapsedSpinner

    now = [100.0]
    spinner = ElapsedSpinner("Working...", clock=lambda: now[0])
    assert spinner.text() == "Working... 0.0s"
    now[0] = 102.46
    assert spinner.text() == "Working... 2.5s"


# ---- exit speed ---------------------------------------------------------------------------------------------------------
def test_run_in_daemon_thread_returns_results_and_propagates_errors():
    import asyncio

    from src.cli import run_in_daemon_thread

    async def main():
        loop = asyncio.get_running_loop()
        assert await run_in_daemon_thread(loop, lambda x: x * 2, 21) == 42
        with pytest.raises(ValueError, match="boom"):
            await run_in_daemon_thread(loop, lambda: (_ for _ in ()).throw(ValueError("boom")))

    asyncio.run(main())


def test_an_abandoned_worker_does_not_delay_process_exit():
    """Regression: asyncio.to_thread workers are joined at exit, so quitting after Ctrl-C waited for the model call."""
    script = textwrap.dedent("""
        import asyncio, time
        from src.cli import run_in_daemon_thread
        async def main():
            loop = asyncio.get_running_loop()
            fut = run_in_daemon_thread(loop, time.sleep, 20)      # an abandoned, slow "model call"
            await asyncio.sleep(0.2)
            fut.cancel()
        asyncio.run(main())
    """)
    start = time.perf_counter()
    subprocess.run([sys.executable, "-c", script], check=True, timeout=15)
    assert time.perf_counter() - start < 5
