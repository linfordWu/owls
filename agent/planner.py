"""Planner — generate and execute multi-step task plans.

The Planner uses an auxiliary LLM to break complex user requests into a
directed acyclic graph of PlanNodes.  The PlanStateMachine topologically
sorts and executes them, with verification and rollback support.

Usage:
    from agent.planner import Planner, PlanStateMachine
    planner = Planner()
    plan = planner.generate_plan("Check disk, restart nginx, verify port 80", available_tools)
    result = plan.execute(agent)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from agent.interfaces import PlanNode, RiskLevel, SandboxProfile
from owls_constants import get_owls_home

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PlanStateMachine
# ---------------------------------------------------------------------------

class PlanStateMachine:
    """Holds the plan DAG and execution state."""

    def __init__(self, plan_id: str, nodes: List[PlanNode]):
        self.plan_id = plan_id
        self.nodes: Dict[str, PlanNode] = {n["id"]: n for n in nodes}
        self._lock = threading.Lock()
        self._results: Dict[str, Any] = {}
        self._db_path = get_owls_home() / "planner_state.db"
        self._init_db()
        self._save_to_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id     TEXT PRIMARY KEY,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    nodes_json  TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _save_to_db(self) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                """
                INSERT INTO plans (plan_id, created_at, updated_at, nodes_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    nodes_json = excluded.nodes_json
                """,
                (
                    self.plan_id,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(list(self.nodes.values()), ensure_ascii=False),
                ),
            )
            conn.commit()

    def get_ready_nodes(self) -> List[str]:
        """Return node IDs whose dependencies are all satisfied."""
        ready = []
        for nid, node in self.nodes.items():
            if node["status"] != "pending":
                continue
            deps = node.get("dependencies", [])
            if all(self.nodes.get(d, {}).get("status") == "success" for d in deps):
                ready.append(nid)
        return ready

    def update_node(self, node_id: str, status: str, result: Optional[Any] = None) -> None:
        with self._lock:
            if node_id in self.nodes:
                self.nodes[node_id]["status"] = status
                if result is not None:
                    self._results[node_id] = result
            self._save_to_db()

    def execute(self, agent: Any) -> "ExecutionResult":
        """Topologically execute the plan.

        Returns an ExecutionResult summarizing successes, failures, and rollbacks.
        """
        from tools.checkpoint_manager import CheckpointManager
        from tools.todo_tool import TodoStore

        cm = CheckpointManager(enabled=True)
        todo = TodoStore()
        successes: List[str] = []
        failures: List[str] = []
        rolled_back: List[str] = []

        # Write plan to todo store
        todos = [{"id": n["id"], "content": n["task_desc"], "status": "pending"} for n in self.nodes.values()]
        todo.write(todos)

        while True:
            ready = self.get_ready_nodes()
            if not ready:
                break

            # Execute ready nodes sequentially.
            # Parallel execution is unsafe because agent.run_conversation
            # mutates shared agent state (messages, token counters, etc.).
            for nid in ready:
                try:
                    ok = self._execute_node(nid, agent, cm)
                    if ok:
                        self.update_node(nid, "success")
                        successes.append(nid)
                        todo.write([{"id": nid, "status": "done"}], merge=True)
                    else:
                        self.update_node(nid, "failed")
                        failures.append(nid)
                        # Rollback if checkpoint exists
                        self._rollback_node(nid, cm)
                        rolled_back.append(nid)
                        todo.write([{"id": nid, "status": "failed"}], merge=True)
                except Exception as e:
                    logger.error("Node %s execution error: %s", nid, e)
                    self.update_node(nid, "failed")
                    failures.append(nid)

        all_done = all(n["status"] in ("success", "failed", "rolled_back") for n in self.nodes.values())
        return ExecutionResult(
            plan_id=self.plan_id,
            successes=successes,
            failures=failures,
            rolled_back=rolled_back,
            completed=all_done,
        )

    def _execute_node(self, node_id: str, agent: Any, cm: Any) -> bool:
        node = self.nodes[node_id]
        self.update_node(node_id, "running")

        # Execute the node's task via the agent.
        # The agent's run_conversation handles tool calling, sandbox policy,
        # and interception chain automatically.
        try:
            result = agent.run_conversation(
                node["task_desc"],
                conversation_history=None,
            )
            node_success = result.get("completed", False) and not result.get("error")
        except Exception as e:
            logger.error("Agent execution failed for node %s: %s", node_id, e)
            node_success = False

        # Run verifier_command if present to validate the outcome
        verifier = node.get("verifier_command")
        if verifier:
            import subprocess
            try:
                v_result = subprocess.run(
                    verifier, shell=True, capture_output=True, text=True, timeout=30
                )
                verifier_ok = v_result.returncode == 0
            except Exception as e:
                logger.error("Verifier failed for node %s: %s", node_id, e)
                verifier_ok = False
            # Retry logic
            if not verifier_ok:
                max_retries = node.get("max_retries", 0)
                for attempt in range(max_retries):
                    logger.info("Retrying node %s (attempt %d/%d)", node_id, attempt + 1, max_retries)
                    try:
                        v_result = subprocess.run(
                            verifier, shell=True, capture_output=True, text=True, timeout=30
                        )
                        if v_result.returncode == 0:
                            verifier_ok = True
                            break
                    except Exception as e:
                        logger.error("Verifier retry failed for node %s: %s", node_id, e)
                if not verifier_ok:
                    return False

        return node_success if node_success is not None else True

    def _rollback_node(self, node_id: str, cm: Any) -> None:
        """Rollback this node and mark downstream nodes as rolled_back."""
        self.update_node(node_id, "rolled_back")
        # Find all downstream nodes
        downstream = self._find_downstream(node_id)
        for nid in downstream:
            self.update_node(nid, "rolled_back")
            try:
                cm.restore(os.getcwd(), "HEAD")
            except Exception as e:
                logger.warning("Rollback failed for node %s: %s", nid, e)

    def _find_downstream(self, node_id: str) -> List[str]:
        """BFS to find all nodes that depend on *node_id* directly or transitively."""
        downstream: Set[str] = set()
        queue = deque([node_id])
        while queue:
            current = queue.popleft()
            for nid, node in self.nodes.items():
                if current in node.get("dependencies", []) and nid not in downstream:
                    downstream.add(nid)
                    queue.append(nid)
        return list(downstream)

    def serialize(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "nodes": list(self.nodes.values()),
        }

    def retry_node(self, node_id: str) -> bool:
        if node_id not in self.nodes:
            return False
        self.update_node(node_id, "pending")
        return True

    def skip_node(self, node_id: str) -> bool:
        if node_id not in self.nodes:
            return False
        self.update_node(node_id, "success")
        return True


@dataclass
class ExecutionResult:
    plan_id: str
    successes: List[str]
    failures: List[str]
    rolled_back: List[str]
    completed: bool


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

class Planner:
    """Generates structured execution plans from natural language."""

    def __init__(self):
        self._db_path = get_owls_home() / "planner_state.db"

    def generate_plan(self, user_message: str, available_tools: List[Dict]) -> PlanStateMachine:
        """Use an auxiliary LLM to generate a plan DAG.

        Returns a PlanStateMachine ready for execution.
        """
        try:
            from agent.auxiliary_client import call_llm
        except ImportError:
            logger.error("auxiliary_client not available — cannot generate plan")
            return PlanStateMachine(plan_id=f"manual_{uuid.uuid4().hex[:8]}", nodes=[])

        tools_desc = "\n".join(
            f"- {t.get('name', 'unknown')}: {t.get('description', '')}" for t in available_tools
        )

        prompt = f"""You are a task planner for a Linux system administration agent.
Break the following user request into a structured execution plan.

User request: {user_message}

Available tools:
{tools_desc}

Respond with a JSON object containing a "nodes" array. Each node must have:
- id: unique string (e.g., "n_001")
- task_desc: what this step does
- status: always "pending"
- dependencies: list of node IDs that must complete first (can be empty)
- allowed_tools: tool names this node can use
- verifier_command: a shell command that returns exit code 0 on success (or null)
- max_retries: integer 0-3
- sandbox_profile: one of "inspect-ro", "diag-net", "mutate-config", "full-mutate"
- risk_level: one of "low", "medium", "high", "critical"

Rules:
1. Keep nodes atomic (one logical operation each)
2. Use dependencies to enforce order
3. Assign lower risk_level to read-only operations
4. verifier_command should be simple and deterministic
5. allowed_tools should be minimal but sufficient

JSON response:"""

        response = call_llm(
            task="planning",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2000,
        )

        content = response.choices[0].message.content or ""
        # Extract JSON from possible markdown fences
        json_str = content
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()

        try:
            plan_data = json.loads(json_str)
            nodes_raw = plan_data.get("nodes", [])
        except json.JSONDecodeError as e:
            logger.error("Planner returned invalid JSON: %s", e)
            nodes_raw = []

        nodes: List[PlanNode] = []
        for i, n in enumerate(nodes_raw):
            node: PlanNode = {
                "id": n.get("id", f"n_{i:03d}"),
                "task_desc": n.get("task_desc", "Untitled step"),
                "status": "pending",
                "dependencies": n.get("dependencies", []),
                "allowed_tools": n.get("allowed_tools", []),
                "verifier_command": n.get("verifier_command"),
                "max_retries": n.get("max_retries", 1),
                "sandbox_profile": n.get("sandbox_profile", "full-mutate"),
                "risk_level": n.get("risk_level", "medium"),
            }
            nodes.append(node)

        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        return PlanStateMachine(plan_id=plan_id, nodes=nodes)


def is_complex_task(user_message: str, expected_tools: int = 3) -> bool:
    """Heuristic: detect if a message describes a multi-step task.

    Triggers:
    - Contains Chinese/English sequencing words
    - Expected to need more than *expected_tools* tool calls
    """
    sequencing_markers = [
        "并且", "然后", "先", "再", "接着", "之后", "最后",
        "and then", "first", "next", "after that", "finally",
        "step by step", "plan", "workflow", "pipeline",
    ]
    msg_lower = user_message.lower()
    if any(m in msg_lower for m in sequencing_markers):
        return True
    # Additional heuristic: length + tool mention count
    tool_mentions = sum(1 for t in ["check", "restart", "verify", "install", "configure"] if t in msg_lower)
    if tool_mentions >= expected_tools:
        return True
    return False
