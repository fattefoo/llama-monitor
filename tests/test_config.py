"""Tests for llama-monitor config module."""

import json
import os
from pathlib import Path

import pytest

import llama_monitor.config as config_module


class TestLoadConfig:
    """Test config loading and validation."""

    def test_load_valid_config(self, isolated_config):
        """Test loading a valid config file."""
        cfg = config_module.load_config()
        assert cfg["enabled"] is True
        assert cfg["autostart"] is False
        assert cfg["check_interval_sec"] == 10
        assert cfg["restart_timeout_sec"] == 10
        assert cfg["daemon_port"] == 9500
        assert cfg["default_binary"] == str(Path("/bin/echo").resolve())

    def test_config_defaults(self, isolated_config):
        """Test that optional config fields have defaults."""
        cfg = config_module.load_config()
        assert cfg["autostart"] is False
        assert cfg["restart_timeout_sec"] == 10
        assert cfg["daemon_port"] == 9500

    def test_missing_config_file(self, tmp_path, monkeypatch):
        """Test that missing config file raises ConfigError."""
        config_dir = tmp_path / ".config" / "llama-monitor"
        config_dir.mkdir(parents=True)
        (config_dir / "templates").mkdir()

        monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.json")
        monkeypatch.setattr(config_module, "TEMPLATES_DIR", config_dir / "templates")

        with pytest.raises(config_module.ConfigError, match="Config file not found"):
            config_module.load_config()

    def test_missing_required_field(self, tmp_path, monkeypatch):
        """Test that missing required field raises ConfigError."""
        config_dir = tmp_path / ".config" / "llama-monitor"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "templates").mkdir()

        monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.json")
        monkeypatch.setattr(config_module, "TEMPLATES_DIR", config_dir / "templates")

        with open(config_dir / "config.json", "w") as f:
            json.dump({"enabled": True}, f)  # Missing check_interval_sec

        with pytest.raises(config_module.ConfigError, match="check_interval_sec"):
            config_module.load_config()

    def test_invalid_binary_path(self, tmp_path, monkeypatch):
        """Test that nonexistent binary raises ConfigError."""
        config_dir = tmp_path / ".config" / "llama-monitor"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "templates").mkdir()

        monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.json")
        monkeypatch.setattr(config_module, "TEMPLATES_DIR", config_dir / "templates")

        with open(config_dir / "config.json", "w") as f:
            json.dump({
                "enabled": True,
                "check_interval_sec": 30,
                "default_binary": "/nonexistent/binary",
            }, f)

        with pytest.raises(config_module.ConfigError, match="not found"):
            config_module.load_config()

    def test_invalid_check_interval(self, tmp_path, monkeypatch):
        """Test that negative/zero check_interval raises ConfigError."""
        config_dir = tmp_path / ".config" / "llama-monitor"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "templates").mkdir()

        monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.json")
        monkeypatch.setattr(config_module, "TEMPLATES_DIR", config_dir / "templates")

        with open(config_dir / "config.json", "w") as f:
            json.dump({
                "enabled": True,
                "check_interval_sec": 0,
                "default_binary": "/bin/echo",
            }, f)

        with pytest.raises(config_module.ConfigError, match="'check_interval_sec'"):
            config_module.load_config()

    def test_invalid_enabled_type(self, tmp_path, monkeypatch):
        """Test that non-boolean enabled raises ConfigError."""
        config_dir = tmp_path / ".config" / "llama-monitor"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "templates").mkdir()

        monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.json")
        monkeypatch.setattr(config_module, "TEMPLATES_DIR", config_dir / "templates")

        with open(config_dir / "config.json", "w") as f:
            json.dump({
                "enabled": "yes",  # Not a boolean
                "check_interval_sec": 30,
                "default_binary": "/bin/echo",
            }, f)

        with pytest.raises(config_module.ConfigError, match="'enabled'"):
            config_module.load_config()

    def test_custom_values(self, tmp_path, monkeypatch):
        """Test loading a config with all optional fields."""
        config_dir = tmp_path / ".config" / "llama-monitor"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "templates").mkdir()

        monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.json")
        monkeypatch.setattr(config_module, "TEMPLATES_DIR", config_dir / "templates")

        with open(config_dir / "config.json", "w") as f:
            json.dump({
                "enabled": True,
                "autostart": True,
                "check_interval_sec": 60,
                "default_binary": "/bin/echo",
                "restart_timeout_sec": 15,
                "daemon_port": 9999,
                "log_file": "~/logs/llama-monitor.log",
            }, f)

        cfg = config_module.load_config()
        assert cfg["enabled"] is True
        assert cfg["autostart"] is True
        assert cfg["check_interval_sec"] == 60
        assert cfg["restart_timeout_sec"] == 15
        assert cfg["daemon_port"] == 9999
        assert cfg["log_file"] == str(Path(os.path.expanduser("~/logs/llama-monitor.log")).resolve())

    def test_config_enabled_false(self, tmp_path, monkeypatch):
        """Test that enabled=false is properly loaded."""
        config_dir = tmp_path / ".config" / "llama-monitor"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "templates").mkdir()

        monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "config.json")
        monkeypatch.setattr(config_module, "TEMPLATES_DIR", config_dir / "templates")

        with open(config_dir / "config.json", "w") as f:
            json.dump({
                "enabled": False,
                "check_interval_sec": 30,
                "default_binary": "/bin/echo",
            }, f)

        cfg = config_module.load_config()
        assert cfg["enabled"] is False


