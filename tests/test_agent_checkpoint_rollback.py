"""
Unit test: CheckpointManager snapshot and rollback functionality.

Scenario
--------
The CheckpointManager creates transparent filesystem snapshots via shadow
git repos. Before file-mutating operations, a checkpoint is taken. If the
operation fails, the checkpoint can be restored to roll back changes.

These tests verify:
1. CheckpointManager can take a snapshot of a directory
2. Files modified after the checkpoint can be restored to their original state
3. New files created after the checkpoint are removed on restore
4. Deleted files are recreated on restore
5. Single-file restore works independently of full-directory restore
"""

import os
import tempfile
from pathlib import Path

import pytest

from tools.checkpoint_manager import CheckpointManager


class TestCheckpointManagerRollback:
    """Direct unit tests for CheckpointManager snapshot and restore."""

    def test_checkpoint_and_restore_full_directory(self, tmp_path):
        """
        Create files, take checkpoint, modify files, restore, assert originals.
        """
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        # Create original files
        original_file = work_dir / "config.txt"
        original_file.write_text("original_value=42\n", encoding="utf-8")

        sub_dir = work_dir / "subdir"
        sub_dir.mkdir()
        sub_file = sub_dir / "nested.txt"
        sub_file.write_text("nested_original\n", encoding="utf-8")

        # Take checkpoint
        cm = CheckpointManager(enabled=True, max_snapshots=10)
        ok = cm.ensure_checkpoint(str(work_dir), reason="pre-modify")
        assert ok is True, "Checkpoint should be taken successfully"

        # Verify checkpoint exists
        checkpoints = cm.list_checkpoints(str(work_dir))
        assert len(checkpoints) >= 1, "Expected at least one checkpoint"
        first_hash = checkpoints[0]["hash"]

        # Modify files
        original_file.write_text("modified_value=99\n", encoding="utf-8")
        sub_file.write_text("nested_modified\n", encoding="utf-8")

        # Add a new file
        new_file = work_dir / "new_file.txt"
        new_file.write_text("i_am_new\n", encoding="utf-8")

        # Delete a file
        sub_file.unlink()

        # Restore to checkpoint
        result = cm.restore(str(work_dir), first_hash)
        assert result["success"] is True, f"Restore failed: {result.get('error')}"

        # Assert original state is back
        assert original_file.read_text(encoding="utf-8") == "original_value=42\n"
        assert sub_file.exists(), "Deleted file should be restored"
        assert sub_file.read_text(encoding="utf-8") == "nested_original\n"
        assert not new_file.exists(), "New file created after checkpoint should be removed"

    def test_checkpoint_and_restore_single_file(self, tmp_path):
        """
        Take checkpoint, modify one file, restore only that file.
        """
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        file_a = work_dir / "a.txt"
        file_a.write_text("A_original\n", encoding="utf-8")
        file_b = work_dir / "b.txt"
        file_b.write_text("B_original\n", encoding="utf-8")

        cm = CheckpointManager(enabled=True, max_snapshots=10)
        cm.ensure_checkpoint(str(work_dir), reason="pre-modify")

        checkpoints = cm.list_checkpoints(str(work_dir))
        assert len(checkpoints) >= 1
        first_hash = checkpoints[0]["hash"]

        # Modify both files
        file_a.write_text("A_modified\n", encoding="utf-8")
        file_b.write_text("B_modified\n", encoding="utf-8")

        # Restore only file_a
        result = cm.restore(str(work_dir), first_hash, file_path="a.txt")
        assert result["success"] is True, f"Single-file restore failed: {result.get('error')}"

        assert file_a.read_text(encoding="utf-8") == "A_original\n"
        assert file_b.read_text(encoding="utf-8") == "B_modified\n"

    def test_checkpoint_disabled_does_nothing(self, tmp_path):
        """When disabled, ensure_checkpoint returns False and restore errors."""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("data", encoding="utf-8")

        cm = CheckpointManager(enabled=False)
        ok = cm.ensure_checkpoint(str(work_dir), reason="should-not-run")
        assert ok is False

        checkpoints = cm.list_checkpoints(str(work_dir))
        assert checkpoints == []

    def test_multiple_checkpoints_listed_correctly(self, tmp_path):
        """Multiple checkpoints should be listed newest first."""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("v1", encoding="utf-8")

        cm = CheckpointManager(enabled=True, max_snapshots=10)

        cm.ensure_checkpoint(str(work_dir), reason="first")
        (work_dir / "file.txt").write_text("v2", encoding="utf-8")
        cm.new_turn()
        cm.ensure_checkpoint(str(work_dir), reason="second")
        (work_dir / "file.txt").write_text("v3", encoding="utf-8")
        cm.new_turn()
        cm.ensure_checkpoint(str(work_dir), reason="third")

        checkpoints = cm.list_checkpoints(str(work_dir))
        assert len(checkpoints) >= 3, f"Expected >=3 checkpoints, got {len(checkpoints)}"

        # Most recent first
        assert checkpoints[0]["reason"] == "third"
        assert checkpoints[1]["reason"] == "second"

    def test_restore_invalid_commit_hash_fails(self, tmp_path):
        """Restoring an invalid hash should return an error."""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("data", encoding="utf-8")

        cm = CheckpointManager(enabled=True)
        cm.ensure_checkpoint(str(work_dir), reason="test")

        result = cm.restore(str(work_dir), "invalid-hash-!!!")
        assert result["success"] is False
        assert "invalid" in result.get("error", "").lower()

    def test_restore_nonexistent_checkpoint_fails(self, tmp_path):
        """Restoring a hash that does not exist in the shadow repo should fail."""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("data", encoding="utf-8")

        cm = CheckpointManager(enabled=True)
        cm.ensure_checkpoint(str(work_dir), reason="test")

        # Valid-looking hash that does not exist
        result = cm.restore(str(work_dir), "deadbeef")
        assert result["success"] is False
        assert "not found" in result.get("error", "").lower()

    def test_path_traversal_blocked(self, tmp_path):
        """Restoring a file path that escapes the working directory should fail."""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("data", encoding="utf-8")

        cm = CheckpointManager(enabled=True)
        cm.ensure_checkpoint(str(work_dir), reason="test")

        checkpoints = cm.list_checkpoints(str(work_dir))
        first_hash = checkpoints[0]["hash"]

        result = cm.restore(str(work_dir), first_hash, file_path="../escape.txt")
        assert result["success"] is False
        assert "traversal" in result.get("error", "").lower() or "relative" in result.get("error", "").lower()

    def test_diff_shows_changes(self, tmp_path):
        """diff() should report changes between checkpoint and current state."""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("original\n", encoding="utf-8")

        cm = CheckpointManager(enabled=True)
        cm.ensure_checkpoint(str(work_dir), reason="pre-change")

        (work_dir / "file.txt").write_text("modified\n", encoding="utf-8")

        checkpoints = cm.list_checkpoints(str(work_dir))
        first_hash = checkpoints[0]["hash"]

        diff_result = cm.diff(str(work_dir), first_hash)
        assert diff_result["success"] is True
        assert "modified" in diff_result.get("diff", "") or "original" in diff_result.get("diff", "")

    def test_checkpoint_skipped_when_no_changes(self, tmp_path):
        """Taking a checkpoint when nothing changed should return False."""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "file.txt").write_text("data", encoding="utf-8")

        cm = CheckpointManager(enabled=True)
        ok1 = cm.ensure_checkpoint(str(work_dir), reason="first")
        assert ok1 is True

        cm.new_turn()
        ok2 = cm.ensure_checkpoint(str(work_dir), reason="second-no-change")
        assert ok2 is False, "Expected checkpoint to be skipped when no changes"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
