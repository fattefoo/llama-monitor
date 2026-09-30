"""HTTP API server for Hermes-Gateway integration."""

import json
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from llama_monitor.daemon import DaemonState

# Global state set by daemon
_api_state: "Optional[DaemonState]" = None


class _APIHandler(BaseHTTPRequestHandler):
    """Handle API requests from Hermes-Gateway."""
    
    def log_message(self, format, *args):
        """Suppress default stderr logging."""
        pass
    
    def _send_json(self, status_code: int, data: dict):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))
    
    def _model_basename(self, model_path: Optional[str]) -> str:
        """Extract just the filename from a model path for display."""
        if not model_path:
            return "unknown"
        return os.path.basename(model_path)
    
    def do_GET(self):
        if self.path == "/api/v1/status":
            self._handle_status()
        elif self.path == "/api/v1/templates":
            self._handle_templates()
        else:
            self._send_json(404, {"error": "Not found"})
    
    def do_POST(self):
        if self.path == "/api/v1/restart":
            self._handle_restart()
        elif self.path == "/api/v1/set-template":
            self._handle_set_template()
        elif self.path == "/api/v1/llm-stop":
            self._handle_llm_stop()
        elif self.path == "/api/v1/llm-start":
            self._handle_llm_start()
        elif self.path == "/api/v1/shutdown":
            self._handle_shutdown()
        else:
            self._send_json(404, {"error": "Not found"})
    
    def _handle_status(self):
        from llama_monitor.config import load_config, list_template_names
        from llama_monitor.daemon import get_global_state
        
        state = get_global_state()
        config = load_config()
        
        self._send_json(200, {
            "pid": state.pid,
            "template": state.active_template,
            "template_name": state.active_template_name,
            "model_path": state.model_path,
            "model_name": self._model_basename(state.model_path),
            "state": state.process_state,
            "health": "ok" if state.health_ok else "unhealthy",
            "config": {
                "check_interval_sec": config["check_interval_sec"],
                "restart_timeout_sec": config["restart_timeout_sec"],
            },
            "templates_count": len(list_template_names()),
            "llm_stopped": state.llm_stopped,
        })
    
    def _handle_templates(self):
        from llama_monitor.config import list_template_names
        
        names = list_template_names()
        self._send_json(200, {
            "templates": [{"name": name} for name in names]
        })
    
    def _handle_set_template(self):
        """Handle switching active template: read JSON body, validate, persist, restart."""
        from llama_monitor.config import save_active_template, list_template_names
        from llama_monitor.daemon import get_global_state
        
        # Read request body
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self._send_json(400, {"success": False, "message": "No template name in request"})
            return
        
        body = self.rfile.read(content_length).decode("utf-8")
        
        try:
            data = json.loads(body)
            template_name = data.get("template", data.get("template_name", ""))
        except (json.JSONDecodeError, KeyError):
            self._send_json(400, {"success": False, "message": "Invalid JSON body"})
            return
        
        if not template_name:
            self._send_json(400, {"success": False, "message": "Template name required"})
            return
        
        # Validate template exists
        available = list_template_names()
        if template_name not in available:
            self._send_json(400, {
                "success": False,
                "message": f"Template '{template_name}' not found. Available: {', '.join(available)}"
            })
            return
        
        # Save to config
        try:
            save_active_template(template_name)
        except Exception as e:
            self._send_json(500, {"success": False, "message": str(e)})
            return
        
        # Update global state
        state = get_global_state()
        state.active_template = template_name
        
        # Signal restart
        if state.restart_event:
            state.restart_event.set()
        
        self._send_json(200, {
            "success": True,
            "message": f"Switched to template '{template_name}' and restart initiated."
        })
    
    def _handle_restart(self):
        # Signal the daemon to restart via the threading event
        from llama_monitor.daemon import get_global_state
        
        state = get_global_state()
        if state and state.restart_event:
            state.restart_event.set()
            self._send_json(200, {
                "success": True,
                "message": "Restart initiated"
            })
        else:
            self._send_json(500, {
                "success": False,
                "message": "Daemon not running"
            })
    
    def _handle_shutdown(self):
        """Gracefully shut down the daemon."""
        from llama_monitor.daemon import get_global_state
        import threading
        import signal
        import os
        import time
        
        state = get_global_state()
        if state and state.shutdown_event:
            state.shutdown_event.set()
        
        self._send_json(200, {
            "success": True,
            "message": "Shutdown initiated"
        })
        
        # Deliver SIGTERM to this process (the daemon process).
        # The signal handler in run_daemon() catches it and raises
        # SystemExit which breaks out of the loop into the finally block.
        # We use a separate thread to avoid the signal being deferred.
        def _send_signal():
            time.sleep(0.5)  # Let the response go out first
            os.kill(os.getpid(), signal.SIGTERM)
        
        threading.Thread(target=_send_signal, daemon=True, name="shutdown-signal").start()
        
        # If the signal handler doesn't work within 5s, force-kill.
        def _force_kill():
            time.sleep(5)
            try:
                os.kill(os.getpid(), signal.SIGKILL)
            except Exception:
                os._exit(1)
        
        threading.Thread(target=_force_kill, daemon=True, name="shutdown-force-kill").start()


def _handle_llm_stop(self):
    """Stop the running llama-server via the API and keep it stopped."""
    from llama_monitor.daemon import get_global_state
    from llama_monitor.config import load_config, save_llm_stopped
    
    state = get_global_state()
    config = load_config()
    
    # Persist the stopped flag so the daemon keeps it stopped (survives restart)
    state.llm_stopped = True
    try:
        save_llm_stopped(True)
    except Exception as e:
        print(f"Warning: could not persist llm_stopped: {e}", file=sys.stderr)
    if state.llm_stop_event:
        state.llm_stop_event.set()
    
    self._send_json(200, {
        "success": True,
        "message": "llama-server stop requested; daemon will not restart it.",
    })


def _handle_llm_start(self):
    """Start the llama-server via the API and resume monitoring."""
    from llama_monitor.daemon import get_global_state
    from llama_monitor.config import save_llm_stopped
    
    state = get_global_state()
    
    # Clear the stopped flag so the daemon resumes normal monitoring (survives restart)
    state.llm_stopped = False
    try:
        save_llm_stopped(False)
    except Exception as e:
        print(f"Warning: could not persist llm_stopped: {e}", file=sys.stderr)
    if state.llm_start_event:
        state.llm_start_event.set()
    
    self._send_json(200, {
        "success": True,
        "message": "llama-server start requested; daemon will resume monitoring.",
    })


def start_api_server(port: int, host: str = "127.0.0.1") -> None:
    """Start the API server in a background thread.
    
    This function blocks until stop_api_server() is called.
    """
    global _api_state
    
    server = HTTPServer((host, port), _APIHandler)
    _api_state = server
    
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="llama-monitor-api")
    thread.start()
    
    # Wait for shutdown
    try:
        while _api_state is not None:
            import time
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_api_server()


def stop_api_server():
    """Stop the API server."""
    global _api_state
    
    if _api_state is not None:
        _api_state.shutdown()
        _api_state.server_close()
        _api_state = None
