"""Configuration reader and validator for llama-monitor."""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def _get_home_dir() -> str:
    """Get the home directory, handling sudo and root correctly.
    
    Priority:
    1. SUDO_USER env var (when running via sudo)
    2. If HOME=/root, scan /home/* for .config/llama-monitor
    3. Fall back to ~
    """
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        return os.path.expanduser(f"~{sudo_user}")
    
    # Running as root — look for llama-monitor config in /home
    if os.path.expanduser("~") == "/root":
        for user_dir in Path("/home").iterdir():
            config_candidate = user_dir / ".config" / "llama-monitor"
            if config_candidate.is_dir():
                return str(user_dir)
    
    return os.path.expanduser("~")


CONFIG_DIR = Path(os.path.join(_get_home_dir(), ".config", "llama-monitor"))
CONFIG_FILE = CONFIG_DIR / "config.json"
TEMPLATES_DIR = CONFIG_DIR / "templates"


class ConfigError(Exception):
    """Raised when configuration is invalid."""


def _expand_path(path_str: str) -> str:
    """Expand ~ and relative paths in config values."""
    return str(Path(os.path.expanduser(path_str)).resolve())


def load_config() -> Dict[str, Any]:
    """Load and validate the main configuration file.
    
    Returns the parsed config dict with validated/expanded values.
    """
    if not CONFIG_FILE.exists():
        raise ConfigError(f"Config file not found: {CONFIG_FILE}")
    
    with open(CONFIG_FILE, "r") as f:
        raw = json.load(f)
    
    # Validate required fields
    required = ["enabled", "check_interval_sec", "default_binary"]
    for key in required:
        if key not in raw:
            raise ConfigError(f"Missing required config field: {key}")
    
    # Validate types
    if not isinstance(raw["enabled"], bool):
        raise ConfigError("'enabled' must be a boolean")
    if not isinstance(raw["check_interval_sec"], (int, float)) or raw["check_interval_sec"] < 1:
        raise ConfigError("'check_interval_sec' must be a positive number")
    if not raw["default_binary"]:
        raise ConfigError("'default_binary' must be a non-empty string")
    
    # Validate active_template if present
    active_template = raw.get("active_template")
    if active_template:
        if not isinstance(active_template, str) or not active_template:
            raise ConfigError("'active_template' must be a non-empty string")
        template_file = TEMPLATES_DIR / f"{active_template}.json"
        if not template_file.exists():
            raise ConfigError(f"Active template '{active_template}' not found in templates directory")
        raw["active_template"] = active_template
    
    # Expand paths
    config = {
        "enabled": raw["enabled"],
        "autostart": raw.get("autostart", False),
        "check_interval_sec": int(raw["check_interval_sec"]),
        "default_binary": _expand_path(raw["default_binary"]),
        "restart_timeout_sec": int(raw.get("restart_timeout_sec", 10)),
        "daemon_port": int(raw.get("daemon_port", 9500)),
        "active_template": raw.get("active_template", ""),
        "llm_stopped": bool(raw.get("llm_stopped", False)),
    }
    
    # Optional log_file
    log_file = raw.get("log_file")
    if log_file:
        config["log_file"] = _expand_path(log_file)
    
    # Validate binary exists
    if not Path(config["default_binary"]).exists():
        raise ConfigError(f"Default binary not found: {config['default_binary']}")
    
    # Validate templates directory
    if not TEMPLATES_DIR.exists():
        raise ConfigError(f"Templates directory not found: {TEMPLATES_DIR}")
    
    return config


def validate_template(template: Dict[str, Any]) -> None:
    """Validate a single template dict.
    
    Raises ConfigError if validation fails.
    """
    if "model_path" not in template:
        raise ConfigError("Template missing required field: 'model_path'")
    
    # Expand model_path
    template["model_path"] = _expand_path(template["model_path"])
    
    if not Path(template["model_path"]).exists():
        raise ConfigError(f"Model file not found: {template['model_path']}")
    
    # Validate optional binary
    if "binary" in template:
        template["binary"] = _expand_path(template["binary"])
        if not Path(template["binary"]).exists():
            raise ConfigError(f"Template binary not found: {template['binary']}")
    
    # Validate port
    if "port" in template:
        port = template["port"]
        if not isinstance(port, int) or not (1 <= port <= 65535):
            raise ConfigError(f"Invalid port in template: {port}")
    
    # Validate bind address
    if "bind" in template:
        bind = template["bind"]
        if not isinstance(bind, str) or not bind:
            raise ConfigError(f"Invalid bind address in template: {bind}")
    
    # Optional: ctx_size, n_gpu_layers, cache_type_k, cache_type_v, chat_format


def load_template(template_name: str) -> Dict[str, Any]:
    """Load a single template by filename (without .json extension)."""
    template_file = TEMPLATES_DIR / f"{template_name}.json"
    if not template_file.exists():
        raise ConfigError(f"Template not found: {template_name}")
    
    with open(template_file, "r") as f:
        template = json.load(f)
    
    validate_template(template)
    template["name"] = template.get("name", template_name)
    return template


def list_template_names() -> List[str]:
    """Return a sorted list of available template names (without .json)."""
    if not TEMPLATES_DIR.exists():
        return []
    
    names = []
    for f in TEMPLATES_DIR.iterdir():
        if f.is_file() and f.suffix == ".json":
            names.append(f.stem)
    
    return sorted(names)


def save_active_template(template_name: str) -> None:
    """Update active_template in config.json."""
    if not CONFIG_FILE.exists():
        raise ConfigError(f"Config file not found: {CONFIG_FILE}")
    
    with open(CONFIG_FILE, "r") as f:
        raw = json.load(f)
    
    # Validate the template exists before saving
    template_file = TEMPLATES_DIR / f"{template_name}.json"
    if not template_file.exists():
        raise ConfigError(f"Template '{template_name}' not found in templates directory")
    
    raw["active_template"] = template_name
    
    with open(CONFIG_FILE, "w") as f:
        json.dump(raw, f, indent=4)


def save_llm_stopped(value: bool) -> None:
    """Persist the llm-stop state so it survives a daemon restart."""
    if not CONFIG_FILE.exists():
        raise ConfigError(f"Config file not found: {CONFIG_FILE}")
    
    with open(CONFIG_FILE, "r") as f:
        raw = json.load(f)
    
    raw["llm_stopped"] = bool(value)
    
    with open(CONFIG_FILE, "w") as f:
        json.dump(raw, f, indent=4)
