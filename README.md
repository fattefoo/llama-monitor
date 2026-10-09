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

Every template key maps to exactly one llama-server flag. Both the wizard and
the daemon read that mapping from a single table, `PARAM_SPECS` in
`llama_monitor/params.py`:

| Field | Meaning |
| --- | --- |
| `key` | JSON key in the template |
| `flag` | llama-server flag (`--ctx-size`) |
| `kind` | `int`, `float`, `bool`, `str` or `select` |
| `default` | llama-server's own default; used to prune the template |
| `options` | choices offered for a `select` |
| `off_flag` | negated form for booleans (`--no-webui`) |
| `when` | predicate that disables the parameter when it does not apply |

`_build_llama_args()` (`llama_monitor/daemon.py`) walks this table to emit the
flags, and the wizard (`llama_monitor/cli.py`) walks it to build its prompts,
so a parameter only has to be declared once.

Some examples of what the table covers:

| Template key | llama-server flag |
| --- | --- |
| `port` / `bind` | `--port` / `--host` |
| `ctx_size` / `parallel` | `--ctx-size` / `--parallel` |
| `n_gpu_layers` / `n_cpu_moe` | `--n-gpu-layers` / `--n-cpu-moe` |
| `split_mode` / `main_gpu` / `tensor_split` | `--split-mode` / `--main-gpu` / `--tensor-split` |
| `flash_attn` / `load_mode` / `fit` | `--flash-attn` / `--load-mode` / `--fit` |
| `cache_type_k` / `cache_type_v` | `--cache-type-k` / `--cache-type-v` |
| `temperature` / `top_p` / `top_k` / `min_p` | `--temp` / `--top-p` / `--top-k` / `--min-p` |
| `mirostat` / `mirostat_lr` / `mirostat_ent` | `--mirostat` / `--mirostat-lr` / `--mirostat-ent` |
| `repeat_last_n` / `repeat_penalty` | `--repeat-last-n` / `--repeat-penalty` |
| `frequency_penalty` / `presence_penalty` | `--frequency-penalty` / `--presence-penalty` |
| `spec_type` / `spec_draft_n_max` / `spec_draft_model` | `--spec-type` / `--spec-draft-n-max` / `--spec-draft-model` |
| `spec_ngram_mod_n_max` / `spec_ngram_simple_size_n` | `--spec-ngram-mod-n-max` / `--spec-ngram-simple-size-n` |
| `lora` / `lora_scaled` / `control_vector` | `--lora` / `--lora-scaled` / `--control-vector` |
| `mmproj` / `mmproj_device` | `--mmproj` / `--mmproj-device` |
| `jinja` / `webui` / `metrics` / `slots` | `--jinja` / `--webui` / `--metrics` / `--slots` |
| `chat_template` / `chat_template_kwargs` | `--chat-template` / `--chat-template-kwargs` |
| `api_key` / `cors_origins` / `ssl_key_file` | `--api-key` / `--cors-origins` / `--ssl-key-file` |

Run `python -c "from llama_monitor.params import PARAM_SPECS as P; print(len(P))"`
for the current count.

#### Parameters that do not apply

A parameter is only asked for — and only emitted — when it is meaningful for
the other values in the template. The `when` predicates encode this:

- `spec_draft_*` needs a `draft-*` spec type (`spec_type: "none"` or an ngram
  type disables all of them; each `spec_ngram_*` key needs its own ngram type).
- `mirostat_lr` needs `mirostat > 0`; `mirostat_ent` only applies to Mirostat
  1, so it is dropped for `mirostat: 2`.
- `top_k`, `top_p`, `top_nsigma` and `typical_p` are dropped when Mirostat is
  on, because llama-server ignores them.
- `dry_*` need `dry_multiplier > 0`, `xtc_threshold` needs
  `xtc_probability > 0`, `yarn_*` need `rope_scaling: "yarn"`,
  `tensor_split` needs `split_mode != "none"`, `pooling` and `embd_normalize`
  need `embedding: true`.

The daemon applies the same predicates, so a stale key in an existing template
is skipped rather than passed as a contradictory flag. The wizard also offers to
drop every key left at llama-server's own default before saving.

