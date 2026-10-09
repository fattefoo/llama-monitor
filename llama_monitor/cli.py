#!/home/n4rf/llama-monitor/.venv/bin/python3
# -*- coding: utf-8 -*-
"""CLI interface for llama-monitor: start, stop, restart, status, enable/disable autostart."""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


def _require_sudo():
    """Ensure the command runs as root, avoiding a terminal password prompt.

    If not root, re-execute the same command through ``pkexec`` so a graphical
    polkit authentication dialog is shown instead of a terminal prompt.
    ``pkexec`` sanitizes the environment (notably PYTHONPATH), so the
    re-execution goes through an explicit interpreter call that injects the
    package root onto ``sys.path``.

    Returns without re-executing when the command can be completed without
    elevation (see :func:`_needs_privileges`); the caller is then responsible
    for handling that path itself.
    """
    if os.geteuid() == 0:
        return  # Already root

    if not _needs_privileges(sys.argv[1:]):
        return  # Caller can handle this command unprivileged (API path)

    _elevate(sys.argv[1:])


def _elevate(argv):
    """Re-execute this CLI as root via pkexec (GUI dialog) or sudo."""
    import shutil

    package_root = str(Path(__file__).resolve().parent.parent)

    if _has_graphical_session() and shutil.which("pkexec"):
        bootstrap = (
            "import sys; sys.path.insert(0, sys.argv.pop(1)); "
            "from llama_monitor.main import main; main()"
        )
        cmd = [shutil.which("pkexec"), sys.executable, "-c", bootstrap,
               package_root] + list(argv)
        result = subprocess.run(cmd)
        # 126/127: program could not be executed or authorization could not
        # be obtained (no dialog agent available). Fall through to sudo.
        if result.returncode not in (126, 127):
            sys.exit(result.returncode)
        print("pkexec could not authenticate (no dialog agent?). Falling back to sudo.",
              file=sys.stderr)

    sudo = shutil.which("sudo")
    if not sudo:
        print("Error: need root privileges but neither pkexec nor sudo is available.",
              file=sys.stderr)
        sys.exit(1)
    result = subprocess.run([sudo, sys.executable, "-m", "llama_monitor.main"] + list(argv))
    sys.exit(result.returncode)


def _needs_privileges(argv) -> bool:
    """True if this invocation cannot be served by the daemon API alone.

    ``stop`` only needs to reach the daemon's HTTP API, which the running
    (root) daemon serves to any local user, so it is handled unprivileged.
    ``start`` and ``restart`` genuinely need root: they spawn the daemon
    process themselves and there is no API endpoint that can do that.
    """
    for arg in argv:
        if arg.startswith("-"):
            continue
        return arg != "stop"
    return True


def _has_graphical_session() -> bool:
    """True if a graphical polkit agent could display an authentication dialog."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _get_home_dir() -> str:
    """Get the home directory, handling sudo correctly.
    
    When running via sudo, $HOME points to /root. We use SUDO_USER
    to find the real user's home directory.
    """
    home = os.path.expanduser("~")
    if home == "/root":
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user:
            return os.path.expanduser(f"~{sudo_user}")
    return home


def main():
    # Pre-process: convert --enable-autostart / --disable-autostart
    # into subcommand names (argparse doesn't accept -- in subcommand names).
    # Also merge trailing --sudo into the subcommand alias.
    argv = sys.argv[1:]
    if argv and argv[0] == "--enable-autostart":
        sudo_idx = None
        for i, a in enumerate(argv[1:], 2):
            if a == "--sudo":
                sudo_idx = i
                break
        if sudo_idx is not None:
            sys.argv[1] = "enable_autostart"  # alias that accepts --sudo
            sys.argv.pop(sudo_idx)
        else:
            sys.argv[1] = "enable-autostart"
    elif argv and argv[0] == "--disable-autostart":
        sudo_idx = None
        for i, a in enumerate(argv[1:], 2):
            if a == "--sudo":
                sudo_idx = i
                break
        if sudo_idx is not None:
            sys.argv[1] = "disable_autostart"
            sys.argv.pop(sudo_idx)
        else:
            sys.argv[1] = "disable-autostart"
    
    parser = argparse.ArgumentParser(
        prog="llama-monitor",
        description="llama-server process monitor with health checking and systemd integration",
    )
    subparsers = parser.add_subparsers(dest="command")
    
    # start — requires root
    start_parser = subparsers.add_parser("start", help="Start the daemon (foreground)")
    
    # stop — requires root
    subparsers.add_parser("stop", help="Stop the daemon")
    
    # restart — requires root
    subparsers.add_parser("restart", help="Restart the daemon")
    
    # status — read-only, no sudo needed
    status_parser = subparsers.add_parser("status", help="Show daemon status")
    status_parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    # list-templates — read-only, no sudo needed
    subparsers.add_parser("list-templates", help="List available model templates")
    
    # enable-autostart
    enable_parser = subparsers.add_parser(
        "enable-autostart", aliases=["enable_autostart"],
        help="Enable autostart via systemd"
    )
    enable_parser.add_argument("--sudo", action="store_true", help="Run with sudo")
    
    # disable-autostart
    disable_parser = subparsers.add_parser(
        "disable-autostart", aliases=["disable_autostart"],
        help="Disable autostart"
    )
    disable_parser.add_argument("--sudo", action="store_true", help="Run with sudo")
    
    # reload-template — uses the API (runs as root), no sudo needed
    reload_parser = subparsers.add_parser("reload-template", help="Change active template and restart")
    reload_parser.add_argument("template", nargs="?", default=None, help="Template name to activate (default: currently active)")
    
    # create-base — interactive wizard to create a new template from base
    create_base_parser = subparsers.add_parser("create-base", help="Create a new template from base.json")
    
    # create-from — interactive wizard to copy an existing template
    # Optional positional: create-from [source_template_name]
    create_from_parser = subparsers.add_parser("create-from", help="Create a new template from an existing one")
    create_from_parser.add_argument("template", nargs="?", default=None, help="Source template to copy from (default: interactive selection)")

    # edit — change, add or remove parameters of an existing template
    # Optional positional: edit [parameter_key]
    edit_parser = subparsers.add_parser(
        "edit", help="Edit parameters of an existing template")
    edit_parser.add_argument(
        "parameter", nargs="?", default=None,
        help="Parameter to edit (default: walk all parameters)")

    # delete — interactive wizard to delete an existing template
    delete_parser = subparsers.add_parser("delete", help="Delete an existing template")
    delete_parser.add_argument("template", nargs="?", default=None, help="Template name to delete (default: interactive selection)")
    
    # models — show GGUF files without a template
    subparsers.add_parser("models", help="List GGUF models without a template")
    
    # set — interactive selector to set active_template
    set_parser = subparsers.add_parser("set", help="Set the active template")
    
    # llm-stop — signal daemon to stop the llama-server and keep it stopped
    subparsers.add_parser("llm-stop", help="Stop llama-server (daemon won't restart it)")

    # llm-start — signal daemon to start the llama-server and resume monitoring
    subparsers.add_parser("llm-start", help="Start llama-server (daemon resumes monitoring)")

    # logs — show daemon and llama-server logs
    logs_parser = subparsers.add_parser("logs", help="Show daemon and llama-server logs")
    logs_parser.add_argument("lines", nargs="?", type=int, default=40, help="Number of lines to show (default: 40)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    command = args.command
    
    # Commands that require elevated privileges
    if command in ("start", "stop", "restart"):
        _require_sudo()
    
    if command == "start":
        cmd_start()
    elif command == "stop":
        cmd_stop()
    elif command == "restart":
        cmd_restart()
    elif command == "status":
        cmd_status(args.json)
    elif command == "list-templates":
        cmd_list_templates()
    elif command == "enable-autostart" or command == "enable_autostart":
        cmd_enable_autostart(sudo=args.sudo)
    elif command == "disable-autostart" or command == "disable_autostart":
        cmd_disable_autostart(sudo=args.sudo)
    elif command == "reload-template":
        cmd_reload_template(args.template)
    elif command == "create-base":
        cmd_create_base()
    elif command == "create-from":
        cmd_create_from(args.template)
    elif command == "edit":
        cmd_edit(args.parameter)
    elif command == "delete":
        cmd_delete(args.template)
    elif command == "models":
        cmd_models()
    elif command == "set":
        cmd_set()
    elif command == "logs":
        cmd_logs(args.lines)
    elif command == "llm-stop":
        cmd_llm_stop()
    elif command == "llm-start":
        cmd_llm_start()
    else:
        parser.print_help()
        sys.exit(1)


def _get_config_path() -> Path:
    home = _get_home_dir()
    return Path(os.path.join(home, ".config", "llama-monitor", "config.json"))


def _get_pid_file() -> Path:
    home = _get_home_dir()
    return Path(os.path.join(home, ".config", "llama-monitor", "llama-monitor.pid"))


def _get_pid_file_fallback() -> Optional[Path]:
    """Get the root user's PID file as a fallback when SUDO_USER changes the lookup."""
    fallback = Path("/root/.config/llama-monitor/llama-monitor.pid")
    try:
        if fallback.exists():
            return fallback
    except PermissionError:
        pass
    return None


