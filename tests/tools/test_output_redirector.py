"""Tests for tools.output_redirector."""

import subprocess
from unittest.mock import MagicMock

import pytest

from tools.output_redirector import CompletedProcess, OutputRedirector


class TestOutputRedirector:
    def test_run_echo_command(self):
        redir = OutputRedirector(mode="silent")
        result = redir.run(["echo", "hello world"])
        assert isinstance(result, CompletedProcess)
        assert result.returncode == 0
        assert "hello world" in result.stdout
        assert result.command == ["echo", "hello world"]

    def test_run_with_cwd(self, tmp_path):
        redir = OutputRedirector(mode="silent")
        result = redir.run(["pwd"], cwd=str(tmp_path))
        assert result.returncode == 0
        assert str(tmp_path) in result.stdout

    def test_run_with_env(self):
        redir = OutputRedirector(mode="silent")
        result = redir.run(["printenv", "TEST_VAR"], env={"TEST_VAR": "42"})
        assert result.returncode == 0
        assert "42" in result.stdout

    def test_run_stderr_capture(self):
        redir = OutputRedirector(mode="silent")
        result = redir.run(["python3", "-c", "import sys; sys.stderr.write('error line')"])
        assert result.returncode == 0
        assert "error line" in result.stderr

    def test_run_nonzero_exit(self):
        redir = OutputRedirector(mode="silent")
        result = redir.run(["python3", "-c", "import sys; sys.exit(1)"])
        assert result.returncode == 1

    def test_timeout_kills_process(self):
        redir = OutputRedirector(mode="silent")
        result = redir.run(["sleep", "10"], timeout=1)
        # Process should be killed due to timeout
        assert result.returncode != 0 or result.returncode is None

    def test_silent_mode_no_audit_log(self):
        audit = MagicMock()
        redir = OutputRedirector(mode="silent", audit_logger=audit)
        redir.run(["echo", "silent"])
        audit.log_event.assert_not_called()

    def test_log_mode_calls_audit_logger(self):
        audit = MagicMock()
        redir = OutputRedirector(mode="log", audit_logger=audit)
        result = redir.run(["echo", "audit me"])
        assert result.returncode == 0
        audit.log_event.assert_called_once()
        call_args = audit.log_event.call_args[0][0]
        assert call_args["event_type"] == "command_result"
        assert "echo" in call_args["command"]

    def test_display_log_mode_calls_audit_logger(self):
        audit = MagicMock()
        redir = OutputRedirector(mode="display+log", audit_logger=audit)
        result = redir.run(["echo", "display and audit"])
        assert result.returncode == 0
        audit.log_event.assert_called_once()

    def test_display_mode_no_audit_log(self):
        audit = MagicMock()
        redir = OutputRedirector(mode="display", audit_logger=audit)
        redir.run(["echo", "display only"])
        audit.log_event.assert_not_called()

    def test_no_audit_logger_does_not_crash(self):
        redir = OutputRedirector(mode="display+log", audit_logger=None)
        result = redir.run(["echo", "no audit logger"])
        assert result.returncode == 0

    def test_completed_process_fields(self):
        redir = OutputRedirector(mode="silent")
        result = redir.run(["echo", "fields"], cwd="/tmp")
        assert result.command == ["echo", "fields"]
        assert result.cwd == "/tmp"
        assert hasattr(result, "returncode")
        assert hasattr(result, "stdout")
        assert hasattr(result, "stderr")