> **Renamed keys.** Earlier versions used `penalty_last_n`, `penalty_repeat`,
> `penalty_freq`, `penalty_present`, `mirostat_tau`, `mirostat_eta`,
> `row_split`, `num_gpu`, `rpc`, `lora_base` and `lora_path`. Those flags do
> **not** exist in current llama-server builds — a template using them would
> fail to launch. They are now `repeat_last_n`, `repeat_penalty`,
> `frequency_penalty`, `presence_penalty`, `mirostat_lr`, `mirostat_ent`,
> `main_gpu` and `lora` as listed above. Update existing templates accordingly.
>
> `chat_format` was also removed; use `chat_template`. The wizard migrates it
> automatically when you next edit a template.

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

Both wizards ask for the binary, the model, the template name and then walk the
parameters in groups — Server, Access control, Server features, Chat,
Context & cache, KV cache, CPU & threads, GPU & offload, Multimodal, LoRA,
Sampling, DRY sampling, Mirostat, Speculative decoding and Logging.

The core knobs (port, bind, context size, GPU layers, KV cache types,
sampling, spec type, ...) are always offered. After those, the wizard asks
whether to show the ~130 further parameters the selected binary supports.
Answer `n` at the "Configure llama-server parameters?" prompt to skip the walk
and keep the source template's values.

**Only relevant parameters are asked.** Applicability is re-evaluated as you go,
so choosing `spec_type: draft-mtp` reveals `spec_draft_n_max`,
`spec_draft_model` and the other draft-model parameters, while `none` or an
ngram type hides them. Enabling Mirostat hides `top_k`/`top_p` (llama-server
ignores them) and reveals `mirostat_lr`. See
[Parameters that do not apply](#parameters-that-do-not-apply) for the full set
of rules. Keys that no longer apply are removed from the template, and before
saving you are offered to drop everything still sitting at llama-server's own
default so the template only records what actually differs.

**Context size** is a select list of `n*1024` tokens for `n` from 8 to 256 in
steps of 8 (32 entries), printed six per row, plus `0 (from model)`. A
non-standard size can still be typed through the `custom value...` entry.

### Editing a template

`edit` changes, adds or removes parameters of an existing template. It always
starts by letting you pick the template to edit.

```bash
llama-monitor edit                  # walk every parameter of the template
llama-monitor edit ctx_size         # edit just one parameter
llama-monitor edit --spec-draft-n-max
```

`edit` with no argument runs the same wizard as `create-base`, seeded with the
template's current values, so parameters can be changed and removed. Keys that
no longer apply are dropped, and you are again offered to remove everything
left at a llama-server default.

`edit <name>` prompts for exactly one parameter, which is handy for a quick
change without walking 14 groups. The name is the template key (`ctx_size`),
but the llama-server flag also works, with or without a leading `--` and with
the negated form (`--no-webui` resolves to `webui`). Unknown names get a
suggestion, so a typo like `top_ky` points at `top_k`.

Three things can happen depending on the state of the parameter:

- **Present and changed** — the new value is written.
- **Present and left alone** — you are asked whether to remove it from the
  template instead.
- **Not present** — the value is added, along with a note when it equals
  llama-server's default (in which case you can still choose to store it
  explicitly).

If the parameter does not apply to the template's current values, `edit` says
so and prompts for the parameter it depends on, recursing through the chain if
needed:

```
$ llama-monitor edit spec_draft_n_max

Editing: qwen35_4b_crow

spec_draft_n_max (--spec-draft-n-max) does not apply to this template
because spec_type=''.

  spec_type --spec-type  (comma-separated list of methods)  [current: (unset)]
  ...
  -> spec_type = draft-mtp

spec_draft_n_max --spec-draft-n-max [not set; llama-server default: 3]
  spec_draft_n_max --spec-draft-n-max  (number of tokens to draft) [integer] [3]
```

Which parameters gate which is derived from the same `when` predicates the rest
of the wizard uses, so this follows the table automatically.

**Select lists.** Any parameter with defined values is offered as a numbered
list with the current value marked (`◀ current`) and a final
`custom value...` entry that prompts for a free-form value. Parameters without
a defined value set get a plain typed prompt that re-asks on invalid input.

```
  cache_type_k --cache-type-k  [current: turbo4]
      1. f32        2. f16        3. bf16        4. q8_0   ◀ current
      5. q4_0       6. q4_1       7. iq4_nl      8. q5_0
      9. q5_1      10. turbo2    11. turbo3     12. turbo4
     13. custom value...
    Selection [12]
```

**Runtime introspection.** The wizard adapts itself to the selected binary: it
runs `<binary> --help` once (cached per binary) and then

- drops parameters the build does not advertise,
- refreshes the select lists from the value sets the build itself documents,
  keeping the static "unset" entry and the current default, and
- drops the `--no-x` form of a boolean when the build has none.

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