def _get_service_file() -> Path:
    return Path("/etc/systemd/system/llama-monitor.service")


def _find_existing_llama_server() -> Optional[dict]:
    """Check if any llama-server process is already running.
    
    Returns dict with pid, model info, or None.
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,args", "--no-headers"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.strip().split(None, 1)
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if "llama-server" not in parts[1]:
                continue
            # Found a llama-server process, get more info
            cmdline_path = f"/proc/{pid}/cmdline"
            try:
                with open(cmdline_path, "r") as f:
                    cmdline = f.read()
                tokens = cmdline.split("\x00")
                binary = tokens[0] if tokens else "unknown"
                model_path = None
                model_name = "unknown"
                for i, t in enumerate(tokens):
                    if t == "--model" and i + 1 < len(tokens):
                        model_path = tokens[i + 1]
                        model_name = os.path.basename(model_path)
                return {
                    "pid": pid,
                    "binary": binary,
                    "model_name": model_name,
                    "model_path": model_path or "unknown",
                    "cmdline": " ".join(tokens[:3]) + ("..." if len(tokens) > 3 else ""),
                }
            except (FileNotFoundError, PermissionError):
                return {"pid": pid, "binary": "unknown", "model_name": "unknown",
                        "model_path": "unknown", "cmdline": "unknown"}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _check_port_in_use(port: int) -> Optional[dict]:
    """Check if a port is already in use.
    
    Returns dict with pid, process info, or None.
    """
    try:
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if f":{port} " not in line and f":{port}\t" not in line:
                continue
            # Extract PID from the line, e.g. users:(("llama-server",pid=1234,fd=3))
            if "pid=" in line:
                start = line.index("pid=")
                end = line.index(",", start)
                try:
                    pid = int(line[start+4:end])
                except ValueError:
                    continue
                # Get process name
                proc_name = "unknown"
                try:
                    name_path = f"/proc/{pid}/comm"
                    with open(name_path) as f:
                        proc_name = f.read().strip()
                except (FileNotFoundError, PermissionError):
                    pass
                return {
                    "pid": pid,
                    "process_name": proc_name,
                    "address": line.strip().split()[3] if len(line.strip().split()) > 3 else "unknown",
                }
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def cmd_start():
    """Start the daemon in foreground mode."""
    from llama_monitor.config import load_config
    from llama_monitor.daemon import run_daemon, get_global_state, _read_pid
    
    # ---- Check 1: Is another daemon already running? ----
    pid_file = _get_pid_file()
    try:
        if not pid_file.exists():
            fallback = _get_pid_file_fallback()
            if fallback and fallback.exists():
                pid_file = fallback
    except PermissionError:
        pass
    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text().strip())
            try:
                os.kill(existing_pid, 0)
                print(f"Daemon already running (PID {existing_pid}). "
                      f"To stop it, run: llama-monitor stop")
                sys.exit(1)
            except ProcessLookupError:
                print(f"Found stale PID file (PID {existing_pid}). Cleaning up.")
                pid_file.unlink()
            except PermissionError:
                # Process exists but we can't signal it (e.g. root-owned).
                # Still running — refuse to start another.
                print(f"Daemon already running (PID {existing_pid}). "
                      f"To stop it, run: llama-monitor stop")
                sys.exit(1)
        except ValueError:
            print("Found malformed PID file. Cleaning up.")
            pid_file.unlink()
    
    # ---- Check 2: Is the daemon port (9500) already in use? ----
    try:
        config = load_config()
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        sys.exit(1)
    
    if not config["enabled"]:
        print("Daemon is disabled in config. Set 'enabled': true in config.json")
        sys.exit(0)
    
    daemon_port = config.get("daemon_port", 9500)
    port_conflict = _check_port_in_use(daemon_port)
    if port_conflict:
        print(f"ERROR: Daemon port {daemon_port} is already in use!")
        print(f"  PID:       {port_conflict['pid']}")
        print(f"  Process:   {port_conflict['process_name']}")
        print(f"  Address:   {port_conflict['address']}")
        print(f"Stop that process or change the daemon_port in config.json.")
        sys.exit(1)
    
    # ---- Check 3: Template port conflicts (informational warning only) ----
    from llama_monitor.config import list_template_names
    template_names = list_template_names()
    for tname in template_names:
        try:
            from llama_monitor.config import load_template
            tpl = load_template(tname)
            tpl_port = tpl.get("port", 8080)
            tpl_name = tpl.get("name", tname)
            if tpl_port == daemon_port:
                continue
            port_conflict = _check_port_in_use(tpl_port)
            if port_conflict:
                print(f"WARNING: Template '{tpl_name}' uses port {tpl_port}, "
                      f"which is already in use by PID {port_conflict['pid']} "
                      f"({port_conflict['process_name']}). "
                      f"llama-monitor will monitor this instance.")
        except Exception:
            pass
    
    # All checks passed - start the daemon (it will auto-detect running llama-server)
    run_daemon(config)


def _stop_via_api():
    """Shut the daemon down through its HTTP API (no root needed)."""
    import httpx

    print("Shutting down via daemon API...")
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.post("http://127.0.0.1:9500/api/v1/shutdown")
    except httpx.ConnectError:
        print("Cannot connect to daemon API.", file=sys.stderr)
        print("Try: sudo systemctl stop llama-monitor", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Shutdown failed: {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(f"API error: {resp.status_code} - {resp.text}", file=sys.stderr)
        sys.exit(1)

    print("Shutdown initiated. Waiting for daemon to stop...")
    for _ in range(20):
        time.sleep(0.5)
        if _daemon_api_alive() is False:
            print("Daemon stopped.")
            sys.exit(0)
    print("Daemon did not stop within timeout.", file=sys.stderr)
    sys.exit(1)


def _daemon_api_alive() -> Optional[bool]:
    """True if the daemon API answers, False if not, None if undetermined."""
    import httpx
    try:
        with httpx.Client(timeout=2) as client:
            resp = client.get("http://127.0.0.1:9500/api/v1/status")
            return resp.status_code == 200
    except httpx.ConnectError:
        return False
    except Exception:
        return None


def cmd_stop():
    """Stop the running daemon.

    Signals the PID directly when permitted, otherwise uses the daemon API
    (which does not require root).
    """
    import signal as sig

    pid_file = _get_pid_file()
    try:
        if not pid_file.exists():
            fallback = _get_pid_file_fallback()
            if fallback and fallback.exists():
                pid_file = fallback
    except PermissionError:
        pass

    pid = None
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        pass  # missing, unreadable or malformed — try the API below

    if pid is None:
        _stop_via_api()
    
    try:
        os.kill(pid, sig.SIGTERM)
        print(f"Sent SIGTERM to daemon (PID {pid}). Waiting for shutdown...")
        
        # Wait up to 10 seconds
        for _ in range(20):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                print("Daemon stopped.")
                pid_file.unlink(missing_ok=True)
                sys.exit(0)
        
        print("Daemon did not stop gracefully. Sending SIGKILL...", file=sys.stderr)
        os.kill(pid, sig.SIGKILL)
        time.sleep(1)
        pid_file.unlink(missing_ok=True)
        print("Daemon killed.")
        
    except ProcessLookupError:
        print("Daemon not running (stale PID file).")
        pid_file.unlink(missing_ok=True)
    except PermissionError:
        # Daemon is owned by another user (root) — use the API instead.
        _stop_via_api()


def cmd_restart():
    """Restart the daemon."""
    if os.geteuid() != 0:
        # Starting the daemon needs root; stop unprivileged via the API first,
        # then hand the actual start to the elevation path.
        cmd_stop()
        time.sleep(1)
        _elevate(["start"])
    cmd_stop()
    time.sleep(1)
    cmd_start()


def _parse_llama_cmdline(llama_pid: int) -> Optional[dict]:
    """Parse a running llama-server process cmdline to extract model info.
    
    Reads /proc/<pid>/cmdline to find the model path and other parameters.
    Returns dict with llama-server status info or None.
    """
    try:
        cmdline_path = f"/proc/{llama_pid}/cmdline"
        with open(cmdline_path, "r") as f:
            cmdline = f.read()
        
        if not cmdline:
            return None
        
        # cmdline is null-separated
        parts = cmdline.split("\x00")
        
        # First token is the binary/execution path
        binary = parts[0] if parts else "unknown"
        
        # Find model path: look for --model followed by its value
        model_path = None
        model_name = "unknown"
        
        for i, part in enumerate(parts):
            if part == "--model" and i + 1 < len(parts):
                model_path = parts[i + 1]
                model_name = os.path.basename(model_path)
        
        if not model_path:
            # Try to find model from path component (.gguf)
            for part in parts:
                if ".gguf" in part:
                    model_path = part
                    model_name = os.path.basename(part)
                    break
        
        return {
            "pid": llama_pid,
            "model_path": model_path or "unknown",
            "model_name": model_name,
            "binary": binary,
            "cmdline": " ".join(parts[:5]) + ("..." if len(parts) > 5 else ""),
            "health": "unknown",  # We don't know without API
            "state": "running",
        }
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _fetch_llama_server_models(base_url: str, timeout: float = 3.0) -> Optional[dict]:
    """Query llama-server's /v1/models endpoint.
    
    Returns dict with model_id, model_name, or None on failure.
    """
    try:
        import httpx
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{base_url}/v1/models")
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                if models:
                    return {
                        "model_id": models[0].get("id", "unknown"),
                        "model_name": os.path.basename(models[0].get("id", "unknown")),
                    }
    except Exception:
        pass
    return None


def cmd_status(as_json: bool = False):
    """Show daemon and llama-server status."""
    from llama_monitor.config import load_config, list_template_names, load_template
    from llama_monitor.daemon import get_global_state, _read_pid
    from llama_monitor.process_monitor import get_process_state, find_llama_server_pids
    
    pid_file = _get_pid_file()
    service_file = _get_service_file()
    
    daemon_running = False
    try:
        if not pid_file.exists():
            fallback = _get_pid_file_fallback()
            if fallback and fallback.exists():
                pid_file = fallback
    except PermissionError:
        pass
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            try:
                os.kill(pid, 0)
                daemon_running = True
            except ProcessLookupError:
                pass
            except PermissionError:
                daemon_running = True
        except ValueError:
            pass
        except PermissionError:
            pass
    
    autostart_enabled = service_file.exists()
    
    try:
        config = load_config()
    except Exception:
        config = {}
    
    templates = list_template_names()
    
    # Get API status from daemon (port 9500)
    api_status = None
    import httpx
    import asyncio
    async def fetch_status():
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                resp = await client.get("http://127.0.0.1:9500/api/v1/status")
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass
        return None
    
    try:
        api_status = asyncio.run(fetch_status())
    except Exception:
        pass
    
    # Build active template info from config + daemon API
    active_template_name = "N/A"
    active_template_path = "N/A"
    llama_server_url = None
    llama_server_model_info = None
    
    if api_status and api_status.get("active_template"):
        active_template_name = api_status.get("active_template_name", api_status.get("active_template"))
        active_template_path = api_status.get("model_path", "N/A")
        tpl_port = api_status.get("port", 8080)
        tpl_bind = api_status.get("bind", "127.0.0.1")
        llama_server_url = f"http://{tpl_bind}:{tpl_port}"
    elif config.get("active_template"):
        try:
            tpl = load_template(config["active_template"])
            active_template_name = tpl.get("name", config["active_template"])
            active_template_path = tpl.get("model_path", "N/A")
            tpl_port = tpl.get("port", 8080)
            tpl_bind = tpl.get("bind", "127.0.0.1")
            llama_server_url = f"http://{tpl_bind}:{tpl_port}"
        except Exception:
            pass
    
    # Query the running llama-server's /v1/models endpoint
    if llama_server_url:
        llama_server_model_info = _fetch_llama_server_models(llama_server_url)
    
    # If no daemon API available, check for running llama-server processes directly
    direct_llama_status = None
    if not api_status:
        llama_pids = find_llama_server_pids()
        for llama_pid in llama_pids:
            ps_state = get_process_state(llama_pid)
            if ps_state.alive:
                direct_llama_status = _parse_llama_cmdline(llama_pid)
                if direct_llama_status:
                    break
    
    status = {
        "daemon_running": daemon_running,
        "autostart_enabled": autostart_enabled,
        "check_interval_sec": config.get("check_interval_sec", "unknown"),
        "restart_timeout_sec": config.get("restart_timeout_sec", "unknown"),
        "config_path": str(_get_config_path()),
        "templates_available": templates,
        "templates_count": len(templates),
    }
    
    if api_status:
        status.update(api_status)
        if api_status.get("pid"):
            daemon_running = True
    elif direct_llama_status:
        status.update(direct_llama_status)
    
    # Enrich with /v1/models data
    if llama_server_model_info:
        status["running_model_id"] = llama_server_model_info["model_id"]
        status["running_model_name"] = llama_server_model_info["model_name"]
    
    if as_json:
        print(json.dumps(status, indent=2))
    else:
        print("=== llama-monitor Status ===")
        print(f"  Daemon: {'running' if daemon_running else 'stopped'}")
        print(f"  Autostart: {'enabled' if autostart_enabled else 'disabled'}")
        print(f"  Check interval: {status['check_interval_sec']}s")
        print(f"  Restart timeout: {status['restart_timeout_sec']}s")
        print(f"  Config: {status['config_path']}")
        print(f"  Templates available: {status['templates_count']}")
        for t in templates:
            print(f"    - {t}")
        
        # Active template info
        print(f"\n  Active template: {active_template_name}")
        print(f"  Model file: {active_template_path}")
        
        if api_status and api_status.get("pid"):
            print(f"  llama-server PID: {api_status['pid']}")
            print(f"  Process state: {api_status.get('state', 'unknown')}")
            print(f"  Health: {api_status.get('health', 'unknown')}")
        print(f"  llm stopped: {api_status.get('llm_stopped', 'False')}")
        
        # Running model from /v1/models
        if llama_server_model_info:
            print(f"\n  Running model (from /v1/models):")
            print(f"    ID: {llama_server_model_info['model_id']}")
        elif direct_llama_status:
            print(f"\n  llama-server process found:")
            print(f"  Model: {direct_llama_status.get('model_name', 'unknown')}")
            print(f"  PID: {direct_llama_status.get('pid', 'N/A')}")
            print(f"  Command line: {direct_llama_status.get('cmdline', 'N/A')}")
    
    sys.exit(0)


def cmd_list_templates():
    """List available templates as JSON."""
    from llama_monitor.config import list_template_names
    
    templates = list_template_names()
    print(json.dumps(templates, indent=2))


def cmd_enable_autostart(sudo: bool = False):
    """Create and enable systemd service."""
    # Check for root permissions first
    if not (sudo or os.geteuid() == 0):
        print(
            "Error: --enable-autostart requires root privileges.\n"
            "Usage:\n"
            "  sudo llama-monitor enable-autostart\n"
            "  llama-monitor enable-autostart --sudo\n",
            file=sys.stderr,
        )
        sys.exit(1)
    
    service_file = _get_service_file()
    config_dir = Path(os.path.join(_get_home_dir(), ".config", "llama-monitor"))
    
    service_content = """[Unit]
