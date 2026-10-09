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

### Parameter names

Each JSON key maps to one llama-server flag. The mapping is defined in
`_build_llama_args()` (`llama_monitor/daemon.py`) and mirrored in `_PARAM_FLAGS`
(`llama_monitor/cli.py`) for the wizard.

| Template key | llama-server flag |
| --- | --- |
| `port` / `bind` | `--port` / `--host` |
| `ctx_size` | `--ctx-size` |
| `n_gpu_layers` | `--n-gpu-layers` |
| `n_cpu_moe` | `--n-cpu-moe` |
| `flash_attn` | `--flash-attn` |
| `load_mode` | `--load-mode` |
| `cache_type_k` / `cache_type_v` | `--cache-type-k` / `--cache-type-v` |
| `temperature` | `--temp` |
| `top_p` / `top_k` / `min_p` | `--top-p` / `--top-k` / `--min-p` |
| `seed` / `logit_bias` | `--seed` / `--logit-bias` |
| `mirostat` | `--mirostat` |
| `mirostat_lr` / `mirostat_ent` | `--mirostat-lr` / `--mirostat-ent` |
| `repeat_last_n` | `--repeat-last-n` |
| `repeat_penalty` | `--repeat-penalty` |
| `frequency_penalty` | `--frequency-penalty` |
| `presence_penalty` | `--presence-penalty` |
| `spec_type` / `spec_draft_n_max` | `--spec-type` / `--spec-draft-n-max` |
| `jinja` / `metrics` | `--jinja` / `--metrics` (boolean flags) |
| `parallel` | `--parallel` |
| `split_mode` / `main_gpu` / `tensor_split` | `--split-mode` / `--main-gpu` / `--tensor-split` |
| `lora` | `--lora` |

> **Renamed keys.** Earlier versions used `penalty_last_n`, `penalty_repeat`,
> `penalty_freq`, `penalty_present`, `mirostat_tau`, `mirostat_eta`,
> `row_split`, `num_gpu`, `rpc`, `lora_base` and `lora_path`. Those flags do
> **not** exist in current llama-server builds — a template using them would
> fail to launch. They are now `repeat_last_n`, `repeat_penalty`,
> `frequency_penalty`, `presence_penalty`, `mirostat_lr`, `mirostat_ent`,
> `main_gpu` and `lora` as listed above. Update existing templates accordingly.

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

### Privilege escalation

`start` and `restart` spawn the daemon process itself and therefore need root.
The CLI escalates without hijacking your terminal prompt:

1. **Already root** — runs directly.
2. **Graphical session** (`DISPLAY` or `WAYLAND_DISPLAY` set) — re-executes via
   `pkexec`, so a polkit dialog asks for the password. `pkexec` sanitizes the
   environment (including `PYTHONPATH`), so the re-execution goes through an
   explicit interpreter call that injects the package root onto `sys.path`.
3. **Headless / no dialog agent** — falls back to `sudo`. This is the only case
   that prompts on the terminal, and it is unavoidable: nothing else can spawn a
   root process.

`stop` never escalates. It signals the PID file directly when permitted, and
otherwise shuts the daemon down over its own HTTP API
(`POST /api/v1/shutdown`), which the root daemon already serves to any local
user. `restart` as a normal user does the same stop, then escalates for the
start half only.

### Template wizards

`create-base` and `create-from` are interactive wizards that build a template
from scratch (off `base.json`) or from an existing one.

```bash
llama-monitor create-base                # new template from base.json
llama-monitor create-from                # copy an existing template
llama-monitor create-from qwen36_35b     # copy a named template
```

Both wizards ask for the binary, the model, the template name and then walk
**every** llama-server parameter, grouped into Server, Model loading, KV cache,
Sampling, Speculative decoding, Server features, Multi-GPU/distributed and
LoRA. Answer `n` at the "Configure every supported llama-server parameter?"
prompt to skip the walk and keep the source template's values.

**Select lists.** Any parameter with defined values is offered as a numbered
list with the current value marked (`◀ current`) and a final
`custom value...` entry that prompts for a free-form value. Parameters without
a defined value set get a plain typed prompt that re-asks on invalid input.

```
  Cache type K  (--cache-type-k)  [current: turbo4]
      1. f32        2. f16        3. bf16        4. q8_0   ◀ current
      5. q4_0       6. q4_1       7. iq4_nl      8. q5_0
      9. q5_1      10. turbo2    11. turbo3     12. turbo4
     13. custom value...
    Selection [12]
```

**Context size** is a select list of `n*1024` tokens for `n` from 8 to 256
(249 entries), printed six per row.

**Runtime introspection.** The wizard adapts itself to the selected binary: it
runs `<binary> --help` once (cached per binary) and then

- drops parameters the build does not advertise, and
- refreshes the select lists from the value sets the build itself documents.

It handles all four formats llama.cpp uses for enumerated values: the inline
`--flash-attn [on|off|auto]` and `--split-mode {none,layer,row,tensor}` forms,
the multi-line `allowed values: f32, f16, bf16, ...` continuation used by
`--cache-type-k`, and the `- value: description` bullet list used by
`--load-mode`. So on a given build `--spec-type` offers exactly the draft and
ngram modes that build implements. If the binary cannot be queried, the static
`PARAM_SPECS` table is used instead.

### Deleting a template

```bash
llama-monitor delete              # pick from a list
llama-monitor delete qwen36_35b   # delete a named template
```

After removing the template JSON you are asked whether to delete the model file
too. The prompt shows the path and size. The model is **kept** without asking if
another remaining template still references the same file. Deleting the active
template requires an extra confirmation.

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
