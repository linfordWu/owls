"""Tests for tools.sandbox_policy."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import tools.sandbox_policy as sandbox_policy
from tools.sandbox_policy import (
    apply_sandbox_profile,
    is_sandbox_available,
    _has_landlock,
    _has_unshare,
)


class TestSandboxAvailability:
    def test_full_mutate_always_available(self):
        assert is_sandbox_available("full-mutate") is True

    def test_inspect_ro_availability(self):
        # Result depends on kernel support
        result = is_sandbox_available("inspect-ro")
        assert isinstance(result, bool)

    def test_diag_net_availability(self):
        result = is_sandbox_available("diag-net")
        assert isinstance(result, bool)

    def test_mutate_config_availability(self):
        result = is_sandbox_available("mutate-config")
        assert isinstance(result, bool)

    def test_unknown_profile_returns_false(self):
        assert is_sandbox_available("unknown-profile") is False  # type: ignore[arg-type]


class TestApplySandboxProfile:
    def test_full_mutate_returns_true(self):
        assert apply_sandbox_profile("full-mutate", "task_123") is True

    def test_inspect_ro_returns_false_when_landlock_unavailable(self, monkeypatch):
        monkeypatch.setattr(sandbox_policy, "_has_landlock", lambda: False)

        result = apply_sandbox_profile("inspect-ro", "task_123")
        assert result is False

    def test_diag_net_returns_false_when_unshare_unavailable(self, monkeypatch):
        monkeypatch.setattr(sandbox_policy, "_has_unshare", lambda: False)

        result = apply_sandbox_profile("diag-net", "task_123")
        assert result is False

    def test_mutate_config_returns_false_when_landlock_unavailable(self, monkeypatch):
        monkeypatch.setattr(sandbox_policy, "_has_landlock", lambda: False)

        result = apply_sandbox_profile("mutate-config", "task_123")
        assert result is False

    def test_unknown_profile_returns_false(self):
        assert apply_sandbox_profile("unknown", "task_123") is False  # type: ignore[arg-type]

    def test_inspect_ro_landlock_enforces_read_only_filesystem_in_child_process(self, tmp_path):
        if not _has_landlock():
            pytest.skip("Landlock is not available on this kernel")

        script = textwrap.dedent(
            r"""
            import errno
            import json
            import sys
            from pathlib import Path

            from tools.sandbox_policy import apply_sandbox_profile

            root = Path(sys.argv[1])
            existing = root / "existing.txt"
            new_file = root / "new.txt"
            existing.write_text("original", encoding="utf-8")

            applied = apply_sandbox_profile("inspect-ro", "landlock-test")
            if not applied:
                print(json.dumps({"applied": False}))
                raise SystemExit(0)

            def is_blocked(operation):
                try:
                    operation()
                except OSError as exc:
                    return exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}
                return False

            result = {
                "applied": True,
                "can_read_existing": existing.read_text(encoding="utf-8") == "original",
                "blocks_existing_write": is_blocked(
                    lambda: existing.write_text("modified", encoding="utf-8")
                ),
                "blocks_new_file": is_blocked(
                    lambda: new_file.write_text("created", encoding="utf-8")
                ),
            }
            print(json.dumps(result))
            """
        )
        repo_root = Path(__file__).resolve().parents[2]
        completed = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr
        output_lines = [line for line in completed.stdout.splitlines() if line.strip()]
        assert output_lines, completed.stderr
        result = json.loads(output_lines[-1])
        assert result["applied"] is True, completed.stderr
        assert result["can_read_existing"] is True
        assert result["blocks_existing_write"] is True
        assert result["blocks_new_file"] is True


class TestLandlockRulesetConstruction:
    class FakeLibC:
        def __init__(self):
            self.created_access = None
            self.added_rules = []
            self.prctl_calls = []
            self.restricted = False

        def syscall(self, syscall_number, *args):
            if syscall_number == sandbox_policy._LANDLOCK_CREATE_RULESET:
                self.created_access = args[0]._obj.handled_access_fs
                return 10
            if syscall_number == sandbox_policy._LANDLOCK_ADD_RULE:
                self.added_rules.append(args[2]._obj.allowed_access)
                return 0
            if syscall_number == sandbox_policy._LANDLOCK_RESTRICT_SELF:
                self.restricted = True
                return 0
            raise AssertionError(f"unexpected syscall: {syscall_number}")

        def prctl(self, *args):
            self.prctl_calls.append(args)
            return 0

    def _patch_landlock_syscalls(self, monkeypatch):
        fake_libc = self.FakeLibC()
        monkeypatch.setattr(sandbox_policy.ctypes.util, "find_library", lambda name: "libc.so.6")
        monkeypatch.setattr(sandbox_policy.ctypes, "CDLL", lambda *args, **kwargs: fake_libc)
        monkeypatch.setattr(sandbox_policy.os, "open", lambda *args, **kwargs: 20)
        monkeypatch.setattr(sandbox_policy.os, "close", lambda fd: None)
        return fake_libc

    def test_inspect_ro_ruleset_handles_write_but_grants_root_read_only(self, monkeypatch):
        fake_libc = self._patch_landlock_syscalls(monkeypatch)

        assert sandbox_policy._landlock_restrict(sandbox_policy._ACCESS_FS_ROUGHLY_READ) is True

        assert fake_libc.created_access == sandbox_policy._ACCESS_FS_ROUGHLY_WRITE
        assert fake_libc.added_rules == [sandbox_policy._ACCESS_FS_ROUGHLY_READ]
        assert fake_libc.prctl_calls == [(sandbox_policy._PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)]
        assert fake_libc.restricted is True

    def test_mutate_config_keeps_root_read_only_and_adds_limited_write_grant(self, monkeypatch):
        fake_libc = self._patch_landlock_syscalls(monkeypatch)

        assert (
            sandbox_policy._landlock_restrict(
                sandbox_policy._ACCESS_FS_ROUGHLY_WRITE,
                allowed_rw_paths=["/allowed"],
            )
            is True
        )

        assert fake_libc.created_access == sandbox_policy._ACCESS_FS_ROUGHLY_WRITE
        assert fake_libc.added_rules == [
            sandbox_policy._ACCESS_FS_ROUGHLY_READ,
            sandbox_policy._ACCESS_FS_ROUGHLY_WRITE,
        ]


class TestHasHelpers:
    def test_has_unshare_returns_bool(self):
        assert isinstance(_has_unshare(), bool)

    def test_has_landlock_returns_bool(self):
        assert isinstance(_has_landlock(), bool)
