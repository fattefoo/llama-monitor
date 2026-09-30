"""Main daemon: process lifecycle, health-check loop, config reload."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

def _get_home_dir() -> str:
    """Get the home directory, handling sudo correctly.
    
    When running via sudo, $HOME points to /root. We use SUDO_USER
    to find the real user's home directory. SUDO_USER takes priority
    over HOME.
    """
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        return os.path.expanduser(f"~{sudo_user}")
    return os.path.expanduser("~")


CONFIG_HOME = Path(_get_home_dir())
PID_FILE = CONFIG_HOME / ".config" / "llama-monitor" / "llama-monitor.pid"

# Global daemon state (singleton)
_global_state: "Optional[DaemonState]" = None
_daemon_loop = None  # EventLoop used by await_health — set for shutdown interrupt
_global_lock = threading.Lock()


@dataclass
class DaemonState:
    """Current daemon state shared across modules."""
    pid: Optional[int] = None
    active_template: Optional[str] = None
    active_template_name: Optional[str] = None
    model_path: Optional[str] = None
    process_state: str = "stopped"  # stopped, starting, running, crashed, checking, waiting, killing
    health_ok: bool = False
    server_owned: bool = False  # True if we started this server ourselves
    restart_event: threading.Event = field(default_factory=threading.Event)
    shutdown_event: threading.Event = field(default_factory=threading.Event)


def get_global_state() -> DaemonState:
    """Get the global daemon state singleton."""
    return _global_state


def set_global_state(state: DaemonState):
    """Set the global daemon state singleton."""
    global _global_state
    with _global_lock:
        _global_state = state


def _write_pid(pid: int) -> None:
    """Write PID file."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def _read_pid() -> Optional[int]:
    """Read PID from file."""
    if not PID_FILE.exists():
        return None
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip())
    except (ValueError, OSError):
        return None


def _remove_pid() -> None:
    """Remove PID file."""
    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except OSError:
            pass


def find_llama_server_by_port(port: int) -> Optional[dict]:
    """Find a running llama-server process on a specific port.
    
    Returns dict with pid, cmdline info or None.
    """
    try:
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if f":{port} " not in line and f":{port}\t" not in line:
                continue
            if "pid=" in line:
                try:
                    start = line.index("pid=")
                    end = line.index(",", start)
                    pid = int(line[start+4:end])
                    # Get process name
                    proc_name = "unknown"
                    try:
                        with open(f"/proc/{pid}/comm") as f:
                            proc_name = f.read().strip()
                    except (FileNotFoundError, PermissionError):
                        pass
                    # Get cmdline
                    cmdline = ""
                    try:
                        with open(f"/proc/{pid}/cmdline") as f:
                            tokens = f.read().split("\x00")
                        cmdline = " ".join(tokens[:3]) + ("..." if len(tokens) > 3 else "")
                    except (FileNotFoundError, PermissionError):
                        pass
                    return {"pid": pid, "process_name": proc_name, "cmdline": cmdline}
                except (ValueError, IndexError):
                    pass
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _build_llama_args(template: dict, config: dict) -> list:
    """Build llama-server CLI arguments from template.
    
    Supports all llama-server parameters. Key-value pairs use the JSON key
    mapped to the long-form flag (e.g. "n_cpu_moe" → "--n-cpu-moe").
    Boolean flags (no value) use keys like "no_mmap": true.
    """
    binary = template.get("binary", config["default_binary"])
    bind = template.get("bind", "127.0.0.1")
    port = template.get("port", 8080)
    
    args = [binary]
    args.extend(["--model", template["model_path"]])
    args.extend(["--host", bind])
    args.extend(["--port", str(port)])
    
    # --- Key-value parameters ---
    _add_kv(args, template, "ctx_size", "--ctx-size")
    _add_kv(args, template, "n_gpu_layers", "--n-gpu-layers")
    _add_kv(args, template, "n_cpu_moe", "--n-cpu-moe")
    _add_kv(args, template, "parallel", "--parallel")
    _add_kv(args, template, "spec_type", "--spec-type")
    _add_kv(args, template, "spec_draft_n_max", "--spec-draft-n-max")
    _add_kv(args, template, "cache_type_k", "--cache-type-k")
    _add_kv(args, template, "cache_type_v", "--cache-type-v")
    _add_kv(args, template, "chat_format", "--chat-format")
    _add_kv(args, template, "temperature", "--temp")
    _add_kv(args, template, "top_p", "--top-p")
    _add_kv(args, template, "top_k", "--top-k")
    _add_kv(args, template, "min_p", "--min-p")
    _add_kv(args, template, "seed", "--seed")
    _add_kv(args, template, "logit_bias", "--logit-bias")
    _add_kv(args, template, "penalty_last_n", "--penalty-last-n")
    _add_kv(args, template, "penalty_repeat", "--penalty-repeat")
    _add_kv(args, template, "penalty_freq", "--penalty-freq")
    _add_kv(args, template, "penalty_present", "--penalty-penalty")
    _add_kv(args, template, "mirostat", "--mirostat")
    _add_kv(args, template, "mirostat_tau", "--mirostat-tau")
    _add_kv(args, template, "mirostat_eta", "--mirostat-eta")
    _add_kv(args, template, "split_mode", "--split-mode")
    _add_kv(args, template, "row_split", "--row-split")
    _add_kv(args, template, "tensor_split", "--tensor-split")
    _add_kv(args, template, "lora_base", "--lora-base")
    _add_kv(args, template, "lora_path", "--lora-path")
    _add_kv(args, template, "num_gpu", "--num-gpu")
    _add_kv(args, template, "rpc", "--rpc")
    _add_kv(args, template, "flash_attn", "--flash-attn")
    _add_kv(args, template, "load_mode", "--load-mode")
    
    # --- Boolean flags ---
    if template.get("jinja"):
        args.append("--jinja")
    if template.get("metrics"):
        args.append("--metrics")
    
    return args