class TestValidateTemplate:
    """Test template validation."""

    def test_valid_template(self):
        """Test validation of a valid template."""
        template = {
            "name": "Test Model",
            "model_path": "/dev/null",
            "binary": "/bin/echo",
            "port": 18080,
            "ctx_size": 4096,
        }

        config_module.validate_template(template)
        assert template["name"] == "Test Model"

    def test_missing_model_path(self):
        """Test that missing model_path raises ConfigError."""
        template = {"binary": "/bin/echo"}

        with pytest.raises(config_module.ConfigError, match="'model_path'"):
            config_module.validate_template(template)

    def test_nonexistent_model_file(self):
        """Test that nonexistent model file raises ConfigError."""
        template = {
            "name": "Fake",
            "model_path": "/nonexistent/model.gguf",
        }

        with pytest.raises(config_module.ConfigError, match="not found"):
            config_module.validate_template(template)

    def test_invalid_port(self):
        """Test that invalid port raises ConfigError."""
        template = {
            "name": "Bad Port",
            "model_path": "/dev/null",
            "binary": "/bin/echo",
            "port": 99999,
        }

        with pytest.raises(config_module.ConfigError, match="Invalid port"):
            config_module.validate_template(template)

    def test_port_zero(self):
        """Test that port 0 raises ConfigError."""
        template = {
            "name": "No Port",
            "model_path": "/dev/null",
            "binary": "/bin/echo",
            "port": 0,
        }

        with pytest.raises(config_module.ConfigError, match="Invalid port"):
            config_module.validate_template(template)

    def test_template_optional_fields(self):
        """Test that templates with minimal fields are valid."""
        template = {
            "name": "Minimal",
            "model_path": "/dev/null",
            "binary": "/bin/echo",
        }

        config_module.validate_template(template)
        assert template["name"] == "Minimal"

    def test_template_without_binary_uses_fallback(self):
        """Test that template without binary field is still valid."""
        template = {
            "name": "Fallback Binary",
            "model_path": "/dev/null",
        }

        config_module.validate_template(template)

    def test_nonexistent_template_binary(self):
        """Test that nonexistent template binary raises ConfigError."""
        template = {
            "name": "Bad Binary",
            "model_path": "/dev/null",
            "binary": "/nonexistent/binary",
        }

        with pytest.raises(config_module.ConfigError, match="not found"):
            config_module.validate_template(template)

    def test_template_without_name_field(self):
        """Test that template without name is valid after validate_template."""
        template = {
            "model_path": "/dev/null",
            "binary": "/bin/echo",
        }

        config_module.validate_template(template)
        # validate_template doesn't set name, load_template does


class TestListTemplates:
    """Test template listing."""

    def test_list_empty_directory(self, isolated_template_dir):
        """Test listing templates in an empty directory."""
        templates_dir, _ = isolated_template_dir
        names = config_module.list_template_names()
        assert names == []

    def test_list_templates(self, isolated_template_dir):
        """Test listing available templates."""
        templates_dir, _ = isolated_template_dir

        for name in ["model_a", "model_b", "model_c"]:
            with open(templates_dir / f"{name}.json", "w") as f:
                json.dump({
                    "name": name,
                    "model_path": "/dev/null",
                    "binary": "/bin/echo",
                }, f)

        names = config_module.list_template_names()
        assert names == sorted(["model_a", "model_b", "model_c"])

    def test_ignore_non_json_files(self, isolated_template_dir):
        """Test that non-JSON files are ignored."""
        templates_dir, _ = isolated_template_dir

        with open(templates_dir / "valid.json", "w") as f:
            json.dump({
                "name": "valid",
                "model_path": "/dev/null",
                "binary": "/bin/echo",
            }, f)

        with open(templates_dir / "invalid.txt", "w") as f:
            f.write("not a template")

        names = config_module.list_template_names()
        assert names == ["valid"]
        assert "invalid" not in names


class TestLoadTemplate:
    """Test individual template loading."""

    def test_load_valid_template(self, isolated_template_dir):
        """Test loading a valid template by name."""
        templates_dir, _ = isolated_template_dir

        with open(templates_dir / "test_model.json", "w") as f:
            json.dump({
                "name": "Test Model",
                "model_path": "/dev/null",
                "binary": "/bin/echo",
                "port": 18080,
            }, f)

        template = config_module.load_template("test_model")
        assert template["name"] == "Test Model"
        assert template["model_path"] == "/dev/null"
        assert template["binary"] == str(Path("/bin/echo").resolve())
        assert template["port"] == 18080

    def test_load_nonexistent_template(self, isolated_template_dir):
        """Test that loading nonexistent template raises ConfigError."""
        templates_dir, _ = isolated_template_dir

        with pytest.raises(config_module.ConfigError, match="not found"):
            config_module.load_template("nonexistent")


