"""Output redirector — capture subprocess stdout/stderr with optional
display, logging, and audit forwarding.

Modes:
    display      : echo to terminal only
    log          : write to audit logger only
    display+log  : both (default for AgentShell)
    silent       : discard output, still capture for return value

Usage:
    from tools.output_redirector import OutputRedirector
    from tools.audit_logger import get_audit_logger

    redir = OutputRedirector(mode="display+log", audit_logger=get_audit_logger())
    result = redir.run(["ls", "-la"], cwd="/tmp")
    print(result.stdout)
"""

from __future__ import annotations

import logging
import select
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional

from tools.audit_logger import AuditLogger

logger = logging.getLogger(__name__)


@dataclass
class CompletedProcess:
    """Result of OutputRedirector.run()."""
    returncode: int
    stdout: str
    stderr: str
    command: List[str]
    cwd: Optional[str]


class OutputRedirector:
    """Run a subprocess and capture its output with configurable redirection."""

    def __init__(
        self,
        mode: Literal["display", "log", "display+log", "silent"] = "display+log",
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.mode = mode
        self.audit_logger = audit_logger

    def run(
        self,
        cmd: List[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> CompletedProcess:
        """Execute *cmd* and return a CompletedProcess.

        Args:
            cmd: Command and arguments as a list.
            cwd: Working directory.
            env: Environment variables (merged with os.environ).
            timeout: Maximum seconds to wait (None = no limit).

        Returns:
            CompletedProcess with stdout, stderr, and return code.
        """
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        stdout_chunks: List[str] = []
        stderr_chunks: List[str] = []

        # Reader thread so we can optionally display live output
        def _reader():
            while True:
                reads = [proc.stdout, proc.stderr]
                # Filter out None (shouldn't happen with Popen pipes)
                reads = [r for r in reads if r is not None]
                if not reads:
                    break
                ready, _, _ = select.select(reads, [], [], 0.1)
                done = False
                for stream in ready:
                    line = stream.readline()
                    if not line:
                        done = True
                        continue
                    if stream is proc.stdout:
                        stdout_chunks.append(line)
                        if self.mode in ("display", "display+log"):
                            print(line, end="", flush=True)
                    else:
                        stderr_chunks.append(line)
                        if self.mode in ("display", "display+log"):
                            print(line, end="", flush=True)
                if done and proc.poll() is not None:
                    break

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            returncode = proc.wait()

        reader_thread.join(timeout=5)

        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)

        # Audit log if requested
        if self.mode in ("log", "display+log") and self.audit_logger is not None:
            self.audit_logger.log_event({
                "event_type": "command_result",
                "command": " ".join(cmd),
                "description": f"Command exited with code {returncode}",
                "risk_level": "low",
            })

        return CompletedProcess(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            command=cmd,
            cwd=cwd,
        )
