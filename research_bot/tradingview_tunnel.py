from __future__ import annotations

"""Optional Cloudflare Quick Tunnel manager for local DEMO validation.

Quick Tunnels are for testing/development only. They expose the local control
service through a temporary HTTPS trycloudflare.com URL without requiring
inbound firewall/NAT changes.
"""

from dataclasses import dataclass
import os
import queue
import re
import shutil
import subprocess
import threading
from typing import Any


_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I)


class TunnelUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class TunnelStatus:
    running: bool
    public_url: str
    error: str
    pid: int | None


class QuickTunnelManager:
    def __init__(self):
        self._process: subprocess.Popen[str] | None = None
        self._public_url = ""
        self._error = ""
        self._lock = threading.Lock()
        self._lines: "queue.Queue[str]" = queue.Queue()

    def _reader(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                clean = line.rstrip()
                self._lines.put(clean)
                match = _URL_RE.search(clean)
                if match:
                    with self._lock:
                        self._public_url = match.group(0)
                        self._error = ""
        except Exception as exc:
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
        finally:
            code = process.poll()
            if code not in (None, 0) and not self._public_url:
                with self._lock:
                    self._error = self._error or f"cloudflared exited with code {code}"

    def start(self, *, local_url: str = "http://127.0.0.1:8000") -> TunnelStatus:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return TunnelStatus(
                    running=True,
                    public_url=self._public_url,
                    error=self._error,
                    pid=self._process.pid,
                )
            self._public_url = ""
            self._error = ""

        executable = shutil.which("cloudflared")
        if not executable:
            raise TunnelUnavailableError(
                "cloudflared was not found. On Windows install it with: "
                "winget install --id Cloudflare.cloudflared"
            )

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        process = subprocess.Popen(
            [
                executable,
                "tunnel",
                "--url",
                local_url,
                "--no-autoupdate",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        with self._lock:
            self._process = process

        thread = threading.Thread(
            target=self._reader,
            args=(process,),
            daemon=True,
            name="cloudflared-quick-tunnel-reader",
        )
        thread.start()
        return self.status()

    def stop(self) -> TunnelStatus:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        with self._lock:
            self._process = None
            self._public_url = ""
        return self.status()

    def status(self) -> TunnelStatus:
        with self._lock:
            process = self._process
            running = process is not None and process.poll() is None
            return TunnelStatus(
                running=running,
                public_url=self._public_url,
                error=self._error,
                pid=(process.pid if running and process is not None else None),
            )

    def recent_logs(self, limit: int = 30) -> list[str]:
        items: list[str] = []
        while True:
            try:
                items.append(self._lines.get_nowait())
            except queue.Empty:
                break
        return items[-max(1, int(limit)) :]