Description=Llama Server Monitor
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/llama-monitor start
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=HOME=%(HOME_USER)s
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=%(CONFIG_DIR)s

[Install]
WantedBy=multi-user.target
"""
    config_dir = str(Path(os.path.join(_get_home_dir(), ".config", "llama-monitor")).resolve())
    home_user = _get_home_dir()
    
    service_content = service_content % {"HOME_USER": home_user, "CONFIG_DIR": config_dir}
    
    with open(service_file, "w") as f:
        f.write(service_content)
    
    print(f"Created systemd service: {service_file}")
    
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "llama-monitor"], check=True)
    
    print("llama-monitor autostart enabled and started.")


def cmd_disable_autostart(sudo: bool = False):
    """Disable and remove systemd service."""
    # Check for root permissions first
    if not (sudo or os.geteuid() == 0):
        print(
            "Error: --disable-autostart requires root privileges.\n"
            "Usage:\n"
            "  sudo llama-monitor disable-autostart\n"
            "  llama-monitor disable-autostart --sudo\n",
            file=sys.stderr,
        )
        sys.exit(1)
    
    service_file = _get_service_file()
    
    subprocess.run(["systemctl", "stop", "llama-monitor"], check=False)
    subprocess.run(["systemctl", "disable", "llama-monitor"], check=False)
    
    if service_file.exists():
        service_file.unlink()
    
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    print("llama-monitor autostart disabled and removed.")


def cmd_reload_template(template_name: str):
    """Change active template in config and restart the llama-server."""
    import httpx
    import asyncio
    
    # If no template specified, use the one configured as active
    if not template_name:
        from llama_monitor.config import load_config
        config = load_config()
        template_name = config.get("active_template")
        if not template_name:
            print("No template specified and no active template configured.")
            print("Usage: llama-monitor reload-template <template-name>")
            sys.exit(1)
        print(f"No template specified — using active template: {template_name}")
    
    async def reload():
        try:
            # Send template switch via API
            body = json.dumps({"template": template_name}).encode("utf-8")
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post("http://127.0.0.1:9500/api/v1/set-template", content=body)
                if resp.status_code == 200:
                    data = resp.json()
                    print(f"Restart initiated: {data.get('message', '')}")
                    return True
                else:
                    print(f"API error: {resp.status_code} - {resp.text}", file=sys.stderr)
                    return False
        except httpx.ConnectError:
            print("Cannot connect to llama-monitor daemon. Is it running?", file=sys.stderr)
            print("Try: sudo systemctl start llama-monitor")
            return False
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return False
    
    success = asyncio.run(reload())
    sys.exit(0 if success else 1)


def _get_model_files():
    """List all GGUF files in ~/models/."""
    models_dir = Path(os.path.expanduser("~/models"))
    if not models_dir.exists():
        return []
    return sorted([f for f in models_dir.iterdir() if f.is_file() and f.suffix == ".gguf"])


def _models_with_templates():
    """Return set of model file basenames that have templates."""
    templates = list_template_names()
    result = set()
    for tname in templates:
        try:
            tpl = load_template(tname)
            mp = tpl.get("model_path", "")
            result.add(os.path.basename(mp))
        except Exception:
            pass
    return result


def _read_base_template():
    """Read base.json from config directory."""
    from llama_monitor.config import CONFIG_DIR
    base_path = CONFIG_DIR / "base.json"
    if not base_path.exists():
        print(f"Base template not found: {base_path}")
        sys.exit(1)
    with open(base_path) as f:
        return json.load(f)


def _prompt_input(prompt_text, default=None):
    """Prompt user for input with optional default value."""
    if default is not None:
        display = f"[{default}] "
    else:
        display = ""
    val = input(f"{prompt_text} {display}").strip()
    return val if val else default


def _prompt_yes(prompt_text, default=False):
    """Prompt user for yes/no answer."""
    choices = "Y/n" if default else "y/N"
    display = f"{prompt_text} ({choices}) "
    val = input(display).strip().lower()
    if not val and default is True:
        return True
    if not val and default is False:
        return False
    return val in ("y", "yes")


def _find_llama_servers():
    """Find all llama-server binaries on the system.

    Searches the home directory recursively for ``build/bin/llama-server``
    and the LLM Studio extension backends. Returns a sorted list of unique
    absolute paths.
    """
    from glob import glob
    import shutil

    found = {}
    home = os.path.expanduser("~/")
    patterns = [
        os.path.join(home, "**", "build", "bin", "llama-server"),
        os.path.join(home, ".lmstudio", "extensions", "backends", "*", "llama-server"),
    ]
    for pat in patterns:
        for path in glob(pat, recursive=True):
            if os.path.isfile(path) and os.access(path, os.X_OK):
                found[os.path.realpath(path)] = os.path.abspath(path)

    path_bin = shutil.which("llama-server")
    if path_bin:
        found[os.path.realpath(path_bin)] = os.path.abspath(path_bin)

    return sorted(found.values())


def _prompt_binary_selection(default_binary=None):
    """Show all found llama-server binaries and let the user pick one.

    Returns the chosen absolute path, or ``default_binary`` on empty input.
    """
    binaries = _find_llama_servers()
    if not binaries:
        return default_binary

    default_bin = default_binary or binaries[0]
    display_idx = 1
    for i, b in enumerate(binaries, 1):
        try:
            if os.path.samefile(b, default_bin):
                display_idx = i
                break
        except OSError:
            continue

    print("\nAvailable llama-server binaries:")
    for i, b in enumerate(binaries, 1):
        mark = "  (active)" if i == display_idx else ""
        print(f"  {i}. {b}{mark}")

    sel = _prompt_input("  Binary selection (number)", str(display_idx))
    try:
        n = int(sel)
        if 1 <= n <= len(binaries):
            return binaries[n - 1]
    except ValueError:
        pass
    return default_binary


def _collect_parameters(template, ask_all=None, ask_binary=True):
    """Interactively prompt for the binary + all template parameters.

    Uses the current values in ``template`` as defaults and updates
    ``template`` in place. Returns the template.

    Parameters come from ``params.PARAM_SPECS``, adapted to the selected
    llama-server build by :func:`_runtime_specs`. Prompting walks the table
    group by group; inside a group only parameters that are meaningful for the
    values chosen so far are asked, so (for example) ``spec_draft_n_max`` is
    only offered when a draft-model spec type is selected. A parameter that
    becomes irrelevant is removed from the template, and anything left at
    llama-server's own default can be dropped before saving.
    """
    from llama_monitor.params import (CORE_KEYS, GROUPS, PARAM_SPECS,
                                      PARAM_FLAGS, SPEC_BY_KEY, is_default)

    # Binary selection (the build decides which parameters apply)
    binaries = _find_llama_servers() if ask_binary else []
    if binaries:
        default_bin = template.get("binary") or binaries[0]
        chosen = _prompt_binary_selection(default_bin)
        if chosen:
            template["binary"] = chosen
            print(f"  -> {chosen}")

    if ask_all is None:
        ask_all = _prompt_yes(
            "  Configure llama-server parameters?", default=True)

    if not ask_all:
        return template

    specs = _runtime_specs(template.get("binary")) or PARAM_SPECS

    # Offer the core knobs first, then let the user walk every remaining
    # parameter of the selected build group by group.
    core = [s for s in specs if s.key in CORE_KEYS or s.always_ask]
    extra = [s for s in specs if s.key not in CORE_KEYS and not s.always_ask]

    def show(group_specs):
        """Prompt for each spec, skipping the ones that do not apply."""
        for spec in group_specs:
            if not spec.active(template):
                continue
            current = template.get(spec.key, spec.default)
            value = _prompt_spec(spec, current)
            if value is not None:
                template[spec.key] = value

    _prompt_groups(core, template, show)

    remaining = [s for s in extra if s.active(template)
                 or s.key in template]
    if remaining:
        print()
        if _prompt_yes(f"  Show {len(remaining)} additional parameter(s) "
                       "this build supports?", default=False):
            _prompt_groups(remaining, template, show)

    _finalize_template(template, PARAM_SPECS, PARAM_FLAGS, SPEC_BY_KEY,
                       is_default)
    return template


def _prompt_groups(specs, template, show):
    """Walk the table group by group, offering each group as a block.

    Applicability is re-checked inside ``show`` right before each prompt, so
    changing a value (enabling a spec type, say) still exposes the parameters
    it unlocks later in the same group.
    """
    from llama_monitor.params import GROUPS

    for group in GROUPS:
        group_specs = [s for s in specs if s.group == group]
        applicable = [s for s in group_specs if s.active(template)]
        if not applicable:
            continue

        plural = "s" if len(applicable) != 1 else ""
        print(f"\n=== {group} ({len(applicable)} parameter{plural}) ===")
        if not _prompt_yes(f"  Configure {group}?", default=True):
            print("  (unchanged)")
            continue
        show(group_specs)


# Keys the daemon always passes explicitly, so they are never pruned.
_ALWAYS_KEEP = {"port", "bind", "name", "model_path", "binary"}


def _finalize_template(template, PARAM_SPECS, PARAM_FLAGS, SPEC_BY_KEY, is_default):
    """Drop irrelevant parameters and (optionally) llama-server defaults."""
    stale = [spec.key for spec in PARAM_SPECS
             if spec.key in template and not spec.active(template)]
    for key in stale:
        del template[key]
    if stale:
        print(f"\nRemoved {len(stale)} inapplicable parameter(s): {', '.join(stale)}")

    # Legacy key from older llama-server builds.
    legacy = template.pop("chat_format", None)
    if legacy and not template.get("chat_template"):
        template["chat_template"] = legacy
        print(f"Migrated chat_format -> chat_template ({legacy})")

    defaults = [key for key, value in template.items()
                if key not in _ALWAYS_KEEP and key in PARAM_FLAGS
                and is_default(SPEC_BY_KEY[key], value)]
    if not defaults:
        return
    if _prompt_yes(
            f"  Drop {len(defaults)} parameter(s) left at llama-server defaults?",
            default=True):
        for key in defaults:
            del template[key]
        print(f"  -> {', '.join(sorted(defaults))}")


_HELP_CACHE: dict = {}


def _llama_server_help(binary: str) -> str:
    """Run ``<binary> --help`` once and cache the output."""
    if binary not in _HELP_CACHE:
        try:
            result = subprocess.run([binary, "--help"], capture_output=True,
                                    text=True, timeout=10)
            _HELP_CACHE[binary] = result.stdout or ""
        except (OSError, subprocess.SubprocessError):
            _HELP_CACHE[binary] = ""
    return _HELP_CACHE[binary]


def _help_flag_block(help_text: str, flag: str) -> Optional[str]:
    """Return the flag's help entry plus its indented continuation lines."""
    lines = help_text.splitlines()
    for i, line in enumerate(lines):
        # Require a word boundary so --port does not match --parallel.
        if not re.search(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])", line):
            continue
        block = [line]
        for follow in lines[i + 1:]:
            if not follow.strip():
                break
            if not follow.startswith((" " * 8, "\t")):
                break
            block.append(follow)
        return "\n".join(block)
    return None


