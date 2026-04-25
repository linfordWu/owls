"""Tests for CLI shell mode and direct shell-command fast paths."""

import asyncio
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock


def _make_cli():
    from cli import OWLSCLI

    cli = OWLSCLI.__new__(OWLSCLI)
    cli._shell_mode = "off"
    cli._shell_session_id = None
    cli._shell_output_snapshot = ""
    cli._shell_full_transcript = ""
    cli._shell_context_label = ""
    cli._shell_transcript_recorded = False
    cli._agent_running = False
    cli._console_print = MagicMock()
    cli._approval_callback = MagicMock(return_value="once")
    cli.session_id = "session-123"
    cli.conversation_history = []
    return cli


def test_direct_shell_command_detection_accepts_simple_commands():
    from cli import _looks_like_direct_shell_command

    assert _looks_like_direct_shell_command("ls -hl") is True
    assert _looks_like_direct_shell_command("git status") is True


def test_direct_shell_command_detection_rejects_natural_language_and_interactive_bins():
    from cli import _looks_like_direct_shell_command

    assert _looks_like_direct_shell_command("请帮我看看当前目录") is False
    assert _looks_like_direct_shell_command("git status?") is False
    assert _looks_like_direct_shell_command("python") is False


def test_parse_interactive_ssh_request_accepts_natural_language():
    from owls_cli.shell_detection import parse_interactive_ssh_request

    request = parse_interactive_ssh_request("ssh 远程连接192.168.0.37")

    assert request == {
        "target": "192.168.0.37",
        "host": "192.168.0.37",
    }


def test_parse_interactive_ssh_request_accepts_user_and_port():
    from owls_cli.shell_detection import parse_interactive_ssh_request

    request = parse_interactive_ssh_request("ssh 远程登录 root@192.168.0.37 端口 2222")

    assert request == {
        "target": "root@192.168.0.37",
        "host": "192.168.0.37",
        "user": "root",
        "port": 2222,
    }


def test_shell_command_enters_managed_mode_when_interactive_backend_unavailable(monkeypatch):
    cli = _make_cli()
    monkeypatch.setattr(cli, "_shell_supports_interactive_session", lambda: False)

    cli._handle_shell_command("/shell")

    assert cli._shell_mode == "managed"
    assert cli._shell_session_id is None


def test_shell_command_enters_native_shell_on_local_backend(monkeypatch):
    cli = _make_cli()
    native_calls = []

    monkeypatch.setattr(cli, "_shell_supports_interactive_session", lambda: True)
    monkeypatch.setattr(cli, "_run_native_shell_session", lambda: native_calls.append(True))

    cli._handle_shell_command("/shell")

    assert native_calls == [True]
    assert cli._shell_mode == "off"


def test_shell_guarded_enters_interactive_mode_on_local_backend(monkeypatch):
    cli = _make_cli()

    monkeypatch.setattr(cli, "_shell_supports_interactive_session", lambda: True)
    monkeypatch.setattr("tools.environments.local._find_shell", lambda: "/bin/bash")

    fake_registry = SimpleNamespace(
        spawn_local=lambda **kwargs: SimpleNamespace(id="proc_shell"),
    )
    monkeypatch.setattr("tools.process_registry.process_registry", fake_registry)

    cli._handle_shell_command("/shell guarded")

    assert cli._shell_mode == "pty"
    assert cli._shell_session_id == "proc_shell"


def test_natural_language_ssh_request_starts_interactive_ssh_session(monkeypatch):
    cli = _make_cli()
    calls = []

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ssh" if name == "ssh" else None)
    monkeypatch.setattr(cli, "_run_native_ssh_session", lambda argv, request: calls.append((argv, request)))

    handled = cli._maybe_handle_interactive_ssh_request("ssh 远程连接192.168.0.37")

    assert handled is True
    assert calls == [(
        [
            "ssh",
            "-tt",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ServerAliveInterval=30",
            "192.168.0.37",
        ],
        {
            "target": "192.168.0.37",
            "host": "192.168.0.37",
        },
    )]


