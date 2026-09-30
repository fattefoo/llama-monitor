"""HTTP health checking for llama-server instances."""

import asyncio
import httpx
from typing import Optional

# Global reference to the current event loop — used by the daemon
# signal handler to interrupt a blocking run_until_complete().
_current_loop = None


DEFAULT_HEALTH_ENDPOINT = "/health"
EXPECTED_STATUS = 200
EXPECTED_BODY = '{"status":"ok"}'


async def check_health(
    url: str = "http://127.0.0.1:8080/health",
    timeout_sec: float = 3.0,
    expected_status: int = EXPECTED_STATUS,
    expected_body: str = EXPECTED_BODY,
) -> bool:
    """Check llama-server health via HTTP endpoint.
    
    Returns True if health check passes (expected status code and body content).
    Returns False if health check fails or times out.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            response = await client.get(url)
            
            if response.status_code != expected_status:
                return False
            
            body = response.text.strip()
            # Normalize whitespace for comparison
            return body == expected_body or _normalize_json(body) == _normalize_json(expected_body)
            
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, OSError):
        return False


def _normalize_json(text: str) -> str:
    """Normalize JSON text for comparison (whitespace-insensitive)."""
    import json
    try:
        return json.dumps(json.loads(text), sort_keys=True)
    except (json.JSONDecodeError, ValueError):
        return text


def await_health(
    url: str = "http://127.0.0.1:8080/health",
    timeout_sec: float = 3.0,
) -> bool:
    """Synchronous wrapper for check_health.
    
    Runs the async health check in a new event loop. Used by the
    synchronous daemon thread.
    """
    import asyncio
    
    async def _run():
        try:
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                response = await client.get(url)
                
                if response.status_code != 200:
                    return False
                
                body = response.text.strip()
                return body == EXPECTED_BODY or _normalize_json(body) == _normalize_json(EXPECTED_BODY)
                
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, OSError):
            return False
    
    try:
        global _current_loop
        # Try to get the current running loop (e.g. from api.py async context)
        try:
            _current_loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — create a new one
            _current_loop = asyncio.new_event_loop()
        
        result = _current_loop.run_until_complete(_run())
        # Only close if we created a fresh loop (not reused)
        if not asyncio.get_event_loop() if _current_loop else True:
            if _current_loop and not _current_loop.is_running():
                _current_loop.close()
                _current_loop = None
        return result
    except RuntimeError:
        # Event loop already running — fall back to synchronous httpx
        try:
            import requests
            response = requests.get(url, timeout=timeout_sec)
            return (response.status_code == 200 and 
                    (response.text.strip() == EXPECTED_BODY or 
                     _normalize_json(response.text.strip()) == _normalize_json(EXPECTED_BODY)))
        except Exception:
            return False
