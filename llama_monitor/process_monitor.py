"""Process monitoring for llama-server instances."""

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class ProcessState:
    """State information for a running process."""
    pid: int
    alive: bool
    zombie: bool
    status: str  # 'running', 'sleeping', 'zombie', 'stopped', 'dead'


def get_process_state(pid: int) -> ProcessState:
    """Get the current state of a process by PID.
    
    Returns ProcessState with alive=True if the process exists and is visible.
    """
    if pid <= 0:
        return ProcessState(pid=pid, alive=False, zombie=False, status="dead")
    
    try:
        os.kill(pid, 0)  # Check if process exists
    except ProcessLookupError:
        return ProcessState(pid=pid, alive=False, zombie=False, status="dead")
    except PermissionError:
        # Process exists but we can't signal it (different user)
        # Check via ps if zombie
        return _check_via_ps(pid)
    
    # Process exists, check its state
    return _check_via_ps(pid)


def _check_via_ps(pid: int) -> ProcessState:
    """Check process state via ps command for detailed status."""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat=", "--no-headers"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ProcessState(pid=pid, alive=False, zombie=False, status="dead")
        
        state_code = result.stdout.strip()[0]
        # ps state codes: R=running, S=sleeping, Z=zombie, T=stopped, D=uninterruptible sleep
        # X=dead (shouldn't show up in ps, but handle it)
        if state_code == "Z":
            return ProcessState(pid=pid, alive=True, zombie=True, status="zombie")
        elif state_code == "R":
            return ProcessState(pid=pid, alive=True, zombie=False, status="running")
        elif state_code == "S":
            return ProcessState(pid=pid, alive=True, zombie=False, status="sleeping")
        elif state_code == "T":
            return ProcessState(pid=pid, alive=True, zombie=False, status="stopped")
        else:
            return ProcessState(pid=pid, alive=True, zombie=False, status=state_code)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ProcessState(pid=pid, alive=False, zombie=False, status="dead")


def is_process_zombie(pid: int) -> bool:
    """Check if a process is a zombie (defunct)."""
    state = get_process_state(pid)
    return state.zombie


def kill_process_graceful(pid: int, timeout_sec: int = 10) -> bool:
    """Kill a process gracefully: SIGTERM → wait → SIGKILL if needed.
    
    Returns True if the process was successfully terminated.
    Returns False if the process couldn't be killed.
    """
    state = get_process_state(pid)
    if not state.alive:
        return True  # Already gone
    
    try:
        # Try SIGTERM first
        os.kill(pid, signal.SIGTERM)
        
        # Wait for process to exit
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            state = get_process_state(pid)
            if not state.alive:
                return True
            time.sleep(0.5)
        
        # Process still alive, try SIGKILL
        os.kill(pid, signal.SIGKILL)
        
        # Wait a bit for SIGKILL to take effect
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = get_process_state(pid)
            if not state.alive:
                return True
            time.sleep(0.25)
        
        return False  # Couldn't kill after SIGKILL
        
    except (ProcessLookupError, PermissionError):
        # Process gone or can't signal - effectively dead
        return True
    except OSError as e:
        print(f"Warning: Error killing process {pid}: {e}")
        return False


def find_llama_server_pids() -> list:
    """Find all running llama-server processes.
    
    Returns a list of PIDs for running llama-server instances.
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,comm", "--no-headers"],
            capture_output=True, text=True, timeout=5
        )
        pids = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.strip().split()
            if len(parts) >= 2 and "llama-server" in parts[1]:
                try:
                    pids.append(int(parts[0]))
                except ValueError:
                    continue
        return pids
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
