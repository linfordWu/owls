import asyncio
import json

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


class _FakeRequest:
    headers = {}

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def test_runs_direct_shell_command_without_creating_agent(monkeypatch):
    async def _run():
        adapter = APIServerAdapter(PlatformConfig())

        monkeypatch.setattr(
            "owls_cli.shell_detection.looks_like_direct_shell_command",
            lambda text: text == "ip addr",
        )

        def _fail_create_agent(*args, **kwargs):
            raise AssertionError("direct shell command should not create an agent")

        monkeypatch.setattr(adapter, "_create_agent", _fail_create_agent)
        monkeypatch.setattr(
            APIServerAdapter,
            "_execute_direct_shell_command",
            classmethod(lambda cls, command, session_id: f"ran {command} in {session_id}"),
        )

        response = await adapter._handle_runs(
            _FakeRequest({"input": "ip addr", "session_id": "web-session"})
        )

        assert response.status == 202
        run_id = json.loads(response.text)["run_id"]
        queue = adapter._run_streams[run_id]

        events = []
        for _ in range(4):
            event = await asyncio.wait_for(queue.get(), timeout=1)
            events.append(event)
            if event is None:
                break

        assert [event["event"] for event in events if event] == [
            "run.started",
            "message.delta",
            "run.completed",
        ]
        assert events[1]["delta"] == "ran ip addr in web-session"
        assert events[2]["usage"]["total_tokens"] == 0

    asyncio.run(_run())


def test_direct_shell_result_format_preserves_terminal_output():
    formatted = APIServerAdapter._format_direct_shell_result(
        {"output": "hello\nworld", "exit_code": 0, "error": None}
    )

    assert formatted == "```text\nhello\nworld\n```"