def _add_kv(args: list, template: dict, key: str, flag: str) -> None:
    """Append key-value arg if the key exists in template."""
    if key in template:
        args.extend([flag, str(template[key])])


class Daemon:
    """Main daemon controller."""
    
    def __init__(self, config: dict):
        self.config = config
        self.proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self._health_url: str = ""
        self._current_port: int = 8080
        # PID of the server we ourselves started (for stop/kill decisions)
        self.started_server_pid: Optional[int] = None
    
    def start(self, template_name: str) -> bool:
        """Start llama-server with the given template."""
        from llama_monitor.config import load_template
        
        try:
            template = load_template(template_name)
        except Exception as e:
            print(f"Error loading template '{template_name}': {e}", file=sys.stderr)
            return False
        
        self.set_active_template(template_name, template.get("name", template_name))
        bind = template.get("bind", "127.0.0.1")
        port = template.get("port", 8080)
        self._health_url = f"http://{bind}:{port}/health"
        
        # Store model info for status reporting
        state = get_global_state()
        state.model_path = template.get("model_path")
        
        args = _build_llama_args(template, self.config)
        
        state = get_global_state()
        state.process_state = "starting"
        
        print(f"Starting llama-server with template: {template.get('name', template_name)}", flush=True)
        print(f"Args: {' '.join(args)}", flush=True)
        print(f"Health URL: {self._health_url}", flush=True)
        
        try:
            self.proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                universal_newlines=True,
                close_fds=True,
                start_new_session=True,
            )
            state.pid = self.proc.pid
            self.started_server_pid = self.proc.pid
            state.server_owned = True
            
            # Start log reader thread
            threading.Thread(target=self._read_logs, args=(self.proc,), daemon=True, name="llama-log-reader").start()
            
            # Brief wait to see if it crashes immediately
            self.proc.wait(timeout=5)
            
            if self.proc.returncode == 0:
                state.process_state = "running"
                print(f"llama-server started (PID {self.proc.pid}) - exited cleanly? Checking again...", flush=True)
                # Actually, if it exited cleanly after 5s, something might be wrong
                # Let's keep monitoring
            else:
                state.process_state = "crashed"
                print(f"llama-server crashed with code {self.proc.returncode}", file=sys.stderr, flush=True)
            
        except subprocess.TimeoutExpired:
            # Process is still running after 5s - that's good!
            state.process_state = "running"
            print(f"llama-server started successfully (PID {self.proc.pid})", flush=True)
        except FileNotFoundError:
            state.process_state = "stopped"
            print(f"Binary not found: {args[0]}", file=sys.stderr, flush=True)
            return False
        except Exception as e:
            state.process_state = "stopped"
            print(f"Failed to start llama-server: {e}", file=sys.stderr, flush=True)
            return False
        
        return True
    
    def stop(self, timeout_sec: int = 10, force_kill: bool = False) -> bool:
        """Stop the running llama-server process.
        
        Only stops/kills the server if we started it ourselves or if
        force_kill=True. If it's external and force_kill=False, we just
        release monitoring without killing it.
        """
        from llama_monitor.process_monitor import kill_process_graceful
        
        state = get_global_state()
        pid = state.pid
        
        if not pid:
            return True  # Nothing to stop
        
        if force_kill or state.server_owned:
            # We started this server — kill it (or force killing external)
            state.process_state = "killing"
            print(f"Stopping llama-server (PID {pid})...", flush=True)
            
            success = kill_process_graceful(pid, timeout_sec)
            
            if success:
                state.process_state = "stopped"
                state.pid = None
                state.health_ok = False
                state.server_owned = False
                _remove_pid()
                print("llama-server stopped.", flush=True)
            else:
                print(f"WARNING: Could not stop llama-server (PID {pid})", file=sys.stderr, flush=True)
                state.process_state = "stopped"
        else:
            # External server — just release monitoring
            print(f"Releasing monitoring of external llama-server (PID {pid}).", flush=True)
            state.pid = None
            state.server_owned = False
            state.health_ok = False
            _remove_pid()
        
        self.proc = None
        return True
    
    def restart(self, force_kill: bool = False) -> bool:
        """Stop then start with the current template.
        
        If force_kill=True, kill even external servers (used when health
        checks fail for a faulty running instance).
        """
        self.stop(force_kill=force_kill, timeout_sec=self.config["restart_timeout_sec"])
        
        state = get_global_state()
        if state.active_template:
            return self.start(state.active_template)
        return False
    
    def check_and_recover(self) -> None:
        """Run health check and recover if needed.
        
        If an external llama-server is already running on the template port,
        we only monitor it. If health fails, we take over: kill and restart.
        If no server is running, we start a new one.
        """
        from llama_monitor.health import await_health
        from llama_monitor.process_monitor import get_process_state, is_process_zombie
        
        state = get_global_state()
        pid = state.pid
        
        # Check if any server is running on our template port
        if state.active_template:
            from llama_monitor.config import load_template
            try:
                tpl = load_template(state.active_template)
                tpl_port = tpl.get("port", 8080)
                existing_server = find_llama_server_by_port(tpl_port)
            except Exception:
                existing_server = None
        else:
            tpl_port = None
            existing_server = None
        
        if not pid and not tpl_port:
            # No template configured
            return
        
        # Case 1: No server running → start one
        if not pid and not existing_server:
            if state.active_template:
                print(f"No llama-server running. Starting with template...", flush=True)
                self.start(state.active_template)
            return
        
        # Case 2: External server found (we didn't start it ourselves)
        if existing_server and not pid:
            port = tpl_port or 8080
            if not self._health_url:
                self._health_url = f"http://127.0.0.1:{port}/health"
            # Adopt this server for monitoring
            print(f"Found external llama-server on port {port} (PID {existing_server['pid']}). "
                  f"Monitoring it.", flush=True)
            state.pid = existing_server["pid"]
            state.server_owned = False
            # Don't return — fall through to health check
            
        # Now check health (for either our server or adopted external server)
        if pid:
            state.health_ok = await_health(self._health_url)
            
            if state.health_ok:
                state.process_state = "running"
                return
            
            # Health check failed — check process state
            ps_state = get_process_state(pid)
            state.process_state = "checking"
            
            if not ps_state.alive:
                # Process gone
                print(f"llama-server not running (PID {pid} gone). Restarting...", flush=True)
                state.pid = 0
                _remove_pid()
                self.start(state.active_template)
            elif is_process_zombie(pid):
                # Zombie — kill and restart
                print(f"llama-server is zombie (PID {pid}). Killing...", flush=True)
                self.stop(timeout_sec=1)
                self.start(state.active_template)
            else:
                # Process alive but health failing — kill and restart
                print(f"llama-server unhealthy (PID {pid}). Restarting...", flush=True)
                self.restart()
        elif not pid and state.server_owned:
            # We started a server but it's not in state.pid anymore — check if port has one
            if tpl_port:
                srv = find_llama_server_by_port(tpl_port)
                if srv:
                    print(f"llama-server restarted externally (PID {srv['pid']}). "
                          f"Monitoring.", flush=True)
                    state.pid = srv["pid"]
                    state.server_owned = False
    
    def set_active_template(self, template_name: str, template_display_name: str) -> None:
        """Set the currently active template."""
        state = get_global_state()
        state.active_template = template_name
        state.active_template_name = template_display_name
    
    def _read_logs(self, proc: subprocess.Popen) -> None:
        """Read llama-server log output line by line."""
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                print(f"[llama-server] {line.rstrip()}", flush=True)
        except Exception:
            pass