def test_native_ssh_session_records_script_transcript(monkeypatch, tmp_path):
    cli = _make_cli()
    cli._app = object()
    transcript_file = tmp_path / "owls-ssh-abc123def456.log"
    calls = []

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/script" if name == "script" else "/usr/bin/ssh")
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr("uuid.uuid4", lambda: SimpleNamespace(hex="abc123def456"))

    def _fake_run_in_terminal(callback):
        calls.append("handoff")
        callback()

    def _fake_run(argv, env=None, check=None):
        calls.append((argv, check))
        Path(argv[-1]).write_text("remote banner\n$ ip addr\neth0: UP\n", encoding="utf-8")

    monkeypatch.setattr("prompt_toolkit.application.run_in_terminal", _fake_run_in_terminal)
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tools.environments.local._sanitize_subprocess_env",
        lambda env, extra: {"PATH": env.get("PATH", "")},
    )

    cli._run_native_ssh_session(
        [
            "ssh",
            "-tt",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ServerAliveInterval=30",
            "192.168.0.37",
        ],
        {"target": "192.168.0.37", "host": "192.168.0.37"},
    )

    assert calls[0] == "handoff"
    assert calls[1][0] == [
        "/usr/bin/script",
        "-q",
        "-f",
        "-c",
        "ssh -tt -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 192.168.0.37",
        str(transcript_file),
    ]
    assert "eth0: UP" in cli.conversation_history[0]["content"]
    assert cli._shell_mode == "off"


def test_terminal_handoff_from_background_thread_uses_running_app_loop():
    cli = _make_cli()
    loop = asyncio.new_event_loop()
    cli._app = SimpleNamespace(loop=loop, context=None)
    calls = []

    def _run_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    loop_thread = threading.Thread(target=_run_loop)
    loop_thread.start()
    try:
        worker = threading.Thread(target=lambda: cli._run_terminal_handoff(lambda: calls.append("handoff")))
        worker.start()
        worker.join(timeout=3)

        assert not worker.is_alive()
        assert calls == ["handoff"]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=3)
        loop.close()


def test_shell_command_inline_detection_accepts_shell():
    from cli import OWLSCLI

    cli = OWLSCLI.__new__(OWLSCLI)

    assert cli._should_handle_shell_command_inline("/shell") is True
    assert cli._should_handle_shell_command_inline("/shell guarded") is True
    assert cli._should_handle_shell_command_inline("/model") is False


def test_run_native_shell_session_hands_off_to_login_shell(monkeypatch):
    cli = _make_cli()
    cli._app = object()
    calls = []

    monkeypatch.setattr("tools.environments.local._find_shell", lambda: "/bin/bash")
    monkeypatch.setattr(
        "tools.environments.local._sanitize_subprocess_env",
        lambda env, extra: {"PATH": env.get("PATH", "")},
    )
    monkeypatch.setenv("TERMINAL_CWD", "/tmp")

    def _fake_run_in_terminal(callback):
        calls.append("handoff")
        callback()

    def _fake_subprocess_run(argv, cwd=None, env=None, check=None):
        calls.append((argv, cwd, env.get("SHELL"), check))

    monkeypatch.setattr("prompt_toolkit.application.run_in_terminal", _fake_run_in_terminal)
    monkeypatch.setattr("subprocess.run", _fake_subprocess_run)

    cli._run_native_shell_session()

    assert calls[0] == "handoff"
    assert calls[1] == (["/bin/bash", "-l"], "/tmp", "/bin/bash", False)
    cli._console_print.assert_called_once()


def test_render_shell_result_is_silent_on_success_without_output():
    cli = _make_cli()

    cli._render_shell_result({"output": "", "error": None, "exit_code": 0})

    cli._console_print.assert_not_called()


