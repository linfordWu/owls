"""AgentTeams orchestration for OWLS CLI.

AgentTeams are independent OWLS worker processes coordinated by a leader CLI.
They are intentionally separate from delegate/subagent tools: work is routed
through a small file-backed mailbox and, in tmux mode, each role has its own
visible terminal pane.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from owls_constants import get_owls_home


@dataclass(frozen=True)
class TeamRole:
    name: str
    display_name: str
    responsibility: str


TEAM_LEAD_ROLE = TeamRole(
    "lead",
    "协调者",
    "面向用户接收目标、拆解任务、分配给各角色并汇总最终结果。",
)

DEFAULT_TEAM_ROLES: tuple[TeamRole, ...] = (
    TeamRole(
        "researcher",
        "调研员",
        "阅读代码、资料和运行环境，给出事实依据、约束和可行方案。",
    ),
    TeamRole(
        "implementer",
        "执行员",
        "根据协调者下发的任务修改代码、运行命令并记录关键产出。",
    ),
    TeamRole(
        "verifier",
        "质检员",
        "独立检查改动、运行验证、指出风险、回归点和缺失测试。",
    ),
)

TEAM_ROLES = DEFAULT_TEAM_ROLES

_TEAM_TRIGGER_RE = re.compile(
    r"(agent\s*teams?|agentteams|agenteams|agenteam|agen\s*teams?|多智能体协作|多角色分工)",
    re.IGNORECASE,
)
_ROLE_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?([^：:\n]{2,40})\s*[：:]\s*(\S.+?)\s*$")
_NON_ROLE_HEADINGS = (
    "任务",
    "任务需求",
    "需求",
    "要求",
    "最后输出",
    "输出",
    "实现",
    "功能",
    "说明",
    "注意",
    "示例",
    "指令",
    "角色分工",
    "agentteams",
    "agent teams",
)


def teams_root() -> Path:
    return get_owls_home() / "teams"


def sanitize_component(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in value.strip())
    return cleaned.strip("-_") or "team"


def _role_name_from_display(display_name: str, used: set[str]) -> str:
    base = sanitize_component(display_name)
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}-{index}"
        index += 1
    used.add(candidate)
    return candidate


def parse_agent_team_request(text: str) -> tuple[bool, list[TeamRole]]:
    """Parse an AgentTeams request and any explicitly listed dynamic roles."""
    if not isinstance(text, str) or not _TEAM_TRIGGER_RE.search(text):
        return False, []

    roles: list[TeamRole] = []
    used: set[str] = set()
    for raw_line in text.splitlines():
        match = _ROLE_LINE_RE.match(raw_line)
        if not match:
            continue
        display_name = match.group(1).strip().strip("-* ")
        responsibility = match.group(2).strip()
        normalized = display_name.lower().replace(" ", "")
        if any(normalized.startswith(prefix.replace(" ", "")) for prefix in _NON_ROLE_HEADINGS):
            continue
        if len(display_name) > 30 or len(responsibility) < 4:
            continue
        if any(ch in display_name for ch in "，,。.;；/\\()（）[]【】"):
            continue
        roles.append(
            TeamRole(
                _role_name_from_display(display_name, used),
                display_name,
                responsibility,
            )
        )
        if len(roles) >= 8:
            break
    return True, roles


def new_team_name() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"agentteams-{stamp}-{uuid.uuid4().hex[:6]}"


def _roles_from_data(data: Any) -> tuple[TeamRole, ...]:
    roles: list[TeamRole] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            display_name = str(item.get("display_name") or name).strip()
            responsibility = str(item.get("responsibility") or "").strip()
            if name and display_name and responsibility:
                roles.append(TeamRole(name, display_name, responsibility))
    return tuple(roles) or DEFAULT_TEAM_ROLES


def get_team_roles(team_name: str | None = None, roles: tuple[TeamRole, ...] | list[TeamRole] | None = None) -> tuple[TeamRole, ...]:
    if roles:
        return tuple(roles)
    if team_name:
        team = read_team(team_name)
        if team:
            return _roles_from_data(team.get("roles"))
    return DEFAULT_TEAM_ROLES


def render_role_lines(mode: str, roles: tuple[TeamRole, ...] | list[TeamRole] | None = None) -> list[str]:
    active_roles = get_team_roles(roles=roles)
    lines = [
        f"AgentTeams 已开启，模式：{mode}",
        "角色分工：",
        f"  - {TEAM_LEAD_ROLE.display_name} ({TEAM_LEAD_ROLE.name})：{TEAM_LEAD_ROLE.responsibility}",
    ]
    for role in active_roles:
        lines.append(f"  - {role.display_name} ({role.name})：{role.responsibility}")
    lines.extend(
        [
            "指令：",
            "  /team roles                         查看角色分工",
            "  /team status                        查看各角色状态",
            "  /team send <role|all> <instruction> 分别下发指令",
            "  /team tail <role> [lines]           查看角色日志",
            "  /team stop                          停止当前 AgentTeams",
        ]
    )
    return lines


def leader_prompt_addendum(mode: str, team_name: str, roles: tuple[TeamRole, ...] | list[TeamRole] | None = None) -> str:
    active_roles = get_team_roles(team_name, roles)
    role_text = "\n".join(render_role_lines(mode, active_roles))
    dispatch_lines = "\n".join(
        f"- {role.display_name} ({role.name})：{role.responsibility}" for role in active_roles
    )
    return f"""

