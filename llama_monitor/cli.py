#!/home/n4rf/llama-monitor/.venv/bin/python3
# -*- coding: utf-8 -*-
"""CLI interface for llama-monitor: start, stop, restart, status, enable/disable autostart."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


def _require_sudo():
    """Ensure the command runs as root. If not, re-execute with sudo."""
    if os.geteuid() == 0:
        return  # Already root
    
    # Re-run the full command with sudo — the system will prompt for password.
    result = subprocess.run(["sudo"] + sys.argv)
    sys.exit(result.returncode)


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

    # delete — interactive wizard to delete an existing template
    delete_parser = subparsers.add_parser("delete", help="Delete an existing template")
    delete_parser.add_argument("template", nargs="?", default=None, help="Template name to delete (default: interactive selection)")
    
    # models — show GGUF files without a template
    subparsers.add_parser("models", help="List GGUF models without a template")
    
    # set — interactive selector to set active_template
    set_parser = subparsers.add_parser("set", help="Set the active template")
    
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
    elif command == "delete":
        cmd_delete(args.template)
    elif command == "models":
        cmd_models()
    elif command == "set":
        cmd_set()
    elif command == "logs":
        cmd_logs(args.lines)
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


def cmd_stop():
    """Send SIGTERM to the running daemon."""
    import signal as sig
    
    pid_file = _get_pid_file()
    try:
        if not pid_file.exists():
            fallback = _get_pid_file_fallback()
            if fallback and fallback.exists():
                pid_file = fallback
            else:
                print("No daemon PID file found. Is the daemon running?")
                sys.exit(1)
    except PermissionError:
        print("No daemon PID file found. Is the daemon running?")
        sys.exit(1)
    
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        print(f"Invalid PID file: {pid_file}")
        sys.exit(1)
    except PermissionError:
        print("Permission denied reading PID file. Is the daemon running as a different user?")
        sys.exit(1)
    
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
        # We can't signal the daemon directly (e.g. root-owned, run as n4rf).
        # Shut it down via the HTTP API.
        print("Daemon owned by another user. Shutting down via API...")
        import httpx
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.post("http://127.0.0.1:9500/api/v1/shutdown")
                if resp.status_code == 200:
                    print("Shutdown initiated. Waiting for daemon to stop...")
                    for _ in range(20):
                        time.sleep(0.5)
                        try:
                            os.kill(pid, 0)
                        except ProcessLookupError:
                            print("Daemon stopped.")
                            pid_file.unlink(missing_ok=True)
                            sys.exit(0)
                        except PermissionError:
                            pass  # still running
                    print("Daemon did not stop within timeout.", file=sys.stderr)
                    sys.exit(1)
                else:
                    print(f"API error: {resp.status_code} - {resp.text}",
                          file=sys.stderr)
                    sys.exit(1)
        except httpx.ConnectError:
            print("Cannot connect to daemon API.", file=sys.stderr)
            print("Try: sudo systemctl stop llama-monitor", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Shutdown failed: {e}", file=sys.stderr)
            sys.exit(1)


def cmd_restart():
    """Restart the daemon."""
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


def _collect_parameters(template):
    """Interactively prompt for the binary + all template parameters.

    Uses the current values in ``template`` as defaults and updates
    ``template`` in place. Returns the template.
    """
    # Binary selection
    binaries = _find_llama_servers()
    if binaries:
        default_bin = template.get("binary") or binaries[0]
        chosen = _prompt_binary_selection(default_bin)
        if chosen:
            template["binary"] = chosen
            print(f"  -> {chosen}")

    defaults = dict(template)

    # Port
    v = _prompt_input("  Port", str(defaults.get("port", 8080)))
    if v:
        template["port"] = int(v)

    # Bind address
    v = _prompt_input("  Bind address", defaults.get("bind", "127.0.0.1"))
    if v:
        template["bind"] = v

    # Context size
    v = _prompt_input("  Context size", str(defaults.get("ctx_size", 262144)))
    if v:
        template["ctx_size"] = int(v)

    # GPU layers
    v = _prompt_input("  GPU layers", str(defaults.get("n_gpu_layers", 99)))
    if v:
        template["n_gpu_layers"] = int(v)

    # n_cpu_moe
    v = _prompt_input("  n_cpu_moe", str(defaults.get("n_cpu_moe", 41)))
    if v:
        template["n_cpu_moe"] = int(v)

    # Flash attention
    v = _prompt_input("  Flash attention", defaults.get("flash_attn", "on"))
    if v:
        template["flash_attn"] = v

    # Parallel
    v = _prompt_input("  Parallel", str(defaults.get("parallel", 1)))
    if v:
        template["parallel"] = int(v)

    # Load mode
    v = _prompt_input("  Load mode", defaults.get("load_mode", "mmap+mlock"))
    if v:
        template["load_mode"] = v

    # Cache type K
    v = _prompt_input("  Cache type K", defaults.get("cache_type_k", "turbo4"))
    if v:
        template["cache_type_k"] = v

    # Cache type V
    v = _prompt_input("  Cache type V", defaults.get("cache_type_v", "turbo3"))
    if v:
        template["cache_type_v"] = v

    # Jinja
    v = _prompt_input("  Jinja format (on/off)", str(defaults.get("jinja", True)))
    if v:
        template["jinja"] = v.lower() in ("on", "true", "1")

    # Temperature
    v = _prompt_input("  Temperature", str(defaults.get("temperature", 1.0)))
    if v:
        template["temperature"] = float(v)

    # Top-p
    v = _prompt_input("  Top-p", str(defaults.get("top_p", 0.95)))
    if v:
        template["top_p"] = float(v)

    # Top-k
    v = _prompt_input("  Top-k", str(defaults.get("top_k", 20)))
    if v:
        template["top_k"] = int(v)

    # Min-p
    v = _prompt_input("  Min-p", str(defaults.get("min_p", 0.00)))
    if v:
        template["min_p"] = float(v)

    # Spec type
    v = _prompt_input("  Spec type", defaults.get("spec_type", "draft-mtp"))
    if v:
        template["spec_type"] = v

    # Draft n-max
    v = _prompt_input("  Draft n-max", str(defaults.get("spec_draft_n_max", 2)))
    if v:
        template["spec_draft_n_max"] = int(v)

    return template


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


if __name__ == "__main__":
    main()
