#!/usr/bin/env python3
"""AgentShell — interactive shell wrapper for OWLS Agent.

Reads user input, routes through AIAgent.chat(), and executes planned
commands via the interceptor chain and sandbox policy.

Usage:
    python -m owls_cli.agent_shell --user alice --config ~/.owls/config.yaml
    # Or via SSH ForceCommand:
    agent-shell --user %u
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from owls_constants import display_owls_home, get_owls_home
from tools.audit_logger import get_audit_logger
from tools.interceptor_chain import ApprovalInterceptor, InterceptorChain, PolicyInterceptor
from tools.output_redirector import OutputRedirector
from tools.sandbox_policy import apply_sandbox_profile, is_sandbox_available

logger = logging.getLogger(__name__)


class AgentShell:
    """Interactive shell that wraps the AI Agent with policy enforcement."""

    def __init__(self, user: str, config: Dict):
        self.user = user
        self.config = config
        self.session_id = f"shell_{user}_{int(time.time())}"
        self._shutdown = threading.Event()
        self._children: List[int] = []
        self._audit = get_audit_logger()
        self._chain = InterceptorChain([
            PolicyInterceptor(),
            ApprovalInterceptor(),
        ])
        self._output = OutputRedirector(
            mode="display+log",
            audit_logger=self._audit,
        )
        self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                signal.signal(sig, self._signal_handler)
            except Exception:
                pass

    def _signal_handler(self, signum, frame):
        logger.info("Received signal %s — shutting down AgentShell", signum)
        self._shutdown.set()
        self._reap_children()
        sys.exit(128 + signum)

    def _reap_children(self):
        for pid in list(self._children):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def _print_welcome(self) -> None:
        try:
            from owls_cli.skin_engine import get_active_skin
            skin = get_active_skin()
            welcome = skin.get("branding", {}).get("welcome", "Welcome to OWLS AgentShell")
            agent_name = skin.get("branding", {}).get("agent_name", "OWLS")
        except Exception:
            welcome = "Welcome to OWLS AgentShell"
            agent_name = "OWLS"

        print(f"\n  {welcome}")
        print(f"  User: {self.user}  |  Session: {self.session_id}")
        print(f"  Home: {display_owls_home()}")
        print(f"  Type 'exit' or Ctrl+D to quit.\n")

    def _spawn_pty(self, cmd: str) -> int:
        """Spawn a command in a PTY via process_registry.

        Returns the process session ID (for tracking), or raises on failure.
        """
        try:
            from tools.process_registry import ProcessRegistry
            registry = ProcessRegistry()
            session = registry.spawn_local(
                command=cmd,
                task_id=self.session_id,
                session_key=self.session_id,
                use_pty=True,
            )
            if session.pid:
                self._children.append(session.pid)
            return session.id
        except Exception as e:
            logger.error("Failed to spawn PTY: %s", e)
            raise

    def run(self) -> None:
        """Main loop: read input → chat with agent → execute commands."""
        self._print_welcome()

        # Log session start
        self._audit.log_event({
            "event_type": "session_start",
            "session_id": self.session_id,
            "user": self.user,
            "description": f"AgentShell session started for user {self.user}",
            "risk_level": "low",
        })

        while not self._shutdown.is_set():
            try:
                user_input = input("agent-shell> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "logout"):
                print("Goodbye.")
                break

            # Simple passthrough for shell commands that don't need the agent
            if user_input.startswith("!"):
                self._run_raw_command(user_input[1:].strip())
                continue

            # Route through AIAgent
            self._handle_agent_turn(user_input)

        # Session end audit
        self._audit.log_event({
            "event_type": "session_end",
            "session_id": self.session_id,
            "user": self.user,
            "description": f"AgentShell session ended for user {self.user}",
            "risk_level": "low",
        })
        self._reap_children()

    def _run_raw_command(self, cmd: str) -> None:
        """Execute a raw shell command (bypasses agent, still runs interceptors)."""
        ctx = {
            "command": cmd,
            "cwd": os.getcwd(),
            "tool_name": "terminal",
            "target_paths": [],
            "session_id": self.session_id,
            "task_id": None,
            "sandbox_profile": self.config.get("sandbox_profile", "full-mutate"),
            "risk_level": "medium",
        }
        action = self._chain.intercept(ctx)
        if action.get("type") != "proceed":
            print(f"  [BLOCKED] {action.get('reason', 'Policy blocked')}")
            if action.get("suggested_fix"):
                print(f"  Suggestion: {action['suggested_fix']}")
            self._audit.log_event({
                "event_type": "policy_violation",
                "session_id": self.session_id,
                "command": cmd,
                "description": action.get("reason", ""),
                "risk_level": "high",
                "suggested_fix": action.get("suggested_fix"),
            })
            return

        # Apply sandbox
        profile = ctx["sandbox_profile"]
        if not apply_sandbox_profile(profile, self.session_id):
            print(f"  [WARN] Sandbox '{profile}' unavailable — proceeding without restrictions")

        # Log execution start
        self._audit.log_event({
            "event_type": "command_execution",
            "session_id": self.session_id,
            "command": cmd,
            "description": f"Executing raw command in AgentShell",
            "risk_level": "medium",
        })

        result = self._output.run(["bash", "-c", cmd])
        if result.returncode != 0:
            print(f"  [exit {result.returncode}]")

    def _handle_agent_turn(self, user_input: str) -> None:
        """Send user input to AIAgent, receive tool calls, execute them."""
        try:
            from run_agent import AIAgent
            # Minimal agent initialization — reuse existing runtime provider
            agent = AIAgent()
            response = agent.chat(user_input)
        except Exception as e:
            print(f"  [Agent Error] {e}")
            return

        # Parse tool calls from response and execute
        # (This is a simplified version; real implementation would mirror
        #  run_agent.py's conversation loop more closely.)
        print(f"  {response}")


def main() -> None:
    parser = argparse.ArgumentParser(description="OWLS AgentShell")
    parser.add_argument("--user", default=os.getenv("USER", "unknown"),
                        help="Username for this session")
    parser.add_argument("--config", type=Path,
                        default=get_owls_home() / "config.yaml",
                        help="Path to config.yaml")
    parser.add_argument("--sandbox-profile", default="full-mutate",
                        choices=["inspect-ro", "diag-net", "mutate-config", "full-mutate"],
                        help="Default sandbox profile")
    args = parser.parse_args()

    # Load config
    config: Dict = {}
    if args.config.exists():
        try:
            import yaml
            config = yaml.safe_load(args.config.read_text()) or {}
        except Exception as e:
            logger.warning("Could not load config: %s", e)

    config.setdefault("sandbox_profile", args.sandbox_profile)

    shell = AgentShell(user=args.user, config=config)
    shell.run()


if __name__ == "__main__":
    main()