[AgentTeams]
当前会话已启用 AgentTeams，team_name={team_name}。这是独立的团队协作功能，不是 delegate/subagent 工具。

你是 AgentTeams 的协调者。你必须先显式告诉用户 AgentTeams 的角色分工，然后使用 agent_team 工具把任务下发给独立角色；不要只要求用户手动下发。

当前可用角色：
{dispatch_lines}

协作流程：
- 先把用户目标拆成适合各角色的任务。
- 当前回合优先使用一次 agent_team(action="dispatch", assignments={{"<role>": "...", ...}}) 同时给多个角色下发任务并等待结果；不要对每个角色反复调用 status/tail 形成轮询循环。
- 如果 dispatch 返回 timed_out=true，向用户说明仍在运行，并用 action=status 展示状态后停止本轮，不要继续循环查询。
- 不要改用 delegate_task 替代 AgentTeams。
- 不要声称已经让某个角色工作，除非已经通过 agent_team 工具或 /team send 下发了对应任务。
- 汇总时说明信息来自哪个角色，并给出最终答案。

{role_text}
""".strip()


def role_prompt(role_name: str, team_name: str) -> str:
    role = role_by_name(role_name, team_name=team_name)
    return f"""
你是 OWLS AgentTeams 中的 {role.display_name} ({role.name})，team_name={team_name}。

你的职责：{role.responsibility}

