# llama-monitor

llama-server process monitor with health checking and systemd integration.

Monitors a llama-server instance, performs HTTP health checks, detects zombie processes,
and automatically restarts when necessary. Supports multiple model templates and is
configurable per-template binary, port, and llama.cpp parameters.

## Architecture

```
llama-monitor (Python daemon)
├── CLI: start/stop/restart/status/list-templates
├── systemd: autostart, restart=always, journalctl logging
├── HTTP API (port 9500): /api/v1/status, /api/v1/templates, /api/v1/set-template, /api/v1/restart, /api/v1/llm-stop, /api/v1/llm-start, /api/v1/shutdown
└── Hermes integration: /llama list, /llama set, /llama status (separate plugin)
```

## Installation

```bash
cd ~/llama-monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Install system-wide
sudo cp scripts/llama-monitor.service /etc/systemd/system/
sudo cp llama-monitor /usr/local/bin/llama-monitor
```

Or use pipx:

```bash
pipx install .
```

## Configuration

Configuration is stored in `~/.config/llama-monitor/`:

```
~/.config/llama-monitor/
├── config.json              # Main configuration
├── templates/               # Model templates (one JSON per model)
│   ├── qwen36_35b.json
│   └── mistral_7b.json
└── llama-server.pid         # Runtime PID file (auto-managed)
```

### config.json

```json
{
    "enabled": true,
    "autostart": false,
    "check_interval_sec": 30,
    "default_binary": "/usr/local/bin/llama-server",
    "restart_timeout_sec": 10,
    "daemon_port": 9500
}
```

### Template schema

Each template file defines one model configuration:

```json
{
    "name": "Qwen 3.6 35B A3B",
    "model_path": "/home/n4rf/.lmstudio/models/qwen3.6-35b.Q4_K_M.gguf",
    "binary": "/usr/local/bin/llama-server",
    "bind": "127.0.0.1",
    "port": 8080,
    "ctx_size": 8192,
    "n_gpu_layers": 35,
    "cache_type_k": "turbo4",
    "cache_type_v": "turbo3"
}
```

**Required fields:**
- `model_path` — Absolute path to the GGUF model file

**Optional fields (defaults in config):**
- `binary` — llama-server binary path (default: `default_binary` from config)
- `bind` — Bind address (default: `127.0.0.1`)
- `port` — Port for the llama-server (default: `8080`)
- `ctx_size` — Context window size
- `n_gpu_layers` — Layers to offload to GPU (-1 = all)
- `cache_type_k`, `cache_type_v` — kv cache type (e.g. `turbo4`, `turbo3`)
- `chat_format` — Chat template format

All optional fields are passed as `--flag value` arguments to the llama-server.

## Usage

### Daemon

```bash
# Start daemon in foreground
llama-monitor start

# Stop daemon
llama-monitor stop

# Restart daemon
llama-monitor restart

# Show status
llama-monitor status

# List available templates
llama-monitor list-templates
```

### Autostart (systemd)

```bash
# Enable autostart (creates systemd unit, starts service)
sudo llama-monitor --enable-autostart

# Disable autostart
sudo llama-monitor --disable-autostart
```

### llm-stop / llm-start

`llm-stop` and `llm-start` give manual control over the running llama-server
*without* fighting the daemon's auto-restart. Both are daemon-driven: the
CLI posts to the daemon's HTTP API and the daemon's main loop does the work.

```bash
# Stop the llama-server and KEEP it stopped. The daemon will NOT restart it
# (whereas a health-check failure normally brings it back).
llama-monitor llm-stop

# Bring the llama-server back up and let the daemon resume normal monitoring.
llama-monitor llm-start
```

Behavior:

- **`llm-stop`** — sets an internal `llm_stopped` flag and stops the
  llama-server. On every subsequent health-check cycle the daemon short-circuits
  (`check_and_recover()` returns early), so a crashed or health-failing
  llama-server stays stopped until `llm-start` is issued.
- **`llm-start`** — clears the flag and starts the llama-server; the daemon
  resumes its regular health checking and auto-recovery.
- The flag is persisted to `config.json` (`"llm_stopped": true/false`) so it
  survives a daemon restart: starting the daemon after a `llm-stop` starts with
  the llama-server already stopped.
- Set via the HTTP API too: `/api/v1/llm-stop` and `/api/v1/llm-start`.
- `status` shows `llm stopped: true|false` reflecting the flag.

```bash
# Check the current state
llama-monitor status
```

## Health Checking

The Hermes plugin (`hermes-llama-monitor`) provides these slash commands via the Hermes Gateway:

- `/llama list` — List available model templates
- `/llama set <name>` — Switch to a model and restart
- `/llama status` — Show current llama-server status

Start/Stop are intentionally NOT available through the Gateway for safety.

## Health Checking

The monitor performs health checks on the running llama-server:

1. HTTP request to `<bind>:<port>/health`
2. Expects HTTP 200 with body `{"status":"ok"}`
3. If health check fails:
   - Check process via `ps` — if zombie (`Z` state), kill and restart
   - If process is gone — restart immediately
   - If process is alive but unhealthy — `SIGTERM`, wait `restart_timeout_sec`, then `SIGKILL`, then restart

## Process State Machine

```
[STOPPED] --[start/restart]--> [STARTING]
[STARTING] --[process healthy]--> [RUNNING]
[STARTING] --[crash]--> [CRASHED]
[RUNNING] --[health OK]--> [RUNNING]
[RUNNING] --[health FAIL]--> [CHECKING]
[CHECKING] --[zombie]--> [KILLING]--> [STARTING]
[CHECKING] --[gone]--> [STARTING]
[CHECKING] --[alive]--> [WAITING]--> [KILLING]--> [STARTING]
```

## Logging

Output goes to stdout/stderr → journald (`journalctl -u llama-monitor`).

To enable a log file, set `log_file` in config.json.

## Hermes Plugin Installation

```bash
mkdir -p ~/.hermes/plugins/hermes-llama-monitor
cp -r llama-monitor/hermes-llama-monitor/hermes_llama_monitor ~/.hermes/plugins/hermes-llama-monitor/
```

Restart Hermes for the plugin to be loaded.

## Development

```bash
cd ~/llama-monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"  # if dev deps added
pip install pytest
pytest -v
```
