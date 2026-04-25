"""Interceptor chain — pre-execution policy enforcement.

The chain runs *before* any tool handler is invoked.  Each interceptor
inspects the ToolContext and returns an Action.  The first non-proceed
Action short-circuits the chain.

Built-in interceptors:
    ApprovalInterceptor    → dangerous-command approval (tools/approval.py)
    PolicyInterceptor      → sandbox_profile vs tool capability mismatch
    ValidationInterceptor  → previous verifier_command result check

Usage:
    from tools.interceptor_chain import InterceptorChain, ApprovalInterceptor
    chain = InterceptorChain([ApprovalInterceptor(), PolicyInterceptor()])
    action = chain.intercept(ctx)
    if action["type"] != "proceed":
        ...  # abort, freeze, or rollback
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from agent.interfaces import Action, Interceptor, ToolContext
from owls_constants import get_owls_home
from tools.registry import registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FrozenTaskStore
# ---------------------------------------------------------------------------

class FrozenTaskStore:
    """SQLite-backed store for tasks that were frozen by the interceptor chain.

    A frozen task waits for human resolution (approve / deny / modify).
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_owls_home() / "frozen_tasks.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS frozen_tasks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT NOT NULL,
                    task_id     TEXT,
                    reason      TEXT NOT NULL,
                    suggested_fix TEXT,
                    created_at  TEXT NOT NULL,
                    resolved_at TEXT
                )
                """
            )
            conn.commit()

    def add(self, session_id: str, task_id: Optional[str], reason: str,
            suggested_fix: Optional[str] = None) -> int:
        """Add a new frozen task.  Returns the row ID."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.execute(
                    """
                    INSERT INTO frozen_tasks (session_id, task_id, reason, suggested_fix, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, task_id, reason, suggested_fix,
                     datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
                return cur.lastrowid or 0

    def list_unresolved(self, session_id: Optional[str] = None) -> List[Dict]:
        """Return unresolved frozen tasks, optionally filtered by session."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                if session_id:
                    rows = conn.execute(
                        "SELECT * FROM frozen_tasks WHERE session_id = ? AND resolved_at IS NULL ORDER BY created_at",
                        (session_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM frozen_tasks WHERE resolved_at IS NULL ORDER BY created_at"
                    ).fetchall()
                return [dict(r) for r in rows]

    def resolve(self, task_id_or_row_id: int) -> bool:
        """Mark a frozen task as resolved.  Returns True if a row was updated."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.execute(
                    "UPDATE frozen_tasks SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
                    (datetime.now(timezone.utc).isoformat(), task_id_or_row_id),
                )
                conn.commit()
                return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Interceptor implementations
# ---------------------------------------------------------------------------

class ApprovalInterceptor:
    """Delegates to tools/approval.py for dangerous-command checks."""

    def intercept(self, ctx: ToolContext) -> Action:
        if ctx.get("tool_name") != "terminal":
            return {"type": "proceed"}

        command = ctx.get("command", "")
        if not command:
            return {"type": "proceed"}

        try:
            from tools.approval import check_all_command_guards
            result = check_all_command_guards(command, env_type="local")
        except Exception as e:
            logger.warning("ApprovalInterceptor: guard check failed: %s", e)
            return {"type": "proceed"}

        if result.get("approved"):
            return {"type": "proceed"}

        # Blocked — freeze or rollback depending on severity
        status = result.get("status")
        if status == "approval_required":
            return {
                "type": "freeze",
                "reason": result.get("message", "Approval required"),
                "suggested_fix": result.get("suggested_fix"),
            }

        return {
            "type": "rollback",
            "reason": result.get("message", "Command blocked by policy"),
            "suggested_fix": result.get("suggested_fix"),
        }


class PolicyInterceptor:
    """Checks whether the requested sandbox_profile is compatible with the
tool's declared capabilities."""

    # Mapping: profile → capabilities required
    _PROFILE_CAPS: Dict[str, List[str]] = {
        "inspect-ro":    ["read"],
        "diag-net":      ["read", "network"],
        "mutate-config": ["read", "write", "network"],
        "full-mutate":   ["read", "write", "network", "exec"],
    }

    def intercept(self, ctx: ToolContext) -> Action:
        profile = ctx.get("sandbox_profile", "full-mutate")
        tool_name = ctx.get("tool_name", "")

        # Read tool capabilities from registry metadata if available
        schema = registry.get_schema(tool_name) or {}
        params = schema.get("parameters", {}).get("properties", {})
        # Heuristic: if schema has no 'sandbox_profile' param, tool doesn't
        # declare sandbox awareness — assume it needs full-mutate.
        required = self._PROFILE_CAPS.get(profile, ["read", "write", "network", "exec"])

        # For now, we only enforce that terminal tool with inspect-ro doesn't
        # try to write.  Real capability extraction would come from registry.
        if tool_name == "terminal" and profile == "inspect-ro":
            cmd = ctx.get("command", "")
            write_indicators = [">", ">>", "| tee", "chmod", "chown", "rm ", "mv ", "cp "]
            if any(ind in cmd for ind in write_indicators):
                return {
                    "type": "rollback",
                    "reason": f"Terminal command '{cmd[:60]}' contains write operations but sandbox_profile is 'inspect-ro'",
                    "suggested_fix": "Switch sandbox_profile to 'mutate-config' or 'full-mutate'",
                }

        return {"type": "proceed"}


class ValidationInterceptor:
    """Checks that the previous PlanNode's verifier_command passed.

    This interceptor reads planner state from a lightweight SQLite store
    so that it can operate without importing agent/planner.py (which may
    not exist yet during early boot).
    """

    def __init__(self, state_db: Optional[Path] = None):
        self.state_db = state_db or get_owls_home() / "planner_state.db"
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(str(self.state_db)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plan_node_results (
                    node_id     TEXT PRIMARY KEY,
                    session_id  TEXT NOT NULL,
                    verifier_passed INTEGER NOT NULL DEFAULT 0,
                    updated_at  TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def intercept(self, ctx: ToolContext) -> Action:
        task_id = ctx.get("task_id")
        if not task_id:
            return {"type": "proceed"}

        with sqlite3.connect(str(self.state_db)) as conn:
            row = conn.execute(
                "SELECT verifier_passed FROM plan_node_results WHERE node_id = ?",
                (task_id,),
            ).fetchone()

        if row is None:
            # No previous verifier result — proceed
            return {"type": "proceed"}

        if row[0]:
            return {"type": "proceed"}

        return {
            "type": "rollback",
            "reason": f"Previous verifier for node '{task_id}' failed.",
            "suggested_fix": "Review the plan and fix the failing verifier command before retrying.",
        }

    def set_verifier_result(self, node_id: str, session_id: str, passed: bool) -> None:
        """Record the result of a verifier_command execution."""
        with sqlite3.connect(str(self.state_db)) as conn:
            conn.execute(
                """
                INSERT INTO plan_node_results (node_id, session_id, verifier_passed, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    verifier_passed = excluded.verifier_passed,
                    updated_at = excluded.updated_at
                """,
                (node_id, session_id, int(passed), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# InterceptorChain
# ---------------------------------------------------------------------------

class InterceptorChain:
    """Ordered list of interceptors evaluated sequentially.

    The first interceptor that returns a non-proceed Action stops the chain.
    """

    def __init__(self, interceptors: Optional[List[Interceptor]] = None):
        self.interceptors: List[Interceptor] = list(interceptors) if interceptors else []

    def add(self, interceptor: Interceptor) -> None:
        """Append an interceptor to the chain."""
        self.interceptors.append(interceptor)

    def intercept(self, ctx: ToolContext) -> Action:
        """Run the chain.  Returns the first non-proceed Action or proceed."""
        for interceptor in self.interceptors:
            try:
                action = interceptor.intercept(ctx)
            except Exception as e:
                logger.warning("Interceptor %s raised: %s", type(interceptor).__name__, e)
                # Fail-safe: if an interceptor crashes, freeze the task
                action = {
                    "type": "freeze",
                    "reason": f"Interceptor {type(interceptor).__name__} crashed: {e}",
                }
            if action.get("type") != "proceed":
                # Audit: log policy violation when interceptors block execution
                try:
                    from tools.audit_logger import get_audit_logger
                    get_audit_logger().log_event({
                        "event_type": "policy_violation",
                        "command": ctx.get("command", ""),
                        "tool_name": ctx.get("tool_name", ""),
                        "session_id": ctx.get("session_id", ""),
                        "task_id": ctx.get("task_id", ""),
                        "description": action.get("reason", "Blocked by interceptor"),
                        "risk_level": "high",
                        "action": action.get("type"),
                    })
                except Exception:
                    pass
                return action
        return {"type": "proceed"}
