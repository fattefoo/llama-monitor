"""Tests for the llm-stop / llm-start feature (daemon-driven server stop/start)."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import llama_monitor.config as config_module
import llama_monitor.daemon as daemon_module
import llama_monitor.health as health_module
import llama_monitor.process_monitor as pm_module
from llama_monitor.daemon import Daemon, DaemonState, _build_llama_args


# ---------------------------------------------------------------------------
# config.py: load/save of the llm_stopped flag
# ---------------------------------------------------------------------------

class TestLlmStoppedConfig:
    """Test the llm_stopped config field."""

    def test_llm_stopped_defaults_false(self, isolated_config):
        """llm_stopped defaults to False when absent."""
        cfg = config_module.load_config()
        assert cfg["llm_stopped"] is False

    def test_llm_stopped_loaded(self, isolated_config, tmp_path, monkeypatch):
        """A stored llm_stopped: true is loaded."""
        config_dir = isolated_config
        with open(config_dir / "config.json", "w") as f:
            json.dump({
                "enabled": True,
                "check_interval_sec": 30,
                "default_binary": str(Path("/bin/echo").resolve()),
                "llm_stopped": True,
            }, f)
        cfg = config_module.load_config()
        assert cfg["llm_stopped"] is True

    def test_save_llm_stopped_true(self, isolated_config, tmp_path, monkeypatch):
        """save_llm_stopped(True) persists and reloads."""
        config_module.save_llm_stopped(True)
        cfg = config_module.load_config()
        assert cfg["llm_stopped"] is True
        # Also persisted to disk
        with open(isolated_config / "config.json") as f:
            assert json.load(f)["llm_stopped"] is True

    def test_save_llm_stopped_false(self, isolated_config, tmp_path, monkeypatch):
        """save_llm_stopped(False) persists and reloads."""
        config_module.save_llm_stopped(True)
        config_module.save_llm_stopped(False)
        cfg = config_module.load_config()
        assert cfg["llm_stopped"] is False

    def test_save_llm_stopped_preserves_other_fields(self, isolated_config, tmp_path, monkeypatch):
        """save_llm_stopped does not clobber other config fields."""
        config_module.save_llm_stopped(True)
        with open(isolated_config / "config.json") as f:
            saved = json.load(f)
        assert saved["llm_stopped"] is True
        assert saved["check_interval_sec"] == 10
        assert saved["enabled"] is True

    def test_save_llm_stopped_missing_config(self, tmp_path, monkeypatch):
        """save_llm_stopped raises ConfigError when config file missing."""
        config_dir = tmp_path / ".config" / "llama-monitor"
        config_dir.mkdir(parents=True)
        templates_dir = config_dir / "templates"
        templates_dir.mkdir()
        monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.json")
        monkeypatch.setattr(config_module, "TEMPLATES_DIR", templates_dir)

        with pytest.raises(config_module.ConfigError, match="Config file not found"):
            config_module.save_llm_stopped(True)


# ---------------------------------------------------------------------------
# DaemonState: new fields exist
# ---------------------------------------------------------------------------

class TestLlmStoppedState:
    """Test that DaemonState carries the new fields."""

    def test_state_has_llm_fields(self):
        state = DaemonState()
        assert state.llm_stopped is False
        assert isinstance(state.llm_stop_event, threading.Event)
        assert isinstance(state.llm_start_event, threading.Event)


# ---------------------------------------------------------------------------
# Daemon.check_and_recover with llm_stopped
# ---------------------------------------------------------------------------

class TestCheckAndRecoverLlmStopped:
    """Test the Daemon's stop/start behavior when llm_stopped is set."""

    def _install_state(self, daemon, *, llm_stopped, pid=None, process_state="running"):
        """Wire up a global state singleton for the daemon instance."""
        from llama_monitor.daemon import set_global_state
        state = DaemonState()
        state.llm_stopped = llm_stopped
        state.pid = pid
        state.process_state = process_state
        state.active_template = "test_model"
        daemon.llm_stop_event = state.llm_stop_event
        daemon.llm_start_event = state.llm_start_event
        set_global_state(state)
        return state

    def _setup_daemon(self):
        """Return a Daemon with a minimal config."""
        daemon = Daemon({"restart_timeout_sec": 10})
        return daemon

    def test_llm_stopped_pid_none_keeps_stopped(self, monkeypatch):
        """When llm_stopped and no server running, daemon stays stopped."""
        monkeypatch.setattr(daemon_module, "find_llama_server_by_port", lambda port: None)
        monkeypatch.setattr(config_module, "load_template", lambda name: SimpleNamespace(name=name, port=8080))
        monkeypatch.setattr(health_module, "await_health", lambda url: False)

        daemon = self._setup_daemon()
        state = self._install_state(daemon, llm_stopped=True, pid=None)
        daemon.check_and_recover()

        assert state.process_state == "stopped"
        assert state.pid is None

    def test_llm_stopped_pid_set_stops_running(self, monkeypatch):
        """When llm_stopped and server running, daemon stops it (once)."""
        monkeypatch.setattr(daemon_module, "find_llama_server_by_port", lambda port: None)
        monkeypatch.setattr(config_module, "load_template", lambda name: SimpleNamespace(name=name, port=8080))
        monkeypatch.setattr(health_module, "await_health", lambda url: False)

        daemon = self._setup_daemon()
        state = self._install_state(daemon, llm_stopped=True, pid=4242, process_state="running")

        stopped = {"count": 0}

        def fake_stop(*args, **kwargs):
            stopped["count"] += 1
            state.pid = None
            state.process_state = "stopped"
            return True

        daemon.stop = fake_stop
        daemon.check_and_recover()

        assert stopped["count"] == 1
        assert state.process_state == "stopped"

    def test_llm_stopped_pid_set_already_stopped_noop(self, monkeypatch):
        """When llm_stopped and already stopped, daemon does nothing."""
        monkeypatch.setattr(daemon_module, "find_llama_server_by_port", lambda port: None)
        monkeypatch.setattr(config_module, "load_template", lambda name: SimpleNamespace(name=name, port=8080))
        monkeypatch.setattr(health_module, "await_health", lambda url: False)

        daemon = self._setup_daemon()
        state = self._install_state(daemon, llm_stopped=True, pid=None, process_state="stopped")

        stopped = {"count": 0}

        def fake_stop(*args, **kwargs):
            stopped["count"] += 1
            return True

        daemon.stop = fake_stop
        daemon.check_and_recover()

        assert stopped["count"] == 0  # nothing to stop

    def test_llm_not_stopped_still_checks_health(self, monkeypatch):
        """When llm_stopped is False, normal health check still runs."""
        monkeypatch.setattr(daemon_module, "find_llama_server_by_port", lambda port: None)
        monkeypatch.setattr(config_module, "load_template", lambda name: SimpleNamespace(name=name, port=8080))
        monkeypatch.setattr(health_module, "await_health", lambda url: True)
        monkeypatch.setattr(pm_module, "get_process_state", lambda pid: SimpleNamespace(alive=True, zombie=False, status="running"))
        monkeypatch.setattr(pm_module, "is_process_zombie", lambda pid: False)

        daemon = self._setup_daemon()
        state = self._install_state(daemon, llm_stopped=False, pid=4242)
        daemon.check_and_recover()

        assert state.process_state == "running"
        assert state.health_ok is True

    def test_llm_stopped_still_checks_health_when_false(self, monkeypatch):
        """Regression: llm_stopped False does not early-return in check_and_recover."""
        monkeypatch.setattr(daemon_module, "find_llama_server_by_port", lambda port: None)
        monkeypatch.setattr(config_module, "load_template", lambda name: SimpleNamespace(name=name, port=8080))
        monkeypatch.setattr(health_module, "await_health", lambda url: True)
        monkeypatch.setattr(pm_module, "get_process_state", lambda pid: SimpleNamespace(alive=True, zombie=False, status="running"))
        monkeypatch.setattr(pm_module, "is_process_zombie", lambda pid: False)

        daemon = self._setup_daemon()
        state = self._install_state(daemon, llm_stopped=False, pid=7777)
        daemon.check_and_recover()

        assert state.health_ok is True