def _help_flag_values(help_text: str, flag: str):
    """Extract the allowed values a llama-server flag advertises in --help.

    Handles the inline forms ``--flag {a,b,c}``, ``--flag [a|b|c]`` and the
    bare ``--flag a,b,c`` (used by --spec-type), plus the multi-line forms
    ``allowed values: a, b, c`` and ``- a: description`` bullet lists.
    Returns a list of strings, or None when nothing is enumerated.
    """
    block = _help_flag_block(help_text, flag)
    if not block:
        return None
    first_line = block.splitlines()[0]

    inline = re.search(r"\[([^\]]+)\]|\{([^}]+)\}", first_line)
    if inline:
        raw = inline.group(1) or inline.group(2)
        values = [v.strip() for v in re.split(r"[|,]", raw) if v.strip()]
        if _is_value_list(values):
            return values

    allowed = re.search(r"allowed values:\s*([^\n]*(?:\n\s{20,}[^\n]*)*)",
                        block, re.IGNORECASE)
    if allowed:
        flat = " ".join(l.strip() for l in allowed.group(1).splitlines())
        flat = flat.split("(default")[0]
        values = [v.strip() for v in flat.split(",") if v.strip()]
        if _is_value_list(values):
            return values

    # Bullet list form: "- value: description"
    bullets = re.findall(r"^\s*-\s+([A-Za-z0-9][A-Za-z0-9._+-]*)\s*:", block,
                         re.MULTILINE)
    if len(bullets) >= 2 and _is_value_list(bullets):
        return bullets

    # Bare form: "--flag a,b,c"
    bare = re.search(r"(?<![\w-])" + re.escape(flag)
                     + r"\s+([a-z0-9][a-z0-9,._-]*(?:,[a-z0-9][a-z0-9,._-]*)+)",
                     first_line)
    if bare:
        values = [v.strip() for v in bare.group(1).split(",") if v.strip()]
        if _is_value_list(values):
            return values
    return None


