#!/usr/bin/env python3
"""BastionProxy — lightweight TCP-to-AgentShell proxy (PoC).

Listens on a TCP port, accepts connections, and spawns agent-shell
with stdin/stdout wired to the client socket.

Usage:
    python -m gateway.bastion_proxy --bind 0.0.0.0 --port 2222
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)


class BastionProxy:
    """Single-threaded TCP acceptor that spawns agent-shell per connection."""

    def __init__(self, bind_host: str = "127.0.0.1", port: int = 2222):
        self.bind_host = bind_host
        self.port = port
        self._shutdown = threading.Event()
        self._sock: socket.socket | None = None

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.bind_host, self.port))
        self._sock.listen(5)
        logger.info("BastionProxy listening on %s:%d", self.bind_host, self.port)

        while not self._shutdown.is_set():
            try:
                self._sock.settimeout(1.0)
                client, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            logger.info("Connection from %s:%d", addr[0], addr[1])
            handler = threading.Thread(
                target=self._handle_client,
                args=(client, addr),
                daemon=True,
            )
            handler.start()

    def _handle_client(self, client: socket.socket, addr: tuple) -> None:
        """Spawn agent-shell and bridge socket ↔ subprocess."""
        try:
            agent_shell_path = Path(__file__).parent.parent / "owls_cli" / "agent_shell.py"
            proc = subprocess.Popen(
                [sys.executable, str(agent_shell_path), "--user", f"bastion-{addr[0]}"],
                stdin=client,
                stdout=client,
                stderr=client,
                text=True,
            )
            proc.wait()
        except Exception as e:
            logger.error("Client handler error: %s", e)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def stop(self) -> None:
        self._shutdown.set()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="OWLS Bastion Proxy")
    parser.add_argument("--bind", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=2222, help="Listen port")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    proxy = BastionProxy(bind_host=args.bind, port=args.port)

    def _signal_handler(signum, frame):
        logger.info("Shutting down BastionProxy")
        proxy.stop()
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _signal_handler)

    proxy.start()


if __name__ == "__main__":
    main()
