import json
import os

from owls_cli.agent_teams import (
    DEFAULT_TEAM_ROLES,
    TeamRole,
    _ensure_team_dirs,
    claim_next_message,
    complete_message,
    get_team_roles,
    inbox_file,
    new_team_name,
    output_file,
    parse_agent_team_request,
    render_role_lines,
    send_team_message,
    wait_for_team_outputs,
    write_team,
)
from tools.agent_team_tool import agent_team, check_requirements


def test_agent_teams_role_lines_are_user_visible():
    lines = render_role_lines("true")

    assert any("AgentTeams 已开启" in line for line in lines)
    assert any("角色分工" in line for line in lines)
    for role in DEFAULT_TEAM_ROLES:
        assert any(role.name in line and role.display_name in line for line in lines)


def test_parse_agent_team_request_extracts_prompt_roles():
    prompt = """你现在启用 Agent Teams 多智能体协作模式，自动拆分多角色分工完成任务：

    架构规划师：拆解整体需求、制定执行步骤、划分子任务
    研发工程师：编写完整可运行代码、配置逻辑
    测试工程师：边界测试、漏洞检查、输出问题清单
    文档专员：整理流程、注释、最终总结报告

任务需求用 Python 编写一个轻量化本地文件管理小工具，实现：
    遍历指定文件夹
"""
    enabled, roles = parse_agent_team_request(prompt)

    assert enabled is True
    assert [role.display_name for role in roles] == ["架构规划师", "研发工程师", "测试工程师", "文档专员"]
    assert roles[0].name == "架构规划师"
    assert "划分子任务" in roles[0].responsibility


def test_agent_teams_mailbox_claim_and_complete(monkeypatch, tmp_path):
    monkeypatch.setenv("OWLS_HOME", str(tmp_path / ".owls"))
    team_name = new_team_name()
    role_name = "researcher"
    _ensure_team_dirs(team_name)

    targets = send_team_message(team_name, role_name, "inspect the repo")
    assert targets == [role_name]
    assert inbox_file(team_name, role_name).exists()

    message = claim_next_message(team_name, role_name)
    assert message is not None
    assert message["text"] == "inspect the repo"

    complete_message(team_name, role_name, message["id"], "done")
    assert claim_next_message(team_name, role_name) is None


def test_agent_teams_dynamic_roles_are_persisted_and_addressable(monkeypatch, tmp_path):
    monkeypatch.setenv("OWLS_HOME", str(tmp_path / ".owls"))
    team_name = new_team_name()
    roles = [TeamRole("架构规划师", "架构规划师", "拆解任务")]
    _ensure_team_dirs(team_name)
    write_team(team_name, {"name": team_name, "roles": [role.__dict__ for role in roles], "members": {}})

    assert get_team_roles(team_name) == tuple(roles)
    targets = send_team_message(team_name, "架构规划师", "请拆解需求")
    assert targets == ["架构规划师"]
    assert claim_next_message(team_name, "架构规划师")["text"] == "请拆解需求"


def test_agent_team_tool_requires_enabled_team(monkeypatch, tmp_path):
    monkeypatch.setenv("OWLS_HOME", str(tmp_path / ".owls"))
    monkeypatch.delenv("OWLS_AGENT_TEAM_NAME", raising=False)
    assert check_requirements() is False

    team_name = new_team_name()
    _ensure_team_dirs(team_name)
    write_team(team_name, {"name": team_name, "members": {}})
    monkeypatch.setenv("OWLS_AGENT_TEAM_NAME", team_name)

    assert check_requirements() is True
    raw = agent_team({"action": "send", "role": "researcher", "instruction": "inspect"}, task_id="test")
    assert '"sent_to": ["researcher"]' in raw
    assert claim_next_message(team_name, "researcher")["text"] == "inspect"


def test_agent_team_status_reports_role_action(monkeypatch, tmp_path):
    monkeypatch.setenv("OWLS_HOME", str(tmp_path / ".owls"))
    team_name = new_team_name()
    _ensure_team_dirs(team_name)
    write_team(
        team_name,
        {
            "name": team_name,
            "roles": [{"name": "测试工程师", "display_name": "测试工程师", "responsibility": "检查问题"}],
            "members": {"测试工程师": {"pid": os.getpid()}},
        },
    )
    monkeypatch.setenv("OWLS_AGENT_TEAM_NAME", team_name)

    raw = agent_team({"action": "status"}, task_id="test")
    status = json.loads(raw)["status"][0]

    assert status["display_name"] == "测试工程师"
    assert status["action"] == "等待协调者下发任务"


def test_agent_team_dispatch_waits_for_outputs(monkeypatch, tmp_path):
    monkeypatch.setenv("OWLS_HOME", str(tmp_path / ".owls"))
    team_name = new_team_name()
    _ensure_team_dirs(team_name)
    write_team(
        team_name,
        {
            "name": team_name,
            "roles": [{"name": "研发工程师", "display_name": "研发工程师", "responsibility": "实现代码"}],
            "members": {},
        },
    )
    monkeypatch.setenv("OWLS_AGENT_TEAM_NAME", team_name)
    raw = agent_team(
        {
            "action": "dispatch",
            "assignments": {"研发工程师": "实现文件管理工具"},
            "timeout_seconds": 0.1,
        },
        task_id="test",
    )
    data = json.loads(raw)
    assert data["sent_to"] == ["研发工程师"]
    assert data["timed_out"] is True

    message_id = data["message_ids"]["研发工程师"]
    output_file(team_name, "研发工程师").write_text(
        json.dumps(
            [
                {
                    "message_id": message_id,
                    "task": "实现文件管理工具",
                    "response": "done",
                    "created_at": "now",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = wait_for_team_outputs(team_name, {"研发工程师": message_id}, timeout_seconds=0.1)
    assert result["timed_out"] is False
    assert result["completed"]["研发工程师"]["response"] == "done"
