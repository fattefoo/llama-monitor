"""Tests for the stop-path wait budgets and the daemon child reaping.

The daemon's shutdown handler stops the llama-server FIRST; every caller
that might SIGKILL the daemon must budget for the full graceful teardown,
otherwise the llama-server is orphaned.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import llama_monitor.config as config_module
from llama_monitor.api import _shutdown_watchdog_seconds
from llama_monitor.cli import _restart_timeout_sec
from llama_monitor.daemon import Daemon, DaemonState, get_global_state, set_global_state
from llama_monitor.process_monitor import get_process_state


CHILD_FAST = (
    "import os, signal, sys, time\n"
    "signal.signal(signal.SIGTERM, lambda s, f: os._exit(0))\n"
    "print('up', flush=True)\n"
    "while True:\n"
    "    time.sleep(0.2)\n"
)


@pytest.fixture
def server_child():
    """A fast-exiting child process (stand-in for llama-server)."""
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD_FAST],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        text=True,
    )
    proc.stdout.readline()
    yield proc
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def test_watchdog_default_covers_graceful_stop(isolated_config):
    assert _shutdown_watchdog_seconds() == 23.0  # 10 default + 13


def test_watchdog_is_config_driven(isolated_config):
    with open(config_module.CONFIG_FILE, "w") as f:
        f.write('{"enabled": true, "check_interval_sec": 10,'
                ' "default_binary": "/bin/echo", "restart_timeout_sec": 3}')
    assert _shutdown_watchdog_seconds() == 16.0  # 3 + 13


def test_watchdog_falls_back_when_config_unreadable(isolated_config, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_FILE",
                        Path(isolated_config) / "missing.json")
    assert _shutdown_watchdog_seconds() == 23.0


def test_cli_restart_timeout_default(isolated_config):
    assert _restart_timeout_sec() == 10


def test_cli_restart_timeout_reads_config(isolated_config):
    with open(config_module.CONFIG_FILE, "w") as f:
        f.write('{"enabled": true, "check_interval_sec": 10,'
                ' "default_binary": "/bin/echo", "restart_timeout_sec": 5}')
    assert _restart_timeout_sec() == 5


def test_cli_restart_timeout_survives_bad_config(isolated_config, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_FILE",
                        Path(isolated_config) / "missing.json")
    assert _restart_timeout_sec() == 10


def test_daemon_stop_reaps_child(server_child, isolated_config):
    """daemon.stop() must wait() the spawned child: otherwise it persists
    as a zombie and pid-based checks keep reporting the server alive."""
    config = {
        "restart_timeout_sec": 10,
        "check_interval_sec": 1,
        "default_binary": "/bin/echo",
    }
    daemon = Daemon(config)
    set_global_state(DaemonState())
    state = get_global_state()
    state.pid = server_child.pid
    state.server_owned = True
    daemon.proc = server_child  # the child belongs to us; stop() must reap it

    daemon.stop()
    assert state.pid is None
    assert server_child.poll() is not None
    assert get_process_state(server_child.pid).alive is False
    set_global_state(None)