def _is_value_list(values) -> bool:
    """Reject placeholders like N/TYPE/MODE and stray prose."""
    if not values:
        return False
    placeholders = {"n", "type", "mode", "index", "value", "num", "str", "int"}
    return all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", v) for v in values) \
        and not (len(values) == 1 and values[0].lower() in placeholders)


def _runtime_specs(binary: Optional[str]) -> list:
    """Adapt PARAM_SPECS to the selected llama-server build.

    Parameters the build does not advertise are dropped, select lists are
    refreshed from the value sets the build itself documents, and a boolean's
    "--no-x" form is dropped when the build has none. Falls back to the static
    table when the binary cannot be queried.
    """
    from llama_monitor.params import PARAM_SPECS

    if not binary:
        return PARAM_SPECS
    help_text = _llama_server_help(binary)
    if not help_text.strip():
        return PARAM_SPECS

    specs = []
    for spec in PARAM_SPECS:
        if spec.flag not in help_text:
            continue  # not supported by this build

        updates = {}
        if spec.kind == "select":
            values = _help_flag_values(help_text, spec.flag)
            if values:
                if spec.key == "split_mode":
                    values = [v for v in values if v != "tensor"]
                # The build's own list is authoritative (it knows about build
                # specific extras such as the turbo quant types); keep the
                # static "unset" and current-default entries, which --help
                # never lists.
                merged = []
                seen = set()

                def add(value, display):
                    if str(value) in seen:
                        return
                    seen.add(str(value))
                    merged.append((value, display))

                for value, display in spec.options:
                    if value == "":
                        add(value, display)
                for value in values:
                    add(value, value)
                # The server default is always accepted, even when --help
                # documents it only in the "(default: ...)" note.
                if spec.default is not None:
                    add(spec.default, spec.default)
                if merged:
                    updates["options"] = merged
        if spec.off_flag and spec.off_flag not in help_text:
            updates["off_flag"] = None

        specs.append(spec.clone(**updates) if updates else spec)
    return specs

def _prompt_spec(spec, current):
    """Prompt for one parameter. Returns the new value, or None to keep current."""
    label = f"{spec.label} {spec.flag}"
    if spec.kind == "select" and spec.options:
        return _prompt_select(spec, current)

    if spec.kind == "bool":
        note = spec.help
        if not spec.off_flag and spec.default:
            note = (note + "; " if note else "") + "no --no- flag in this build"
        print(f"  {label}: {'on' if current else 'off'}"
              f"{'  ' + note if note else ''}")
        if _prompt_yes(f"    {spec.label}?", default=bool(current)):
            return True
        return False

    kind_label = {"int": "integer", "float": "number", "str": "text"}.get(
        spec.kind, "value")
    suffix = f"  ({spec.help})" if spec.help else ""
    val = _prompt_input(f"  {label}{suffix} [{kind_label}]",
                        None if current is None else str(current))
    if val is None:
        return current
    return _coerce(spec.kind, val, current)


