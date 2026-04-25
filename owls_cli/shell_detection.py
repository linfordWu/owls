"""Shared conservative shell-command detection helpers."""

import os
import re
import shlex
import shutil
from typing import TypedDict


_SHELL_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_WRAPPER_COMMANDS = {
    "sudo", "env", "command", "builtin", "time", "nohup", "nice",
    "chrt", "ionice", "stdbuf", "setsid",
}
_SHELL_BUILTINS = {
    "alias", "bg", "cd", "command", "dirs", "echo", "eval", "exec",
    "exit", "export", "false", "fg", "hash", "jobs", "kill", "logout",
    "popd", "printf", "pushd", "pwd", "read", "return", "set", "shift",
    "source", "test", "times", "trap", "true", "type", "ulimit", "umask",
    "unalias", "unset", "wait",
}
_INTERACTIVE_SHELL_COMMANDS = {
    "bash", "bpython", "emacs", "ftp", "htop", "ipython", "irb", "less",
    "man", "more", "mongo", "mysql", "nano", "node", "nmtui", "psql",
    "python", "python3", "screen", "sftp", "sh", "ssh", "sqlite3", "tmux",
    "top", "vi", "view", "vim", "watch", "zsh",
}
_DIRECT_COMMAND_NL_MARKERS = (
    "请", "帮我", "请问", "能否", "可以", "怎么", "为什么", "是什么",
    "what", "why", "how", "explain", "tell me", "can you", "could you",
    "please", "help me",
)

_SSH_INTENT_MARKERS = (
    "远程", "连接", "登录", "登陆", "连到", "连上",
    "connect", "login", "log in", "remote",
)
_SSH_SKIP_WORDS = {
    "ssh", "远程", "连接", "登录", "登陆", "到", "至", "主机", "服务器",
    "connect", "login", "remote", "server", "host",
}
_SSH_HOST_RE = re.compile(
    r"(?:(?P<user>[A-Za-z_][A-Za-z0-9_.-]{0,63})@)?"
    r"(?P<host>"
    r"(?:\d{1,3}\.){3}\d{1,3}"
    r"|"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r")"
    r"(?::(?P<port>\d{1,5}))?"
)
_SSH_PORT_RE = re.compile(r"(?:端口|port)\s*[:：]?\s*(\d{1,5})", re.IGNORECASE)


class SSHConnectRequest(TypedDict, total=False):
    """Parsed interactive SSH request."""

    target: str
    host: str
    user: str
    port: int


def extract_shell_command_token(parts: list[str]) -> str:
    """Return the executable/builtin token from a shell-like argv list."""
    wrapper_context = False
    for token in parts:
        if not token:
            continue
        if token in _SHELL_WRAPPER_COMMANDS:
            wrapper_context = True
            continue
        if wrapper_context and token.startswith("-"):
            continue
        if _SHELL_ENV_ASSIGNMENT_RE.match(token):
            continue
        return token
    return ""


def is_known_shell_command_token(token: str) -> bool:
    """Return True when *token* is a plausible shell command or builtin."""
    if not token:
        return False
    if token in _SHELL_BUILTINS:
        return True
    if token.startswith(("./", "../", "/")):
        return True
    return shutil.which(token) is not None


def looks_like_natural_language_shell_request(text: str) -> bool:
    """Reject shell-looking text that is probably still natural language."""
    lowered = text.lower()
    if lowered.endswith(("?", "？")):
        return True
    return any(marker in lowered for marker in _DIRECT_COMMAND_NL_MARKERS)


def looks_like_direct_shell_command(text: str) -> bool:
    """Conservatively detect one-shot shell commands that can bypass the LLM."""
    if not isinstance(text, str):
        return False

    stripped = text.strip()
    if not stripped or stripped.startswith("/") or "\n" in stripped or len(stripped) > 300:
        return False
    if looks_like_natural_language_shell_request(stripped):
        return False

    try:
        parts = shlex.split(stripped, posix=True)
    except ValueError:
        return False

    command_token = extract_shell_command_token(parts)
    if not is_known_shell_command_token(command_token):
        return False

    command_name = os.path.basename(command_token)
    return command_name not in _INTERACTIVE_SHELL_COMMANDS


def _valid_ssh_port(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError:
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _valid_ssh_host(host: str) -> bool:
    if not host or host.lower() in _SSH_SKIP_WORDS:
        return False
    if len(host) > 253:
        return False
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", host):
        return all(0 <= int(part) <= 255 for part in host.split("."))
    return bool(re.fullmatch(
        r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*",
        host,
    ))


def _request_from_match(match: re.Match, port: int | None = None) -> SSHConnectRequest | None:
    host = match.group("host")
    if not _valid_ssh_host(host):
        return None
    user = match.group("user") or ""
    resolved_port = port or _valid_ssh_port(match.group("port"))
    target = f"{user}@{host}" if user else host
    request: SSHConnectRequest = {"target": target, "host": host}
    if user:
        request["user"] = user
    if resolved_port:
        request["port"] = resolved_port
    return request


def parse_interactive_ssh_request(text: str) -> SSHConnectRequest | None:
    """Parse direct or natural-language requests for an interactive SSH session.

    Examples accepted:
      - ``ssh root@192.168.0.37``
      - ``ssh -p 2222 192.168.0.37``
      - ``ssh 远程连接192.168.0.37``
      - ``ssh 远程登录 root@192.168.0.37 端口 2222``
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped or "\n" in stripped or len(stripped) > 300:
        return None

    lowered = stripped.lower()
    if "ssh" not in lowered:
        return None

    port = None
    port_match = _SSH_PORT_RE.search(stripped)
    if port_match:
        port = _valid_ssh_port(port_match.group(1))

    try:
        parts = shlex.split(stripped, posix=True)
    except ValueError:
        parts = []

    if parts and parts[0] == "ssh":
        idx = 1
        while idx < len(parts):
            token = parts[idx]
            if token == "-p" and idx + 1 < len(parts):
                port = _valid_ssh_port(parts[idx + 1]) or port
                idx += 2
                continue
            if token.startswith("-p") and len(token) > 2:
                port = _valid_ssh_port(token[2:]) or port
                idx += 1
                continue
            if token.startswith("-"):
                idx += 1
                continue
            if token.lower() in _SSH_SKIP_WORDS:
                idx += 1
                continue
            match = _SSH_HOST_RE.fullmatch(token) or _SSH_HOST_RE.search(token)
            if match:
                request = _request_from_match(match, port=port)
                if request:
                    return request
            idx += 1

    if not any(marker in lowered for marker in _SSH_INTENT_MARKERS):
        return None

    for match in _SSH_HOST_RE.finditer(stripped):
        request = _request_from_match(match, port=port)
        if request:
            return request
    return None
