"""Tests for process-termination semantics that matter for the stop path.

These spawn real child processes. The relevant behaviour only appears when a
child exits and the parent has not reaped it yet — exactly what daemon.stop()
faces with the llama-server child: the process counts as nonexistent only
after reaping, and kill_process_graceful must not treat a zombie as alive.
"""

import os
import signal
import subprocess
import sys
import time

from llama_monitor.process_monitor import (
    get_process_state,
    kill_process_graceful,
)

CHILD_FAST = (
    "import os, signal, sys, time\n"
    "signal.signal(signal.SIGTERM, lambda s, f: os._exit(0))\n"
    "print('up', flush=True)\n"
    "while True:\n"
    "    time.sleep(0.2)\n"
)

CHILD_SLOW = (
    "import os, signal, sys, time\n"
    "def slow(s, f):\n"
    "    time.sleep(30)\n"
    "    os._exit(0)\n"
    "signal.signal(signal.SIGTERM, slow)\n"
    "print('up', flush=True)\n"
    "while True:\n"
    "    time.sleep(0.2)\n"
)


def _spawn_child(code):
    """Spawn a signal-aware child; returns it unreaped (potential zombie)."""
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        text=True,
    )
    proc.stdout.readline()  # wait for 'up'
    return proc


def _cleanup(proc):
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def test_zombie_counts_as_terminated():
    """A child that received SIGTERM but was never reaped must be treated
    as gone by kill_process_graceful — quickly, not after the full timeout."""
    proc = _spawn_child(CHILD_FAST)
    try:
        os.kill(proc.pid, signal.SIGTERM)
        time.sleep(0.5)  # child is gone now but still unreaped (zombie)

        state = get_process_state(proc.pid)
        assert state.zombie is True, "expected the unreaped child to be a zombie"

        t0 = time.monotonic()
        assert kill_process_graceful(proc.pid, 10) is True
        took = time.monotonic() - t0
        # The old behaviour burned the full 10s + 5s SIGKILL-poll budget.
        assert took < 2.5, f"kill_process_graceful took {took:.1f}s on a zombie"
    finally:
        _cleanup(proc)
        proc.wait()


def test_alive_child_is_terminated_quickly():
    proc = _spawn_child(CHILD_FAST)
    try:
        t0 = time.monotonic()
        assert kill_process_graceful(proc.pid, 10) is True
        took = time.monotonic() - t0
        assert took < 2.5, f"kill_process_graceful took {took:.1f}s"
        assert proc.poll() is not None
    finally:
        _cleanup(proc)
        proc.wait()


def test_slow_child_escalates_to_sigkill_within_budget():
    """A process that ignores SIGTERM must be SIGKILLed after its timeout,
    and the escalation must finish well inside the caller's budget."""
    proc = _spawn_child(CHILD_SLOW)
    try:
        t0 = time.monotonic()
        assert kill_process_graceful(proc.pid, 1) is True
        took = time.monotonic() - t0
        assert 0.9 <= took < 8.0, f"escalation took {took:.1f}s"
        assert proc.poll() == -signal.SIGKILL
    finally:
        _cleanup(proc)
        proc.wait()


def test_unreaped_child_reported_alive_then_gone_after_reap():
    """Documents get_process_state semantics: an unreaped child looks alive
    (this is what made the old daemon restart loops restart forever) and is
    only 'dead' once its parent reaps it."""
    proc = _spawn_child(CHILD_FAST)
    try:
        os.kill(proc.pid, signal.SIGTERM)
        time.sleep(0.5)
        assert get_process_state(proc.pid).alive is True
        proc.wait()  # reap
        assert get_process_state(proc.pid).alive is False
    finally:
        _cleanup(proc)
        proc.wait()