def test_managed_shell_input_executes_via_terminal_tool(monkeypatch):
    cli = _make_cli()
    cli._shell_mode = "managed"

    terminal_calls = []

    def _fake_terminal_tool(**kwargs):
        terminal_calls.append(kwargs)
        return json.dumps({"output": "file.txt", "exit_code": 0, "error": None})

    monkeypatch.setattr("tools.terminal_tool.terminal_tool", _fake_terminal_tool)

    handled = cli._handle_shell_mode_input("ls")

    assert handled is True
    assert terminal_calls[0]["command"] == "ls"
    assert terminal_calls[0]["task_id"] == "session-123"


def test_interactive_shell_input_checks_guards_before_submitting(monkeypatch):
    cli = _make_cli()
    cli._shell_mode = "pty"
    cli._shell_session_id = "proc_shell"

    submits = []
    monkeypatch.setattr(cli, "_prompt_shell_guard", lambda command: command != "rm -rf /")
    fake_registry = SimpleNamespace(
        submit_stdin=lambda session_id, data: submits.append((session_id, data)) or {"status": "ok"},
    )
    monkeypatch.setattr("tools.process_registry.process_registry", fake_registry)

    assert cli._handle_shell_mode_input("pwd") is True
    assert submits == [("proc_shell", "pwd")]

    submits.clear()
    assert cli._handle_shell_mode_input("rm -rf /") is True
    assert submits == []


def test_ssh_mode_input_does_not_echo_or_run_local_guard(monkeypatch):
    cli = _make_cli()
    cli._shell_mode = "ssh"
    cli._shell_session_id = "proc_ssh"

    submits = []
    monkeypatch.setattr(cli, "_prompt_shell_guard", lambda command: (_ for _ in ()).throw(AssertionError("guard should not run for ssh input")))
    fake_registry = SimpleNamespace(
        submit_stdin=lambda session_id, data: submits.append((session_id, data)) or {"status": "ok"},
    )
    monkeypatch.setattr("tools.process_registry.process_registry", fake_registry)

    assert cli._handle_shell_mode_input("secret-password") is True

    assert submits == [("proc_ssh", "secret-password")]
    cli._console_print.assert_not_called()


def test_ssh_transcript_is_added_to_conversation_history_on_exit():
    cli = _make_cli()
    cli._shell_mode = "ssh"
    cli._shell_session_id = "proc_ssh"
    cli._shell_context_label = "Interactive SSH session: root@192.168.0.37"
    cli._shell_output_snapshot = "Linux host\n$ ip addr\neth0: UP\n"
    cli._shell_full_transcript = "banner\nLinux host\n$ ip addr\neth0: UP\n"

    cli._leave_shell_mode("[dim]SSH session closed.[/]")

    assert cli._shell_mode == "off"
    assert len(cli.conversation_history) == 1
    assert cli.conversation_history[0]["role"] == "user"
    assert "root@192.168.0.37" in cli.conversation_history[0]["content"]
    assert "banner" in cli.conversation_history[0]["content"]
    assert "eth0: UP" in cli.conversation_history[0]["content"]


def test_direct_shell_fast_path_bypasses_agent(monkeypatch):
    cli = _make_cli()
    executed = []
    monkeypatch.setattr(cli, "_execute_direct_shell_command", lambda command: executed.append(command) or {})

    handled = cli._maybe_handle_direct_shell_command("ls -hl")

    assert handled is True
    assert executed == ["ls -hl"]


def test_drain_interactive_shell_output_returns_to_chat_when_session_exits(monkeypatch):
    cli = _make_cli()
    cli._shell_mode = "pty"
    cli._shell_session_id = "proc_shell"
    cli._shell_output_snapshot = ""

    fake_session = SimpleNamespace(output_buffer="bye\n", exited=True, _lock=SimpleNamespace(__enter__=lambda self: None, __exit__=lambda self, exc_type, exc, tb: False))

    class _FakeLock:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_session._lock = _FakeLock()
    fake_registry = SimpleNamespace(get=lambda session_id: fake_session)
    monkeypatch.setattr("tools.process_registry.process_registry", fake_registry)

    cli._drain_interactive_shell_output()

    assert cli._shell_mode == "off"
    assert cli._shell_session_id is None