def run_daemon(config: dict) -> None:
    """Run the main daemon loop.
    
    This function blocks until the daemon is stopped via signal.
    """
    global _global_state, _daemon_loop
    
    daemon = Daemon(config)
    _global_state = DaemonState()
    
    # Restore active_template from config
    active_template = config.get("active_template", "")
    if active_template:
        _global_state.active_template = active_template
    
    def handle_signal(signum, frame):
        print(f"\nReceived signal {signum}, shutting down...", flush=True)
        _global_state.shutdown_event.set()
        # Close the event loop to interrupt any blocking run_until_complete()
        if _daemon_loop and not _daemon_loop.is_closed():
            _daemon_loop.call_soon_threadsafe(_daemon_loop.stop)
        raise SystemExit(0)
    
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    
    print(f"llama-monitor daemon starting (PID {os.getpid()})", flush=True)
    print(f"Config: interval={config['check_interval_sec']}s, timeout={config['restart_timeout_sec']}s", flush=True)
    
    # Write our own PID
    _write_pid(os.getpid())
    
    # Start API server in background
    from llama_monitor.api import start_api_server
    api_thread = threading.Thread(
        target=start_api_server,
        kwargs={"port": config["daemon_port"], "host": "127.0.0.1"},
        daemon=True,
        name="llama-monitor-api"
    )
    api_thread.start()
    print(f"API server started on 127.0.0.1:{config['daemon_port']}", flush=True)
    
    # If autostart is enabled and there's a template set, check first
    if config.get("autostart") and _global_state.active_template:
        try:
            from llama_monitor.config import load_template
            tpl = load_template(_global_state.active_template)
            tpl_port = tpl.get("port", 8080)
            existing_server = find_llama_server_by_port(tpl_port)
        except Exception:
            existing_server = None
        
        if existing_server:
            print(f"Autostart: llama-server already running on port {tpl_port} "
                  f"(PID {existing_server['pid']}). Adopting for monitoring.", flush=True)
            daemon._health_url = f"http://127.0.0.1:{tpl_port}/health"
            state.pid = existing_server["pid"]
            state.server_owned = False
        else:
            print("Autostart enabled - launching template...", flush=True)
            daemon.start(_global_state.active_template)
    
    try:
        while not _global_state.shutdown_event.is_set():
            # Check for restart signal
            if _global_state.restart_event.is_set():
                _global_state.restart_event.clear()
                print("Restart signal received.", flush=True)
                daemon.restart()
                continue
            
            # Check for shutdown signal
            if _global_state.shutdown_event.is_set():
                break
            
            # Health check cycle
            daemon.check_and_recover()
            
            # Check shutdown again
            if _global_state.shutdown_event.is_set():
                break
            
            # Wait for next cycle — poll shutdown_event every second so
            # a shutdown is detected within ~1s instead of waiting up to
            # check_interval_sec (30s).
            for _ in range(config["check_interval_sec"]):
                if _global_state.shutdown_event.is_set():
                    break
                _global_state.shutdown_event.wait(timeout=1.0)
    
    finally:
        daemon.stop(timeout_sec=config["restart_timeout_sec"])
        _remove_pid()
        print("llama-monitor daemon stopped.", flush=True)


def handle_status() -> dict:
    """Return current daemon status as dict."""
    state = get_global_state()
    pid = _read_pid()
    
    return {
        "daemon_pid": os.getpid(),
        "config_path": str(Path.home() / ".config" / "llama-monitor" / "config.json"),
        "pid_file": str(PID_FILE),
        "active_template": state.active_template or "none",
        "active_template_name": state.active_template_name or "none",
        "process_state": state.process_state,
        "health_ok": state.health_ok,
        "llama_pid": state.pid or pid,
    }
