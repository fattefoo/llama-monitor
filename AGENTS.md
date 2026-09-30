# llama-monitor — Agent Instructions

## Project

Single-package Python daemon (Python >=3.10, httpx). Entry point: `llama_monitor.main:main` → dispatched via CLI subcommands.

## Developer commands

```bash
cd ~/llama-monitor && source .venv/bin/activate
pytest -v                        # run all tests
pytest -v tests/test_config.py   # single file
pytest -v -k test_minimal       # single test
```

No lint/typecheck/formatter config exists — tests are the only verification gate.

## Architecture

```
llama-monitor (Python daemon, runs as root via sudo or systemd)
├── CLI (cli.py): start/stop/restart/status/list-templates/create-base/create-from/models/set/reload-template/logs
├── Daemon (daemon.py): process lifecycle, health-check loop, PID management
├── Config (config.py): JSON config in ~/.config/llama-monitor/, per-template JSON files in templates/
├── Health (health.py): async HTTP check against /health endpoint
├── Process monitor (process_monitor.py): zombie detection, graceful kill (SIGTERM→wait→SIGKILL)
├── API (api.py): HTTP server on port 9500 — /api/v1/status, /api/v1/templates, /api/v1/restart, /api/v1/set-template, /api/v1/shutdown
└── Hermes plugin (hermes-llama-monitor/): slash commands /llama list, /llama set, /llama status
```

Config layout: `~/.config/llama-monitor/config.json` + `~/.config/llama-monitor/templates/*.json`.

## Gotchas

- **CLI commands `start`/`stop`/`restart` auto-elevate via `subprocess` + `sudo`** (cli.py:16-23). The `_require_sudo()` re-executes the *full* command line with `sudo`. The shebang in cli.py (`#!/home/n4rf/llama-monitor/.venv/bin/python3`) is hardcoded — if you copy the CLI to a new environment, update the shebang or use `python3 -m llama_monitor.main`.
- **Home directory resolution**: when running via sudo, `~` points to `/root`. The code uses `SUDO_USER` env var and scans `/home/*` for `.config/llama-monitor` to find the real user's config. This happens in both `cli.py` and `daemon.py` — they each have their own `_get_home_dir()`. If you modify the sudo/home logic, update both copies.
- **State machine**: process states flow STOPPED→STARTING→RUNNING/CHECKING→KILLING→STARTING. The `DaemonState` dataclass in daemon.py is a singleton accessed via `get_global_state()`. Never instantiate it directly.
- **Daemon runs in foreground** (for systemd `Type=simple`). The `_daemonize()` double-fork was removed — the process must stay alive. Run `llama-monitor start &` or `nohup` for background CLI usage.
- **External llama-server adoption**: if a llama-server is already running on the template port when daemon starts, it "adopts" it (sets `server_owned=False`). The daemon won't kill external servers unless `force_kill=True` (e.g. health check failure).
- **Health checks**: use `await_health()` which creates its own event loop. The daemon's shutdown handler interrupts `_current_loop` via `call_soon_threadsafe()`. Don't change the event loop creation in health.py without also updating the signal handler in daemon.py.
- **Systemd service**: `scripts/llama-monitor.service` has `WorkingDirectory=/home/n4rf/.config/llama-monitor` hardcoded. The `enable-autostart` CLI command generates a new service file with per-user paths — these don't match. Pick one pattern and reconcile.
- **Template args**: `--penalty-present` maps to config key `penalty_present` (cli.py:165 uses `--penalty-penalty`). Template boolean flags (jinja, metrics) are passed without values; other params are key-value pairs.
- **Tests use monkeypatched module-level `CONFIG_DIR`/`CONFIG_FILE`/`TEMPLATES_DIR`** on the `config_module` (test_config.py). Test fixtures (`conftest.py`) create isolated tmpdir hierarchies. Tests don't require a running daemon or llama-server.