def _print_numbered(options, current_index, columns=None):
    """Print a numbered option list, in columns when it is long."""
    cells = []
    for i, (_value, display) in enumerate(options, 1):
        mark = " ◀" if i == current_index else ""
        cells.append(f"{i:>3}. {display}{mark}")

    if columns is None:
        columns = 6 if len(cells) > 12 else 1
    if columns <= 1:
        for cell in cells:
            print(f"    {cell}")
        return

    width = max(len(c) for c in cells) + 2
    for start in range(0, len(cells), columns):
        row = cells[start:start + columns]
        print("    " + "".join(c.ljust(width) for c in row).rstrip())


def _prompt_select(spec, current):
    """Numbered select list with a trailing custom-value entry."""
    options = list(spec.options)
    custom_index = len(options) + 1
    suffix = f"  ({spec.help})" if spec.help else ""

    current_index = 0
    for i, (value, _display) in enumerate(options, 1):
        if _values_equal(value, current):
            current_index = i
            break

    print(f"\n  {spec.label} {spec.flag}{suffix}  [current: {_display_value(current)}]")
    _print_numbered(options, current_index)
    print(f"    {custom_index:>3}. custom value...")

    raw = _prompt_input("    Selection", str(current_index) if current_index else "")
    if raw is None or raw == "":
        return current

    try:
        index = int(raw)
    except ValueError:
        # Not a number: treat it as a custom value.
        return _coerce_select(spec, raw, current)

    if index == custom_index:
        val = _prompt_input(f"    Custom {spec.label} value", None)
        if val is None or val == "":
            return current
        return _coerce_select(spec, val, current)

    if 1 <= index <= len(options):
        return options[index - 1][0]

    # Out of range: a number this large is a custom value, not a typo.
    val = _prompt_input(f"    Custom {spec.label} value (1-{custom_index}, or a value)", None)
    if val is None or val == "":
        print(f"    Invalid selection. Use 1-{custom_index}.")
        return current
    return _coerce_select(spec, val, current)


def _display_value(value) -> str:
    if value is None or value == "":
        return "(unset)"
    return str(value)


def _values_equal(a, b) -> bool:
    if a is None or b is None:
        return a is b or (not a and not b)
    return str(a) == str(b)


def _coerce_select(spec, raw, fallback):
    """Coerce a custom select entry to the type of the spec's own values.

    ``ctx_size`` is a select over integers, so a typed-in "262144" must land as
    an int rather than the string a plain free-form prompt would produce.
    """
    sample = next((v for v, _ in spec.options if isinstance(v, int)), None)
    kind = "int" if sample is not None else spec.kind
    if kind == "int":
        kind = "float" if any(isinstance(v, float) for v, _ in spec.options) else "int"
    return _coerce(kind, raw, fallback)


def _coerce(kind, raw, fallback):
    """Convert free-form input to the parameter's type, re-prompting on error."""
    while True:
        try:
            if kind == "int":
                return int(raw)
            if kind == "float":
                return float(raw)
            if kind == "bool":
                return raw.strip().lower() in ("on", "true", "1", "yes", "y")
            return raw
        except ValueError:
            fallback_display = "" if fallback is None else f" [{fallback}]"
            raw = _prompt_input(
                f"    Not a valid {kind}{fallback_display}, try again", None)
            if raw is None or raw == "":
                return fallback


def cmd_create_base():
    """Interactive wizard to create a new template from base.json."""
    from llama_monitor.config import TEMPLATES_DIR, CONFIG_DIR
    
    base = _read_base_template()
    
    # Step 1: Select model
    models = _get_model_files()
    if not models:
        print("No GGUF model files found in ~/models/")
        sys.exit(1)
    
    print("\nAvailable models:")
    for i, m in enumerate(models, 1):
        size_mb = m.stat().st_size / (1024 * 1024)
        print(f"  {i}. {m.name} ({size_mb:.0f} MB)")
    
    model_idx = _prompt_input("Select model number", "1")
    try:
        idx = int(model_idx) - 1
        selected_model = models[idx]
    except (ValueError, IndexError):
        print(f"Invalid selection. Use 1-{len(models)}.")
        sys.exit(1)
    
    # Step 2: Template name
    suggested_name = os.path.splitext(selected_model.name)[0].lower()
    suggested_name = suggested_name.replace("-", "").replace("_", "").replace(".", "")
    # Keep it readable: split by uppercase or common patterns
    template_name = _prompt_input("Template name", suggested_name)
    
    # Step 3: All parameters
    print(f"\nTemplate: {template_name}")
    print("Parameter prompts (Enter to accept default):\n")
    
    new_template = dict(base)  # Start from base defaults
    new_template["model_path"] = str(selected_model)
    new_template["name"] = template_name
    
    # Collect all parameters (binary + all others) via interactive prompts
    _collect_parameters(new_template)

    # Save
    output_file = TEMPLATES_DIR / f"{template_name}.json"
    with open(output_file, "w") as f:
        json.dump(new_template, f, indent=4)
    
    print(f"\n✓ Template saved: {output_file}")
    print(f"  Model: {selected_model.name}")
    print(f"  To activate: llama-monitor reload-template {template_name}")


def cmd_create_from(template_name: Optional[str] = None):
    """Interactive wizard to copy an existing template.
    
    If template_name is provided, copy from that template.
    Otherwise, prompt the user to select from available templates.
    
    Flow:
      1. Select source template
      2. Select new model from ~/models/
      3. Template name (suggested from new model)
      4. Port
      5. Save
    """
    from llama_monitor.config import TEMPLATES_DIR, list_template_names, load_template
    
    templates = list_template_names()
    if not templates:
        print("No existing templates to copy from.")
        sys.exit(1)
    
    # Step 1: Select source template
    if not template_name:
        print("\nAvailable templates to copy from:")
        for i, t in enumerate(templates, 1):
            try:
                tpl = load_template(t)
                model_name = os.path.basename(tpl.get("model_path", "unknown"))
                port = tpl.get("port", 8080)
                print(f"  {i}. {t} (model: {model_name}, port: {port})")
            except Exception as e:
                print(f"  {i}. {t} (error: {e})")
        
        src_idx = _prompt_input("Select source template number", "1")
        try:
            idx = int(src_idx) - 1
            source_name = templates[idx]
        except (ValueError, IndexError):
            print(f"Invalid selection. Use 1-{len(templates)}.")
            sys.exit(1)
    else:
        source_name = template_name
        if source_name not in templates:
            print(f"Error: Template '{source_name}' not found.")
            print(f"Available templates: {', '.join(templates)}")
            sys.exit(1)
    
    source = load_template(source_name)
    print(f"\nBase template: {source_name}")
    
    # Step 2: Select new model
    models_dir = Path(os.path.expanduser("~/models"))
    if not models_dir.exists():
        print("No ~/models/ directory found.")
        sys.exit(1)
    all_models = sorted([f for f in models_dir.iterdir() if f.is_file() and f.suffix == ".gguf"])
    if not all_models:
        print("No GGUF model files found in ~/models/.")
        sys.exit(1)
    
    print("\nAvailable models:")
    for i, m in enumerate(all_models, 1):
        size_mb = m.stat().st_size / (1024 * 1024)
        print(f"  {i}. {m.name} ({size_mb:.0f} MB)")
    
    model_idx = _prompt_input("Select model number", "1")
    try:
        idx = int(model_idx) - 1
        selected_model = all_models[idx]
    except (ValueError, IndexError):
        print(f"Invalid selection. Use 1-{len(all_models)}.")
        sys.exit(1)
    
    # Step 3: Template name — suggested from new model
    suggested_name = os.path.splitext(selected_model.name)[0].lower()
    suggested_name = suggested_name.replace("-", "").replace("_", "").replace(".", "")
    template_name = _prompt_input("New template name", suggested_name)
    
    # Collect all parameters (binary + port + all others) via interactive prompts
    _collect_parameters(source)

    # Update model_path and name in the copy
    source["model_path"] = str(selected_model)
    if "name" in source:
        del source["name"]
    
    # Save
    output_file = TEMPLATES_DIR / f"{template_name}.json"
    with open(output_file, "w") as f:
        json.dump(source, f, indent=4)
    
    print(f"\n✓ Template saved: {output_file}")
    print(f"  From: {source_name}")
    print(f"  Model: {selected_model.name}")
    print(f"  To activate: llama-monitor reload-template {template_name}")