class TestSaveActiveTemplate:
    """Test saving active_template to config."""

    def _create_template(self, config_dir, name):
        """Helper to create a valid template."""
        templates_dir = config_dir / "templates"
        with open(templates_dir / f"{name}.json", "w") as f:
            json.dump({
                "name": name,
                "model_path": "/dev/null",
                "binary": "/bin/echo",
            }, f)

    def test_save_valid_template(self, isolated_config, tmp_path, monkeypatch):
        """Test saving a valid active_template to config."""
        # Create template first
        self._create_template(isolated_config, "test_model")
        config_module.save_active_template("test_model")
        
        cfg = config_module.load_config()
        assert cfg["active_template"] == "test_model"

    def test_save_nonexistent_template(self, isolated_config):
        """Test that saving a nonexistent template raises ConfigError."""
        with pytest.raises(config_module.ConfigError, match="not found"):
            config_module.save_active_template("nonexistent")

    def test_save_preserves_other_fields(self, isolated_config, tmp_path, monkeypatch):
        """Test that save preserves all other config fields."""
        self._create_template(isolated_config, "test_model")
        config_module.save_active_template("test_model")
        
        cfg = config_module.load_config()
        assert cfg["enabled"] is True
        assert cfg["check_interval_sec"] == 10
        assert cfg["daemon_port"] == 9500


class TestActiveTemplateValidation:
    """Test active_template validation in config loading."""

    def _create_template(self, config_dir, name):
        """Helper to create a valid template."""
        templates_dir = config_dir / "templates"
        with open(templates_dir / f"{name}.json", "w") as f:
            json.dump({
                "name": name,
                "model_path": "/dev/null",
                "binary": "/bin/echo",
            }, f)

    def test_load_config_with_active_template(self, isolated_config):
        """Test that config loads with active_template set."""
        self._create_template(isolated_config, "test_model")
        config_module.save_active_template("test_model")
        
        cfg = config_module.load_config()
        assert cfg["active_template"] == "test_model"

    def test_load_config_without_active_template(self, isolated_config):
        """Test that config loads with empty active_template when not set."""
        config_file = isolated_config / "config.json"
        
        with open(config_file, "w") as f:
            json.dump({
                "enabled": True,
                "check_interval_sec": 30,
                "default_binary": str(Path("/bin/echo").resolve()),
            }, f)
        
        cfg = config_module.load_config()
        assert cfg["active_template"] == ""

    def test_load_config_invalid_active_template(self, isolated_config):
        """Test that loading config with nonexistent active_template raises."""
        config_file = isolated_config / "config.json"
        
        with open(config_file, "w") as f:
            json.dump({
                "enabled": True,
                "check_interval_sec": 30,
                "default_binary": str(Path("/bin/echo").resolve()),
                "active_template": "nonexistent_template",
            }, f)
        
        with pytest.raises(config_module.ConfigError, match="not found"):
            config_module.load_config()

    def test_save_active_template_to_existing_config(self, isolated_config):
        """Test that save_active_template writes to existing config."""
        self._create_template(isolated_config, "test_model")
        
        config_module.save_active_template("test_model")
        
        config_file = isolated_config / "config.json"
        with open(config_file, "r") as f:
            saved = json.load(f)
        
        assert saved["active_template"] == "test_model"
        assert saved["enabled"] is True
        assert saved["check_interval_sec"] == 10

    def test_save_active_template_clears_existing(self, isolated_config):
        """Test that save_active_template replaces existing value."""
        self._create_template(isolated_config, "test_model")
        self._create_template(isolated_config, "another_model")
        
        config_module.save_active_template("test_model")
        config_module.save_active_template("another_model")
        
        cfg = config_module.load_config()
        assert cfg["active_template"] == "another_model"


class TestPathExpansion:
    """Test path expansion in config."""

    def test_expand_tilde(self):
        """Test that ~ is expanded in paths."""
        expanded = config_module._expand_path("~/test/path")
        assert expanded == str(Path(os.path.expanduser("~/test/path")).resolve())
        assert not expanded.startswith("~/")

    def test_absolute_path_unchanged(self):
        """Test that absolute paths are unchanged."""
        expanded = config_module._expand_path("/absolute/path")
        assert expanded == "/absolute/path"

    def test_relative_path(self, tmp_path, monkeypatch):
        """Test that relative paths are resolved."""
        monkeypatch.chdir(tmp_path)
        expanded = config_module._expand_path("relative/path")
        assert str(tmp_path / "relative" / "path") == expanded