工作规则：
- 你是独立 teammate 进程，不是 subagent/delegate 工具。
- 只处理协调者或用户通过 AgentTeams 队列下发给你的任务。
- 每次任务先简短说明你的理解和计划，再执行必要工作。
- 输出要便于协调者汇总：包含结论、证据、改动文件、命令结果和剩余风险。
- 如果任务不适合你的角色，说明原因并给出需要转交的角色。
""".strip()


def role_by_name(name: str, *, team_name: str | None = None, roles: tuple[TeamRole, ...] | list[TeamRole] | None = None) -> TeamRole:
    needle = str(name or "").strip()
    needle_lower = needle.lower()
    for role in get_team_roles(team_name, roles):
        aliases = {
            role.name,
            role.display_name,
            sanitize_component(role.name),
            sanitize_component(role.display_name),
        }
        if needle in aliases or needle_lower in {alias.lower() for alias in aliases}:
            return role
    raise ValueError(f"Unknown AgentTeams role: {name}")


def team_dir(team_name: str) -> Path:
    return teams_root() / sanitize_component(team_name)


def team_file(team_name: str) -> Path:
    return team_dir(team_name) / "team.json"


def inbox_file(team_name: str, role_name: str) -> Path:
    return team_dir(team_name) / "inboxes" / f"{sanitize_component(role_name)}.json"


def output_file(team_name: str, role_name: str) -> Path:
    return team_dir(team_name) / "outputs" / f"{sanitize_component(role_name)}.json"


def log_file(team_name: str, role_name: str) -> Path:
    return team_dir(team_name) / "logs" / f"{sanitize_component(role_name)}.log"


def _ensure_team_dirs(team_name: str) -> None:
    base = team_dir(team_name)
    for child in ("inboxes", "outputs", "logs"):
        (base / child).mkdir(parents=True, exist_ok=True)


class JsonListStore:
    def __init__(self, path: Path):
        self.path = path

    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_fh = open(lock_path, "a+", encoding="utf-8")
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        return lock_fh

    def read(self) -> list[dict[str, Any]]:
        lock_fh = self._locked()
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
            if not raw:
                return []
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
            lock_fh.close()

    def update(self, mutator) -> Any:
        lock_fh = self._locked()
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
            items = json.loads(raw) if raw else []
            if not isinstance(items, list):
                items = []
            result = mutator(items)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
            return result
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
            lock_fh.close()


def send_team_message(team_name: str, role_name: str, text: str, sender: str = "lead") -> list[str]:
    roles = get_team_roles(team_name)
    targets = [role.name for role in roles] if str(role_name).strip().lower() == "all" else [role_by_name(role_name, team_name=team_name).name]
    sent: list[str] = []
    for target in targets:
        store = JsonListStore(inbox_file(team_name, target))

        def add(items: list[dict[str, Any]]) -> str:
            message_id = uuid.uuid4().hex
            items.append(
                {
                    "id": message_id,
                    "from": sender,
                    "text": text,
                    "status": "queued",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            return message_id

        sent.append(store.update(add))
    return targets


def dispatch_team_messages(
    team_name: str,
    assignments: dict[str, str],
    *,
    sender: str = "lead",
) -> dict[str, str]:
    """Send role-specific instructions and return role -> message_id."""
    message_ids: dict[str, str] = {}
    for role_name, text in assignments.items():
        role = role_by_name(role_name, team_name=team_name)
        store = JsonListStore(inbox_file(team_name, role.name))

        def add(items: list[dict[str, Any]]) -> str:
            message_id = uuid.uuid4().hex
            items.append(
                {
                    "id": message_id,
                    "from": sender,
                    "text": str(text),
                    "status": "queued",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            return message_id

        message_ids[role.name] = store.update(add)
    return message_ids


def claim_next_message(team_name: str, role_name: str) -> dict[str, Any] | None:
    store = JsonListStore(inbox_file(team_name, role_name))

    def claim(items: list[dict[str, Any]]) -> dict[str, Any] | None:
        for item in items:
            if item.get("status") == "queued":
                item["status"] = "processing"
                item["started_at"] = datetime.now().isoformat(timespec="seconds")
                return dict(item)
        return None

    return store.update(claim)


def complete_message(team_name: str, role_name: str, message_id: str, status: str) -> None:
    store = JsonListStore(inbox_file(team_name, role_name))

    def mark(items: list[dict[str, Any]]) -> None:
        for item in items:
            if item.get("id") == message_id:
                item["status"] = status
                item["completed_at"] = datetime.now().isoformat(timespec="seconds")
                return

    store.update(mark)


def append_output(team_name: str, role_name: str, message: dict[str, Any], response: str) -> None:
    store = JsonListStore(output_file(team_name, role_name))

    def add(items: list[dict[str, Any]]) -> None:
        items.append(
            {
                "message_id": message.get("id"),
                "task": message.get("text", ""),
                "response": response,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        )

    store.update(add)


def read_outputs(team_name: str, role_name: str) -> list[dict[str, Any]]:
    return JsonListStore(output_file(team_name, role_name)).read()


def wait_for_team_outputs(
    team_name: str,
    message_ids: dict[str, str],
    *,
    timeout_seconds: float = 180.0,
    poll_interval: float = 1.0,
) -> dict[str, Any]:
    """Wait for dispatched message outputs, returning completed and pending roles."""
    deadline = time.time() + max(timeout_seconds, 0.0)
    completed: dict[str, dict[str, Any]] = {}
    pending = set(message_ids)
    while pending and time.time() <= deadline:
        for role_name in list(pending):
            expected_id = message_ids[role_name]
            for item in read_outputs(team_name, role_name):
                if item.get("message_id") == expected_id:
                    completed[role_name] = item
                    pending.remove(role_name)
                    break
        if pending:
            time.sleep(max(poll_interval, 0.2))
    return {
        "completed": completed,
        "pending": sorted(pending),
        "timed_out": bool(pending),
    }


def read_team(team_name: str) -> dict[str, Any] | None:
    path = team_file(team_name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def write_team(team_name: str, data: dict[str, Any]) -> None:
    path = team_file(team_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class AgentTeamManager:
    def __init__(
        self,
        *,
        mode: str,
        model: str | None,
        cwd: str,
        console=None,
        roles: tuple[TeamRole, ...] | list[TeamRole] | None = None,
    ):
        if mode not in {"true", "tmux"}:
            raise ValueError("teammate-mode must be 'true' or 'tmux'")
        self.mode = mode
        self.model = model
        self.cwd = cwd
        self.console = console
        self.roles = get_team_roles(roles=roles)
        self.team_name = new_team_name()
        self.members: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        _ensure_team_dirs(self.team_name)
        team = {
            "name": self.team_name,
            "mode": self.mode,
            "cwd": self.cwd,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "lead_pid": os.getpid(),
            "roles": [asdict(role) for role in self.roles],
            "members": {},
        }
        write_team(self.team_name, team)
        if self.mode == "tmux":
            self._start_tmux_workers()
        else:
            self._start_background_workers()
        self._save_members()

    def role_lines(self) -> list[str]:
        return render_role_lines(self.mode, self.roles)

    def send(self, role_name: str, text: str) -> list[str]:
        return send_team_message(self.team_name, role_name, text)

    def status_lines(self) -> list[str]:
        team = read_team(self.team_name) or {}
        members = team.get("members") or self.members
        lines = [f"AgentTeams: {self.team_name} ({self.mode})"]
        for role in self.roles:
            member = members.get(role.name, {})
            pid = member.get("pid")
            pane_id = member.get("pane_id")
            alive = _pane_alive(pane_id) if pane_id else (_pid_alive(pid) if pid else False)
            queued, processing, done = _message_counts(self.team_name, role.name)
            pane = f", pane={pane_id}" if pane_id else ""
            if processing:
                action = "处理中"
            elif queued:
                action = "等待处理队列任务"
            elif alive:
                action = "等待协调者下发任务"
            else:
                action = "已停止"
            lines.append(
                f"  - {role.display_name} ({role.name}): "
                f"{'running' if alive else 'stopped'}"
                f"{', pid=' + str(pid) if pid else ''}{pane}, "
                f"queued={queued}, processing={processing}, done={done}, action={action}"
            )
        return lines

    def tail(self, role_name: str, lines: int = 80) -> str:
        role = role_by_name(role_name, roles=self.roles)
        path = log_file(self.team_name, role.name)
        try:
            data = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return f"No log file yet: {path}"
        return "\n".join(data[-max(lines, 1):])

    def stop(self) -> None:
        team = read_team(self.team_name) or {}
        members = team.get("members") or self.members
        for member in members.values():
            pane_id = member.get("pane_id")
            if pane_id:
                subprocess.run(["tmux", "kill-pane", "-t", pane_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                continue
            pid = member.get("pid")
            if pid and _pid_alive(pid):
                try:
                    os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except Exception:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                    except Exception:
                        pass
        team["stopped_at"] = datetime.now().isoformat(timespec="seconds")
        write_team(self.team_name, team)

    def _worker_cmd(self, role_name: str) -> list[str]:
        cmd = [
            sys.executable,
            "-m",
            "owls_cli.main",
            "chat",
            "--team-worker",
            "--team-name",
            self.team_name,
            "--team-role",
            role_name,
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        return cmd

    def _start_background_workers(self) -> None:
        for role in self.roles:
            path = log_file(self.team_name, role.name)
            path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = open(path, "a", encoding="utf-8")
            try:
                env = os.environ.copy()
                env["OWLS_AGENT_TEAM_WORKER"] = "1"
                proc = subprocess.Popen(
                    self._worker_cmd(role.name),
                    cwd=self.cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=env,
                )
            finally:
                log_fh.close()
            self.members[role.name] = {
                "pid": proc.pid,
                "log": str(path),
                "display_name": role.display_name,
            }

    def _start_tmux_workers(self) -> None:
        if not _tmux_available():
            raise RuntimeError("tmux not found. Install tmux to use --teammate-mode tmux.")
        inside_tmux = bool(os.environ.get("TMUX"))
        target_session = None
        first = True
        for role in self.roles:
            shell_cmd = " ".join(shlex.quote(part) for part in self._worker_cmd(role.name))
            title = shlex.quote(f"OWLS AgentTeams - {role.display_name}")
            pane_cmd = (
                f"printf '\\033]2;%s\\007' {title}; "
                f"cd {shlex.quote(self.cwd)} && {_worker_env_prefix()} {shell_cmd}"
            )
            if inside_tmux:
                args = ["tmux", "split-window", "-d", "-P", "-F", "#{pane_id}", pane_cmd]
            elif first:
                target_session = sanitize_component(self.team_name)
                args = ["tmux", "new-session", "-d", "-s", target_session, "-n", "AgentTeams", "-P", "-F", "#{pane_id}", pane_cmd]
            else:
                args = ["tmux", "split-window", "-d", "-t", target_session, "-P", "-F", "#{pane_id}", pane_cmd]
            result = subprocess.run(args, capture_output=True, text=True, check=True)
            pane_id = result.stdout.strip()
            subprocess.run(
                ["tmux", "select-pane", "-t", pane_id, "-T", f"OWLS {role.display_name}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.members[role.name] = {
                "pane_id": pane_id,
                "display_name": role.display_name,
                "log": str(log_file(self.team_name, role.name)),
            }
            first = False
        if inside_tmux:
            subprocess.run(["tmux", "select-layout", "tiled"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif target_session:
            subprocess.run(["tmux", "select-layout", "-t", target_session, "tiled"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.members["_attach"] = {"command": f"tmux attach -t {target_session}"}

    def _save_members(self) -> None:
        team = read_team(self.team_name) or {}
        team["members"] = self.members
        write_team(self.team_name, team)


def run_team_worker(*, team_name: str, role_name: str, model: str | None = None) -> None:
    role = role_by_name(role_name)
    os.environ["OWLS_AGENT_TEAM_WORKER"] = "1"
    os.environ.pop("OWLS_AGENT_TEAM_NAME", None)
    os.environ.pop("OWLS_AGENT_TEAM_MODE", None)
    _ensure_team_dirs(team_name)
    log_path = log_file(team_name, role_name)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"OWLS AgentTeams worker started: {role.display_name} ({role.name})")
    print(f"Team: {team_name}")
    print(f"Mailbox: {inbox_file(team_name, role_name)}")
    print("Waiting for /team send instructions...\n", flush=True)

    from cli import OWLSCLI

    cli = OWLSCLI(model=model, teammate_mode=None)
    cli.system_prompt = "\n\n".join(part for part in (cli.system_prompt, role_prompt(role_name, team_name)) if part).strip()

    while True:
        message = claim_next_message(team_name, role_name)
        if not message:
            time.sleep(1.0)
            continue
        task = str(message.get("text", "")).strip()
        print("\n" + "=" * 72)
        print(f"[{datetime.now().isoformat(timespec='seconds')}] Task from {message.get('from', 'lead')}:")
        print(task)
        print("=" * 72, flush=True)
        status = "done"
        response = ""
        try:
            response = cli.chat(task) or ""
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            status = "error"
            response = f"AgentTeams worker error: {exc}"
            print(response, flush=True)
        append_output(team_name, role_name, message, response)
        complete_message(team_name, role_name, str(message.get("id", "")), status)
        print(f"\n[{datetime.now().isoformat(timespec='seconds')}] Task {status}.\n", flush=True)


def _message_counts(team_name: str, role_name: str) -> tuple[int, int, int]:
    items = JsonListStore(inbox_file(team_name, role_name)).read()
    queued = sum(1 for item in items if item.get("status") == "queued")
    processing = sum(1 for item in items if item.get("status") == "processing")
    done = sum(1 for item in items if item.get("status") in {"done", "error"})
    return queued, processing, done


def _pid_alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _tmux_available() -> bool:
    return subprocess.run(["sh", "-lc", "command -v tmux >/dev/null 2>&1"]).returncode == 0


def tmux_available() -> bool:
    return _tmux_available()


def _worker_env_prefix() -> str:
    keys = [
        "PATH",
        "HOME",
        "OWLS_HOME",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MOONSHOT_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "NO_PROXY",
        "no_proxy",
    ]
    parts = ["OWLS_AGENT_TEAM_WORKER=1"]
    for key in keys:
        value = os.environ.get(key)
        if value:
            parts.append(f"{key}={shlex.quote(value)}")
    return "env " + " ".join(parts)


def _pane_alive(pane_id: Any) -> bool:
    if not pane_id:
        return False
    result = subprocess.run(
        ["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    return str(pane_id).strip() in {line.strip() for line in result.stdout.splitlines()}