def _select_template(action: str):
    """List the templates and return the one the user picks.

    Returns the template name, or None when the user aborts.
    """
    from llama_monitor.config import list_template_names, load_template

    templates = list_template_names()
    if not templates:
        print("No templates available.")
        sys.exit(1)

    print(f"\nAvailable templates:")
    for i, name in enumerate(templates, 1):
        try:
            tpl = load_template(name)
            model_name = os.path.basename(tpl.get("model_path", "unknown"))
            port = tpl.get("port", 8080)
            print(f"  {i}. {name} (model: {model_name}, port: {port})")
        except Exception as e:
            print(f"  {i}. {name} (error: {e})")

    sel = _prompt_input(f"Select template to {action}", "1")
    try:
        idx = int(sel) - 1
    except ValueError:
        print("Aborted.")
        return None
    if not 0 <= idx < len(templates):
        print(f"Invalid selection. Use 1-{len(templates)}.")
        return None
    return templates[idx]


def _resolve_parameter(name: str):
    """Look up a spec by template key or llama-server flag.

    Returns ``(spec, note)``. ``note`` explains a flag rejected by the
    selected build, in which case ``spec`` is None.
    """
    from llama_monitor.params import PARAM_SPECS, SPEC_BY_KEY

    specs = _runtime_specs(None)
    available = {spec.key: spec for spec in specs}

    raw = name.strip().lstrip("-")
    if raw.startswith("no-"):
        raw = raw[3:]  # accept the negated spelling, e.g. --no-jinja
    key = raw
    for candidate in (raw, raw.replace("-", "_")):
        if candidate in available:
            return available[candidate], None
        if candidate in SPEC_BY_KEY:
            return None, (f"Parameter '{candidate}' ({SPEC_BY_KEY[candidate].flag}) "
                          f"is not supported by the selected llama-server build.")
    # Separators are optional: "cachetypek" and "cache-type-k" both work.
    squashed = key.replace("_", "").replace("-", "")
    for candidate in available:
        if candidate.replace("_", "") == squashed:
            return available[candidate], None
    for candidate in SPEC_BY_KEY:
        if candidate.replace("_", "") == squashed:
            return None, (f"Parameter '{candidate}' ({SPEC_BY_KEY[candidate].flag}) "
                          f"is not supported by the selected llama-server build.")
    for spec in PARAM_SPECS:
        if spec.flag == name or spec.flag == f"--{key}":
            return available.get(spec.key), (
                f"Parameter '{spec.key}' ({spec.flag}) is not supported by "
                f"the selected llama-server build."
                if spec.key not in available else None)

    note = "Unknown parameter."
    close = _close_keys(key, available)
    if close:
        note += " Did you mean: " + ", ".join(close[:8]) + "?"
    return None, note


def _close_keys(key: str, available: dict) -> list:
    """Parameter keys that ``key`` plausibly refers to.

    Substring matches first, then a subsequence match so ``top_ky`` and
    ``ctxsize`` still find ``top_k`` and ``ctx_size``.
    """
    needle = key.lower().replace("-", "").replace("_", "")
    if not needle:
        return []

    def normalized(name: str) -> str:
        return name.lower().replace("_", "")

    contains = [k for k in available if needle in normalized(k)]
    if contains:
        return contains

    def is_subsequence(short, long):
        it = iter(long)
        return all(ch in it for ch in short)

    # Subsequence matching gets noisy ("ctxt" would hit every ctx_* key), so
    # require the first two characters to line up.
    prefix = needle[:2]
    subsequence = [k for k in available
                   if normalized(k).startswith(prefix)
                   and is_subsequence(needle, normalized(k))]
    if subsequence:
        return subsequence

    # Last resort: catch typos like "top_ky" or "ctxsize".
    import difflib
    return difflib.get_close_matches(needle, list(available), n=3, cutoff=0.7)


def _edit_single_parameter(template, name):
    """Prompt for exactly one parameter and apply it to ``template``.

    Returns True when the template changed.
    """
    from llama_monitor.params import (PARAM_SPECS, PARAM_FLAGS, SPEC_BY_KEY,
                                      is_default)

    spec, note = _resolve_parameter(name)
    if spec is None:
        if note:
            print(f"  {note}")
        return False

    changed = False
    if not spec.active(template):
        try:
            changed = bool(_prompt_prerequisites(spec, template))
        except _Aborted:
            print("  Aborted.")
            return False
        if not changed:
            return False

    present = spec.key in template
    current = template.get(spec.key, spec.default)
    default_note = ""
    if not present:
        default_note = f" [not set; llama-server default: {_display_value(spec.default)}]"

    print(f"\n{spec.label} {spec.flag}{default_note}")
    value = _prompt_spec(spec, current)

    if value is not None and not _values_equal(value, current):
        template[spec.key] = value
        print(f"  -> {spec.key} = {value!r}")
        changed = True
        if is_default(spec, value):
            print(f"  Note: {value!r} is llama-server's default for {spec.key}.")
    elif present:
        # Nothing new chosen: offer removal as the other way to "edit".
        if _prompt_yes(f"  Remove {spec.key} from the template?", default=False):
            del template[spec.key]
            print(f"  -> removed {spec.key}")
            changed = True
    elif value is not None and is_default(spec, value):
        # The chosen value matches llama-server's default, so it would normally
        # stay out of the template — ask, since naming it explicitly is a valid
        # way to pin a value.
        if _prompt_yes(f"  Store {spec.key} anyway (it equals the "
                       f"llama-server default)?", default=False):
            template[spec.key] = value
            print(f"  -> {spec.key} = {value!r}")
            changed = True

    return changed


class _Aborted(Exception):
    """Raised when the user declines a prompt that was a prerequisite."""


def _prompt_prerequisites(spec, template, _depth=0):
    """Ask for the related parameters ``spec`` depends on, until it applies.

    Mutates ``template`` and returns the list of keys changed. Raises
    :class:`_Aborted` when the user declines, and returns ``[]`` when no
    prerequisite can be offered.
    """
    from llama_monitor.params import SPEC_BY_KEY, dependencies_of

    keys = dependencies_of(spec, template)
    reason = ", ".join(f"{k}={template.get(k, '')!r}" for k in keys)
    print(f"\n{spec.key} ({spec.flag}) does not apply to this template"
          f"{f' because {reason}' if reason else ''}.")

    changed = []
    for key in keys:
        prereq = SPEC_BY_KEY.get(key)
        if prereq is None:
            continue
        current = template.get(key, prereq.default)
        print(f"  {key} is currently {_display_value(current)!r}")
        value = _prompt_spec(prereq, current)
        if value is None or _values_equal(value, current):
            continue
        template[key] = value
        print(f"  -> {key} = {value!r}")
        changed.append(key)

    if spec.active(template) or not keys:
        return changed

    # Still inactive: the prerequisite may itself be gated. Recurse rather than
    # hardcoding a chain.
    if _depth < 3:
        for key in keys:
            prereq = SPEC_BY_KEY.get(key)
            if prereq is not None and not prereq.active(template):
                changed += _prompt_prerequisites(prereq, template, _depth + 1)

    if not spec.active(template):
        print(f"  {spec.key} still does not apply with these values.")
        raise _Aborted()
    return changed


def _save_template(template, template_name):
    from llama_monitor.config import TEMPLATES_DIR

    output_file = TEMPLATES_DIR / f"{template_name}.json"
    with open(output_file, "w") as f:
        json.dump(template, f, indent=4)
    print(f"\n✓ Template saved: {output_file}")
    print(f"  To apply it: llama-monitor reload-template {template_name}")
    return output_file


