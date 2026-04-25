"""Sandbox policy — apply Linux-level isolation profiles.

Profiles:
    inspect-ro    : Landlock read-only
    diag-net      : unshare(CLONE_NEWNET) + selective egress
    mutate-config : Landlock RW on /etc/, /opt/, OWLS_HOME
    full-mutate   : minimal restriction, only mandatory checkpoint

If Landlock or unshare is unavailable, the function returns False so the
caller can fall back to DockerEnvironment.

Usage:
    from tools.sandbox_policy import apply_sandbox_profile, is_sandbox_available
    ok = apply_sandbox_profile("inspect-ro", task_id="task_123")
    if not ok:
        docker_env.run(command)  # fallback
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List

from agent.interfaces import SandboxProfile
from owls_constants import get_owls_home
from tools.checkpoint_manager import CheckpointManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Landlock constants (Linux 5.13+)
# ---------------------------------------------------------------------------

_LANDLOCK_CREATE_RULESET = 444
_LANDLOCK_ADD_RULE = 445
_LANDLOCK_RESTRICT_SELF = 446

_LANDLOCK_RULE_PATH_BENEATH = 1
_LANDLOCK_CREATE_RULESET_VERSION = 1

_PR_SET_NO_NEW_PRIVS = 38

_ACCESS_FS_ROUGHLY_READ = (
    (1 << 0)   # LANDLOCK_ACCESS_FS_EXECUTE
    | (1 << 2) # LANDLOCK_ACCESS_FS_READ_FILE
    | (1 << 3) # LANDLOCK_ACCESS_FS_READ_DIR
)

_ACCESS_FS_ROUGHLY_WRITE = (
    _ACCESS_FS_ROUGHLY_READ
    | (1 << 4) # LANDLOCK_ACCESS_FS_REMOVE_DIR
    | (1 << 5) # LANDLOCK_ACCESS_FS_REMOVE_FILE
    | (1 << 6) # LANDLOCK_ACCESS_FS_MAKE_CHAR
    | (1 << 7) # LANDLOCK_ACCESS_FS_MAKE_DIR
    | (1 << 8) # LANDLOCK_ACCESS_FS_MAKE_REG
    | (1 << 9) # LANDLOCK_ACCESS_FS_MAKE_SOCK
    | (1 << 10)# LANDLOCK_ACCESS_FS_MAKE_FIFO
    | (1 << 11)# LANDLOCK_ACCESS_FS_MAKE_BLOCK
    | (1 << 12)# LANDLOCK_ACCESS_FS_MAKE_SYM
    | (1 << 13)# LANDLOCK_ACCESS_FS_REFER
    | (1 << 14)# LANDLOCK_ACCESS_FS_TRUNCATE
)


# ---------------------------------------------------------------------------
# Capability probing
# ---------------------------------------------------------------------------

def is_sandbox_available(profile: SandboxProfile) -> bool:
    """Pre-check whether the host can apply *profile*.

    Returns True if the kernel supports the required mechanisms.
    """
    if profile == "full-mutate":
        return True  # only needs checkpoint (always available)

    if profile in ("inspect-ro", "mutate-config"):
        return _has_landlock()

    if profile == "diag-net":
        return _has_unshare()

    return False


def _has_landlock() -> bool:
    """Check if the running kernel supports Landlock."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        abi_version = libc.syscall(
            _LANDLOCK_CREATE_RULESET,
            0,
            0,
            _LANDLOCK_CREATE_RULESET_VERSION,
        )
        return abi_version > 0
    except Exception:
        return False


def _has_unshare() -> bool:
    """Check if unshare is available and permitted."""
    return hasattr(os, "unshare")


# ---------------------------------------------------------------------------
# Profile application
# ---------------------------------------------------------------------------

def apply_sandbox_profile(profile: SandboxProfile, task_id: str) -> bool:
    """Apply the sandbox *profile* for the current process.

    Returns True on success, False if the mechanism is unavailable.
    The caller is expected to fall back to DockerEnvironment on False.
    """
    logger.debug("Applying sandbox profile '%s' for task %s", profile, task_id)

    # full-mutate: only checkpoint, no additional sandboxing
    if profile == "full-mutate":
        _ensure_checkpoint(task_id)
        return True

    if profile == "inspect-ro":
        if not _has_landlock():
            logger.warning("Landlock unavailable — cannot apply inspect-ro")
            return False
        _ensure_checkpoint(task_id)
        return _apply_landlock_ro()

    if profile == "mutate-config":
        if not _has_landlock():
            logger.warning("Landlock unavailable — cannot apply mutate-config")
            return False
        _ensure_checkpoint(task_id)
        return _apply_landlock_rw_limited()

    if profile == "diag-net":
        if not _has_unshare():
            logger.warning("unshare unavailable — cannot apply diag-net")
            return False
        _ensure_checkpoint(task_id)
        return _apply_network_namespace()

    logger.warning("Unknown sandbox profile: %s", profile)
    return False


