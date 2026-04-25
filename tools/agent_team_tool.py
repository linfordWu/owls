"""AgentTeams coordination tool.

This tool is only exposed when the CLI has enabled AgentTeams and populated
OWLS_AGENT_TEAM_NAME for the leader process.
"""

import json
import os

from tools.registry import registry


def check_requirements() -> bool:
    return bool(os.getenv("OWLS_AGENT_TEAM_NAME")) and os.getenv("OWLS_AGENT_TEAM_WORKER") != "1"


def agent_team(args: dict, **_kwargs) -> str:
    from owls_cli.agent_teams import (
        _message_counts,
        _pane_alive,
        _pid_alive,
        dispatch_team_messages,
        get_team_roles,
        log_file,
        read_team,
        render_role_lines,
        role_by_name,
        send_team_message,
        wait_for_team_outputs,
    )

    team_name = os.getenv("OWLS_AGENT_TEAM_NAME", "")
    if not team_name:
        return json.dumps({"error": "AgentTeams is not enabled"}, ensure_ascii=False)

    action = str(args.get("action") or "status").strip().lower()
    role = str(args.get("role") or "").strip()
    instruction = str(args.get("instruction") or "").strip()
    assignments = args.get("assignments") or {}
    try:
        lines = int(args.get("lines") or 80)
    except (TypeError, ValueError):
        lines = 80
    try:
        timeout_seconds = float(args.get("timeout_seconds") or 180)
    except (TypeError, ValueError):
        timeout_seconds = 180

    if action == "roles":
        return json.dumps(
            {
                "team": team_name,
                "roles": render_role_lines(
                    os.getenv("OWLS_AGENT_TEAM_MODE", "true"),
                    get_team_roles(team_name),
                ),
            },
            ensure_ascii=False,
        )

    if action == "send":
        if not role or not instruction:
            return json.dumps(
                {"error": "send requires role and instruction"},
                ensure_ascii=False,
            )
        try:
            targets = send_team_message(team_name, role, instruction, sender="lead")
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps(
            {
                "team": team_name,
                "sent_to": targets,
                "instruction": instruction,
                "next": "Task queued. Prefer action=dispatch for multi-role work so results are collected in one tool call.",
            },
            ensure_ascii=False,
        )

    if action == "dispatch":
        if isinstance(assignments, str):
            try:
                assignments = json.loads(assignments)
            except json.JSONDecodeError:
                return json.dumps({"error": "assignments must be an object or JSON object string"}, ensure_ascii=False)
        if not isinstance(assignments, dict) or not assignments:
            return json.dumps(
                {"error": "dispatch requires assignments: {role_name: instruction}"},
                ensure_ascii=False,
            )
        try:
            normalized_assignments = {str(key): str(value) for key, value in assignments.items()}
            message_ids = dispatch_team_messages(team_name, normalized_assignments, sender="lead")
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        wait_result = wait_for_team_outputs(
            team_name,
            message_ids,
            timeout_seconds=max(1.0, min(timeout_seconds, 600.0)),
        )
        return json.dumps(
            {
                "team": team_name,
                "sent_to": list(message_ids.keys()),
                "message_ids": message_ids,
                "completed": wait_result["completed"],
                "pending": wait_result["pending"],
                "timed_out": wait_result["timed_out"],
                "status": "completed" if not wait_result["timed_out"] else "running",
            },
            ensure_ascii=False,
        )

    if action == "status":
        team = read_team(team_name) or {}
        members = team.get("members") or {}
        status = []
        for team_role in get_team_roles(team_name):
            member = members.get(team_role.name, {})
            pid = member.get("pid")
            pane_id = member.get("pane_id")
            alive = _pane_alive(pane_id) if pane_id else (_pid_alive(pid) if pid else False)
            queued, processing, done = _message_counts(team_name, team_role.name)
            if processing:
                action_text = "处理中"
            elif queued:
                action_text = "等待处理队列任务"
            elif alive:
                action_text = "等待协调者下发任务"
            else:
                action_text = "已停止"
            status.append(
                {
                    "role": team_role.name,
                    "display_name": team_role.display_name,
                    "running": alive,
                    "pid": pid,
                    "pane_id": pane_id,
                    "queued": queued,
                    "processing": processing,
                    "done": done,
                    "action": action_text,
                }
            )
        return json.dumps({"team": team_name, "status": status}, ensure_ascii=False)

    if action == "tail":
        if not role:
            return json.dumps({"error": "tail requires role"}, ensure_ascii=False)
        try:
            target_role = role_by_name(role, team_name=team_name)
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        path = log_file(team_name, target_role.name)
        try:
            data = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return json.dumps({"team": team_name, "role": target_role.name, "log": "", "missing": True}, ensure_ascii=False)
        return json.dumps(
            {"team": team_name, "role": target_role.name, "log": "\n".join(data[-max(lines, 1):])},
            ensure_ascii=False,
        )

    return json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False)


registry.register(
    name="agent_team",
    toolset="agent_team",
    schema={
        "name": "agent_team",
        "description": (
            "Coordinate OWLS AgentTeams when teammate mode is enabled or a user prompt explicitly requests AgentTeams. "
            "Use this to show roles, send tasks to independent teammates, "
            "check status, or read teammate logs. This is not the subagent/delegate tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["roles", "dispatch", "send", "status", "tail"],
                    "description": "AgentTeams operation to perform.",
                },
                "role": {
                    "type": "string",
                    "description": "Target role for send/tail. Use action=roles/status to inspect available role names. Use all for send.",
                },
                "instruction": {
                    "type": "string",
                    "description": "Instruction to send when action is send.",
                },
                "assignments": {
                    "type": "object",
                    "description": "For dispatch: an object mapping role names to role-specific instructions. Dispatch sends all tasks first, then waits and returns teammate outputs.",
                    "additionalProperties": {"type": "string"},
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of log lines to read for tail.",
                    "default": 80,
                },
                "timeout_seconds": {
                    "type": "number",
                    "description": "For dispatch: maximum seconds to wait for teammate outputs.",
                    "default": 180,
                },
            },
            "required": ["action"],
        },
    },
    handler=agent_team,
    check_fn=check_requirements,
    description="Coordinate AgentTeams teammates",
    emoji="👥",
)
