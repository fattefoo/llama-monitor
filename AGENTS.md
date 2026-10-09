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
├── CLI (cli.py): start/stop/restart/status/list-templates/create-base/create-from/edit/models/set/reload-template/logs
├── Daemon (daemon.py): process lifecycle, health-check loop, PID management
├── Config (config.py): JSON config in ~/.config/llama-monitor/, per-template JSON files in templates/
├── Health (health.py): async HTTP check against /health endpoint
├── Process monitor (process_monitor.py): zombie detection, graceful kill (SIGTERM→wait→SIGKILL)
├── API (api.py): HTTP server on port 9500 — /api/v1/status, /api/v1/templates, /api/v1/restart, /api/v1/set-template, /api/v1/shutdown
└── Hermes plugin (hermes-llama-monitor/): slash commands /llama list, /llama set, /llama status
```

Config layout: `~/.config/llama-monitor/config.json` + `~/.config/llama-monitor/templates/*.json`.

## Gotchas

- **CLI commands `start`/`stop`/`restart` elevation** (cli.py:16-97). `_require_sudo()` → `_elevate(argv)`: as root, run directly; in a graphical session (`DISPLAY`/`WAYLAND_DISPLAY`), re-exec via `pkexec` for a polkit dialog; otherwise fall back to `sudo` (the only headless option). pkexec strips `PYTHONPATH`, so the re-exec is `pkexec <python> -c "<bootstrap>" <package_root> ...` where the bootstrap inserts the package root into `sys.path` and calls `llama_monitor.main.main()`. `stop` never escalates — it signals the PID or falls back to `POST /api/v1/shutdown` (`_stop_via_api`), polling `/api/v1/status` for liveness. `restart` as non-root stops via API then elevates only the `start` half. `_needs_privileges()` decides per-invocation.
- **Template param keys map 1:1 to llama-server flags, declared in ONE place**: `PARAM_SPECS` in `llama_monitor/params.py`. Each `ParamSpec` carries key, flag, kind, llama-server default, select options, `off_flag` (`--no-x` for booleans) and a `when` predicate. `daemon._build_llama_args()` walks it to emit flags (skipping inactive specs); `cli._collect_parameters()` walks it to build prompts. Adding a parameter = adding one table entry. Verified against the turboquant build: `--penalty-*`, `--mirostat-tau/eta`, `--row-split`, `--num-gpu`, `--rpc`, `--lora-base`, `--lora-path`, `--chat-format` do NOT exist; the real names are `--repeat-last-n`, `--repeat-penalty`, `--frequency-penalty`, `--presence-penalty`, `--mirostat-lr`, `--mirostat-ent`, `--main-gpu`, `--lora`.
- **Applicability is rule-driven, not per-callsite.** A `when` predicate on the spec hides a parameter when a related value contradicts it (`spec_draft_*` needs a `draft-*` spec type; `top_k`/`top_p` are ignored under Mirostat; `dry_*` need `dry_multiplier > 0`; `yarn_*` need `rope_scaling: yarn`). The daemon applies the same predicates, so stale template keys are dropped rather than passed as contradictory flags. `params.prune_defaults()` removes keys equal to llama-server's default; the wizard offers this before saving.
- **The template wizard introspects the binary** (`_runtime_specs()`): runs `<binary> --help` once (cached in `_HELP_CACHE`), drops specs whose flag the build lacks, drops a boolean's `off_flag` when the build has no `--no-x` form, and replaces select lists with the values parsed from the help (keeping the static "unset" entry and the server default, which `--help` never enumerates). `_help_flag_values()` must handle all four llama.cpp formats — inline `[a|b|c]`, `{a,b,c}`, multi-line `allowed values: ...`, and `- value: desc` bullets. If help can't be read it falls back to static `PARAM_SPECS`. ctx_size options come from `params.ctx_options()` (0 plus n*1024 for n in 8..256 step 8).
- **Home directory resolution**: when running via sudo, `~` points to `/root`. The code uses `SUDO_USER` env var and scans `/home/*` for `.config/llama-monitor` to find the real user's config. This happens in both `cli.py` and `daemon.py` — they each have their own `_get_home_dir()`. If you modify the sudo/home logic, update both copies.
- **State machine**: process states flow STOPPED→STARTING→RUNNING/CHECKING→KILLING→STARTING. The `DaemonState` dataclass in daemon.py is a singleton accessed via `get_global_state()`. Never instantiate it directly.
- **Daemon runs in foreground** (for systemd `Type=simple`). The `_daemonize()` double-fork was removed — the process must stay alive. Run `llama-monitor start &` or `nohup` for background CLI usage.
- **External llama-server adoption**: if a llama-server is already running on the template port when daemon starts, it "adopts" it (sets `server_owned=False`). The daemon won't kill external servers unless `force_kill=True` (e.g. health check failure).
- **Health checks**: use `await_health()` which creates its own event loop. The daemon's shutdown handler interrupts `_current_loop` via `call_soon_threadsafe()`. Don't change the event loop creation in health.py without also updating the signal handler in daemon.py.
- **Systemd service**: `scripts/llama-monitor.service` has `WorkingDirectory=/home/n4rf/.config/llama-monitor` hardcoded. The `enable-autostart` CLI command generates a new service file with per-user paths — these don't match. Pick one pattern and reconcile.
- **Wizard flow**: `cli._collect_parameters()` prompts `CORE_KEYS` first (`params.CORE_KEYS`), then optionally the rest of the build's spec list; groups are offered via `cli._prompt_groups()`, which re-evaluates `when` per spec so a value chosen mid-group exposes what it unlocks. `cli._finalize_template()` removes inactive keys, migrates the legacy `chat_format` to `chat_template`, and offers to prune defaults.
- **`edit` subcommand** (`cli.cmd_edit()`): always starts with `_select_template()`, then either runs `_collect_parameters()` on the loaded template (`edit`) or prompts for exactly one parameter (`edit <name>`). Name lookup is lenient — `_resolve_parameter()` accepts the template key, the flag with or without `--`, separator-less spellings (`cachetypek`) and the negated form (`--no-webui` → `webui`), falling back to `_close_keys()` for suggestions (substring → subsequence → `difflib`). When a parameter is inactive, `_prompt_prerequisites()` walks the gating keys (derived by running `when` against a read-recording dict — `params.dependencies_of()`) and prompts for them, recursing up to depth 3; it raises `cli._Aborted` when the chain cannot be satisfied. Tests drive the prompts two ways: `answers` (ordered) for single-parameter edits and `answers_by_prompt` (regex rules, decline unmatched groups) for full walks.
- **Select prompts coerce to the spec's own type**: `_coerce_select()` infers int/float from the option values, so a custom `ctx_size` lands as a number, and an out-of-range selection number is treated as a custom value rather than an error.
- **Tests use monkeypatched module-level `CONFIG_DIR`/`CONFIG_FILE`/`TEMPLATES_DIR`** on the `config_module` (test_config.py). Test fixtures (`conftest.py`) create isolated tmpdir hierarchies. Tests don't require a running daemon or llama-server.
