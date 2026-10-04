"""End-to-end terminal checks: drives the real CLI through a pseudo-terminal.

    uv run --with pexpect python -m eval.e2e_terminal

Covers: query timeout, Ctrl-C at the prompt, Ctrl-C while a turn is running, Ctrl-D, clean exit, no tracebacks.
The mid-turn cancel check uses a slow model (kimi-k3, 5-17s per query) so there is a window to interrupt;
the first prompt waits about a second for a cursor-position reply a real terminal would give instantly, so
Ctrl-C is sent 4s after the question to be sure the turn is already running.
"""

import os
import re
import sys
import time
from pathlib import Path

import pexpect

STREAM = "/tmp/e2e_terminal_stream.txt"
results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def spawn(**env_extra: str) -> pexpect.spawn:
    child = pexpect.spawn(
        sys.executable,
        ["-m", "src.cli"],
        encoding="utf-8",
        dimensions=(40, 120),
        timeout=120,
        env=dict(os.environ, **env_extra),
    )
    child.logfile_read = open(STREAM, "a")  # noqa: SIM115 - held open for the whole session
    child.expect("ask>")
    return child


def main() -> None:
    open(STREAM, "w").close()

    # 1) query timeout: shrink the limit to 50 ms so a scan of the 1M-row table cannot finish
    c = spawn(SQL_TIMEOUT_S="0.05")
    c.sendline("Add up the amounts of every transaction dated in 1996.")
    i = c.expect(["Could not produce", "Traceback"])
    c.expect("ask>")
    check("timeout: clear message shown", i == 0 and "longer than" in c.before.replace("\r\n", " "))
    c.sendline("How many gold cards are there?")
    check("timeout: same session still answers the next question", c.expect(["88", "Traceback"]) == 0)
    c.sendcontrol("d")
    c.expect(pexpect.EOF)
    c.close()

    # 2) Ctrl-C at the prompt keeps the session; Ctrl-D exits cleanly
    c = spawn()
    c.send("half typed")
    c.sendcontrol("c")
    check("ctrl-c at prompt: session stays alive", c.expect(["Ctrl-D", "Traceback", pexpect.EOF]) == 0)
    c.expect("ask>")
    c.sendline("How many gold cards are there?")
    check("ctrl-c at prompt: next question works", c.expect(["88", "Traceback"]) == 0)
    c.sendcontrol("d")
    check("ctrl-d exits", c.expect([pexpect.EOF, "Traceback"]) == 0)
    c.close()
    check("exit status 0", c.exitstatus == 0, str(c.exitstatus))

    # 3) Ctrl-C while a turn is running (slow model gives a wide window); spinner visible; quick exit afterwards
    c = spawn(FIREWORKS_MODEL="accounts/fireworks/models/kimi-k3")
    c.sendline(
        "For every region, how many different clients own an account that has a loan? Show the region and the count."
    )
    time.sleep(4.0)
    try:
        seen = c.read_nonblocking(65536, timeout=1)
    except pexpect.TIMEOUT:
        seen = ""
    check(
        "spinner with elapsed seconds is visible while waiting",
        "Working..." in seen and re.search(r"\d+\.\ds", seen) is not None,
    )
    c.sendcontrol("c")
    check("ctrl-c mid-turn: turn cancelled", c.expect(["Cancelled", "Tables used", "Traceback"], timeout=60) == 0)
    c.expect("ask>")
    c.sendline("How many gold cards are there?")
    i = c.expect(["new topic", "88", "Traceback"], timeout=120)
    check("cancelled turn left no context (no topic-switch notice)", i == 1)
    started = time.perf_counter()  # the abandoned slow call is still running; quitting must not wait for it (F-08)
    c.sendline("quit")
    c.expect(pexpect.EOF, timeout=30)
    c.close()
    elapsed = time.perf_counter() - started
    check("exit right after cancel does not wait for the abandoned call", elapsed < 3.0, f"{elapsed:.1f}s")
    check("clean exit after cancel", c.exitstatus == 0, str(c.exitstatus))

    check("no traceback in any session", "Traceback" not in Path(STREAM).read_text(errors="replace"))
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