def cmd_edit(parameter: Optional[str] = None):
    """Edit the parameters of an existing template.

    ``llama-monitor edit`` walks every parameter of the selected template with
    the same wizard used to create one, so values can be changed, added and
    removed. ``llama-monitor edit <name>`` prompts for just that one parameter.
    """
    from llama_monitor.config import list_template_names, load_template

    templates = list_template_names()
    if not templates:
        print("No templates to edit.")
        sys.exit(1)

    # Step 1: always let the user select the template to edit.
    template_name = _select_template("edit")
    if not template_name:
        sys.exit(1)

    template = load_template(template_name)
    print(f"\nEditing: {template_name}")
    if template.get("name"):
        print(f"  Model: {template.get('name')}")

    # Step 2: edit the named parameter, or walk the wizard.
    if parameter:
        changed = _edit_single_parameter(template, parameter)
        if not changed:
            print("\nNothing changed.")
            return
        _save_template(template, template_name)
        return

    _collect_parameters(template, ask_all=True)
    _save_template(template, template_name)


def cmd_delete(template_name: Optional[str] = None):
    """Interactive wizard to delete an existing template.

    Lists all available templates and prompts for selection. If template_name
    is provided as positional arg, deletes that one.
    """
    from llama_monitor.config import TEMPLATES_DIR, list_template_names, load_template

    templates = list_template_names()
    if not templates:
        print("No templates available to delete.")
        sys.exit(1)

    # Step 1: Select template to delete
    if not template_name:
        print("\nAvailable templates:")
        for i, t in enumerate(templates, 1):
            try:
                tpl = load_template(t)
                model_name = os.path.basename(tpl.get("model_path", "unknown"))
                port = tpl.get("port", 8080)
                print(f"  {i}. {t} (model: {model_name}, port: {port})")
            except Exception as e:
                print(f"  {i}. {t} (error: {e})")

        sel_idx = _prompt_input("Select template number to delete", "")
        try:
            idx = int(sel_idx) - 1
            chosen = templates[idx]
        except (ValueError, IndexError):
            print("No selection or invalid selection. Aborting.")
            sys.exit(1)
    else:
        chosen = template_name
        if chosen not in templates:
            print(f"Error: Template '{chosen}' not found.")
            print(f"Available templates: {', '.join(templates)}")
            sys.exit(1)

    print(f"\nTemplate to delete: {chosen}")
    tpl = None
    try:
        tpl = load_template(chosen)
        print(f"  Model: {tpl.get('model_path', 'unknown')}")
        print(f"  Port: {tpl.get('port', 8080)}")
    except Exception as e:
        print(f"  (could not read template details: {e})")

    # Determine the active template (from daemon state or config)
    active = None
    try:
        from llama_monitor.daemon import get_global_state
        state = get_global_state()
        if state is not None:
            active = state.active_template or None
    except Exception:
        pass
    if not active:
        from llama_monitor.config import load_config
        try:
            active = load_config().get("active_template") or None
        except Exception:
            active = None

    if active == chosen:
        print("\n⚠ WARNING: This is the ACTIVE template.")
        print("  Deleting it will leave llama-monitor with no active template.")
        print("  Activate another template first, then delete.")
        if not _prompt_yes("Delete anyway?", default=False):
            print("Aborted.")
            sys.exit(0)

    if not _prompt_yes(f"Delete template '{chosen}'?", default=False):
        print("Aborted.")
        sys.exit(0)

    # Step 2: Delete
    output_file = TEMPLATES_DIR / f"{chosen}.json"
    if not output_file.exists():
        print(f"Error: Template file not found: {output_file}")
        sys.exit(1)
    try:
        output_file.unlink()
        print(f"\n✓ Template deleted: {output_file}")
    except OSError as e:
        print(f"Error deleting template: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 3: Optionally delete the model file the template pointed at
    _maybe_delete_model(chosen, tpl)


def _maybe_delete_model(deleted_template: str, tpl: Optional[dict]):
    """Offer to delete the model file referenced by the deleted template.

    Skipped if the model path is unknown/missing, or if another remaining
    template still uses the same model file.
    """
    if not tpl:
        return

    model_path = tpl.get("model_path")
    if not model_path:
        return

    model_file = Path(model_path).expanduser()
    if not model_file.is_file():
        return

    # Another template still referencing this model? Then keep the file.
    from llama_monitor.config import list_template_names, load_template
    for other in list_template_names():
        try:
            other_path = load_template(other).get("model_path")
        except Exception:
            continue
        if other_path and Path(other_path).expanduser() == model_file:
            print(f"Model file kept — still used by template '{other}': {model_file}")
            return

    try:
        size_mb = model_file.stat().st_size / (1024 * 1024)
    except OSError:
        size_mb = 0

    print(f"\nModel file: {model_file} ({size_mb:.0f} MB)")
    if not _prompt_yes("Also delete the model file?", default=False):
        print("Model file kept.")
        return

    try:
        model_file.unlink()
        print(f"✓ Model deleted: {model_file}")
    except OSError as e:
        print(f"Error deleting model file: {e}", file=sys.stderr)


def cmd_models():
    """List GGUF files that don't have a template yet."""
    models = _get_model_files()
    if not models:
        print("No GGUF files found in ~/models/")
        return
    
    existing = _models_with_templates()
    print("\nModels without templates:")
    has_missing = False
    for m in models:
        if m.name not in existing:
            size_mb = m.stat().st_size / (1024 * 1024)
            print(f"  - {m.name} ({size_mb:.0f} MB)")
            has_missing = True
    
    if not has_missing:
        print("  All models already have templates.")
    else:
        print(f"\n  {sum(1 for m in models if m.name not in existing)} model(s) without template.")


def cmd_set():
    """Interactive selector to set active_template in config.json."""
    from llama_monitor.config import load_config, list_template_names, load_template, save_active_template
    
    templates = list_template_names()
    if not templates:
        print("No templates available.")
        sys.exit(1)
    
    # Show current active template
    try:
        config = load_config()
        current = config.get("active_template")
    except Exception:
        current = None
    
    print(f"Current active template: {current or '(none)'}\n")
    print("Available templates:")
    for i, t in enumerate(templates, 1):
        mark = " ◀ current" if t == current else ""
        try:
            tpl = load_template(t)
            model_name = os.path.basename(tpl.get("model_path", "unknown"))
            port = tpl.get("port", 8080)
            print(f"  {i}. {t} (model: {model_name}, port: {port}){mark}")
        except Exception as e:
            print(f"  {i}. {t} (error: {e}){mark}")
    
    sel = _prompt_input("Select template number", str(templates.index(current) + 1) if current and current in templates else "1")
    try:
        idx = int(sel) - 1
        chosen = templates[idx]
    except (ValueError, IndexError):
        print(f"Invalid selection. Use 1-{len(templates)}.")
        sys.exit(1)
    
    save_active_template(chosen)
    print(f"\n✓ Active template set to: {chosen}")


def cmd_logs(lines: int):
    """Show daemon and llama-server logs from systemd journal.
    
    Uses journalctl to get the last N lines from the llama-monitor service.
    Daemon logs appear normally; llama-server logs are prefixed with [llama-server].
    """
    try:
        result = subprocess.run(
            ["journalctl", "-u", "llama-monitor", "-n", str(lines), "--no-pager"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout.strip()
        if not output:
            print("No log entries found.")
        else:
            # Separate daemon logs from llama-server logs
            print("=== llama-monitor logs (last {}) ===".format(lines))
            print()
            print(output)
    except subprocess.TimeoutExpired:
        print("Error: journalctl timed out.", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: journalctl not found. Is systemd running?", file=sys.stderr)
        sys.exit(1)


def _api_post(path: str) -> bool:
    """POST to the daemon API and return success/failure."""
    import httpx
    
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.post(f"http://127.0.0.1:9500{path}")
            if resp.status_code == 200:
                return True
            print(f"API error: {resp.status_code} - {resp.text}", file=sys.stderr)
            return False
    except httpx.ConnectError:
        print("Cannot connect to llama-monitor daemon. Is it running?", file=sys.stderr)
        print("Try: sudo systemctl start llama-monitor", file=sys.stderr)
        return False


def cmd_llm_stop():
    """Ask the daemon to stop the llama-server and keep it stopped."""
    print("Stopping llama-server (daemon will not restart it until 'llm-start')...")
    success = _api_post("/api/v1/llm-stop")
    sys.exit(0 if success else 1)


def cmd_llm_start():
    """Ask the daemon to start the llama-server and resume monitoring."""
    print("Starting llama-server (daemon resumes monitoring)...")
    success = _api_post("/api/v1/llm-start")
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