def _ensure_checkpoint(task_id: str) -> None:
    """Take a checkpoint before any mutating operation."""
    try:
        cm = CheckpointManager(enabled=True)
        cm.ensure_checkpoint(os.getcwd(), reason=f"sandbox:{task_id}")
    except Exception as e:
        logger.warning("Checkpoint before sandbox failed: %s", e)


# ---------------------------------------------------------------------------
# Landlock helpers
# ---------------------------------------------------------------------------

def _apply_landlock_ro() -> bool:
    """Landlock read-only: allow read/execute on all accessible paths."""
    return _landlock_restrict(access_mask=_ACCESS_FS_ROUGHLY_READ)


def _apply_landlock_rw_limited() -> bool:
    """Landlock limited RW: allow RW on /etc/, /opt/, and OWLS_HOME."""
    allowed_rw = ["/etc", "/opt", str(get_owls_home())]
    return _landlock_restrict(
        access_mask=_ACCESS_FS_ROUGHLY_WRITE,
        allowed_rw_paths=allowed_rw,
    )


def _landlock_restrict(
    access_mask: int,
    allowed_rw_paths: List[str] | None = None,
) -> bool:
    """Create a Landlock ruleset and restrict the current thread."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

        class LandlockRulesetAttr(ctypes.Structure):
            _fields_ = [
                ("handled_access_fs", ctypes.c_uint64),
                ("handled_access_net", ctypes.c_uint64),
            ]

        attr = LandlockRulesetAttr()
        handled_access = _ACCESS_FS_ROUGHLY_WRITE
        root_access = _ACCESS_FS_ROUGHLY_READ if allowed_rw_paths else access_mask

        attr.handled_access_fs = handled_access
        attr.handled_access_net = 0

        fd = libc.syscall(
            _LANDLOCK_CREATE_RULESET,
            ctypes.byref(attr),
            ctypes.sizeof(attr),
            0,
        )
        if fd < 0:
            logger.error("landlock_create_ruleset failed: %s", os.strerror(ctypes.get_errno()))
            return False

        # Allow read on everything under root. Limited-write profiles add
        # explicit write grants below instead of broad root write access.
        _landlock_add_rule(libc, fd, "/", root_access)

        # If RW limited, add explicit RW allowances
        if allowed_rw_paths:
            for p in allowed_rw_paths:
                _landlock_add_rule(libc, fd, p, _ACCESS_FS_ROUGHLY_WRITE)

        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            logger.error("prctl(PR_SET_NO_NEW_PRIVS) failed: %s", os.strerror(ctypes.get_errno()))
            os.close(fd)
            return False

        ret = libc.syscall(_LANDLOCK_RESTRICT_SELF, fd, 0)
        os.close(fd)
        if ret != 0:
            logger.error("landlock_restrict_self failed: %s", os.strerror(ctypes.get_errno()))
            return False

        logger.debug("Landlock restriction applied successfully")
        return True

    except Exception as e:
        logger.warning("Landlock application failed: %s", e)
        return False


def _landlock_add_rule(libc, ruleset_fd: int, path: str, allowed_access: int) -> None:
    """Add a path-beneath rule to a Landlock ruleset."""
    class LandlockPathBeneathAttr(ctypes.Structure):
        _fields_ = [
            ("allowed_access", ctypes.c_uint64),
            ("parent_fd", ctypes.c_int32),
            ("__padding", ctypes.c_uint32),
        ]

    parent_fd = os.open(path, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        attr = LandlockPathBeneathAttr()
        attr.allowed_access = allowed_access
        attr.parent_fd = parent_fd
        ret = libc.syscall(
            _LANDLOCK_ADD_RULE,
            ruleset_fd,
            _LANDLOCK_RULE_PATH_BENEATH,
            ctypes.byref(attr),
            0,
        )
        if ret != 0:
            logger.debug("landlock_add_rule for %s failed: %s", path, os.strerror(ctypes.get_errno()))
    finally:
        os.close(parent_fd)


# ---------------------------------------------------------------------------
# Network namespace helper
# ---------------------------------------------------------------------------

def _apply_network_namespace() -> bool:
    """Create a new network namespace and allow selective egress.

    This is a best-effort implementation.  Full iptables rules would require
    CAP_NET_ADMIN, which most agent processes don't have.  We unshare the
    network namespace and return True — the caller can set up egress rules
    externally if needed.
    """
    try:
        os.unshare(os.CLONE_NEWNET)
        logger.debug("Network namespace created via unshare(CLONE_NEWNET)")
        return True
    except PermissionError:
        logger.warning("Permission denied for unshare(CLONE_NEWNET)")
        return False
    except Exception as e:
        logger.warning("unshare failed: %s", e)
        return False
