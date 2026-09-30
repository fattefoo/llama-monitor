"""Test configuration and fixtures for llama-monitor."""

import json
from pathlib import Path

import pytest

import llama_monitor.config as config_module


def _setup_paths(tmp_path, monkeypatch):
    """Common fixture: set up isolated config paths and return config_dir."""
    config_dir = tmp_path / ".config" / "llama-monitor"
    config_dir.mkdir(parents=True)
    templates_dir = config_dir / "templates"
    templates_dir.mkdir()
    
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.json")
    monkeypatch.setattr(config_module, "TEMPLATES_DIR", templates_dir)
    
    return config_dir


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Set up isolated config paths + write a valid config. Returns config_dir."""
    config_dir = _setup_paths(tmp_path, monkeypatch)
    
    with open(config_dir / "config.json", "w") as f:
        json.dump({
            "enabled": True,
            "check_interval_sec": 10,
            "default_binary": "/bin/echo",
        }, f)
    
    return config_dir


@pytest.fixture
def isolated_template_dir(tmp_path, monkeypatch):
    """Set up isolated template dir. Returns (templates_dir, config_dir)."""
    config_dir = _setup_paths(tmp_path, monkeypatch)
    return config_dir / "templates", config_dir


@pytest.fixture
def fake_binary(tmp_path):
    """Create a minimal executable script."""
    bin_path = tmp_path / "fake-llama-server"
    bin_path.write_text("#!/bin/sh\necho fake llama-server\n", mode="w")
    bin_path.chmod(0o755)
    return str(bin_path)
