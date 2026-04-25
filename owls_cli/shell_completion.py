"""Shell-style completion helpers for the interactive CLI.

This module keeps command-aware completion logic separate from the slash
command registry so the CLI can grow richer shell completions without making
``owls_cli.commands`` harder to maintain.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass

try:
    from prompt_toolkit.completion import Completion
except ImportError:  # pragma: no cover
    Completion = None  # type: ignore[assignment]


def _file_size_label(path: str) -> str:
    try:
        size = os.path.getsize(path)
    except OSError:
        return "file"

    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


@dataclass(frozen=True)
class ShellCompletionContext:
    command_name: str | None
    word: str
    args_before_current: tuple[str, ...]


@dataclass(frozen=True)
class CommandCompletionProfile:
    options: tuple[str, ...] = ()
    subcommands: tuple[str, ...] = ()
    subcommand_meta: str = "subcommand"
    complete_paths: bool = False
    directories_only: bool = False
    path_after_nonoption_args: int | None = None
    complete_commands: bool = False
    complete_env_vars: bool = False
    options_on_empty: bool = True


class ShellCompletionEngine:
    """Command-aware shell completion for non-slash CLI input."""

    _SHELL_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    _SHELL_WRAPPER_COMMANDS = frozenset({
        "sudo", "env", "command", "builtin", "time", "nohup", "nice",
        "chrt", "ionice", "stdbuf", "setsid",
    })
    _SHELL_BUILTINS = frozenset({
        "alias", "bg", "cd", "command", "dirs", "echo", "eval", "exec",
        "exit", "export", "false", "fg", "hash", "help", "jobs", "kill",
        "logout", "popd", "printf", "pushd", "pwd", "read", "return", "set",
        "shift", "source", "test", "times", "trap", "true", "type", "ulimit",
        "umask", "unalias", "unset", "wait",
    })
    _GIT_FALLBACK_SUBCOMMANDS = frozenset({
        "add", "am", "apply", "archive", "bisect", "blame", "branch", "bundle",
        "checkout", "cherry-pick", "clean", "clone", "commit", "describe", "diff",
        "fetch", "format-patch", "grep", "init", "log", "merge", "mv", "pull",
        "push", "rebase", "remote", "reset", "restore", "revert", "rm", "show",
        "stash", "status", "submodule", "switch", "tag", "worktree",
    })
    _SYSTEMCTL_SUBCOMMANDS = (
        "cat", "daemon-reexec", "daemon-reload", "disable", "edit", "enable",
        "is-active", "is-enabled", "list-unit-files", "list-units", "mask",
        "reload", "restart", "show", "start", "status", "stop", "unmask",
    )
    _DOCKER_SUBCOMMANDS = (
        "attach", "build", "compose", "cp", "exec", "images", "inspect", "logs",
        "network", "port", "ps", "pull", "push", "restart", "rm", "rmi", "run",
        "start", "stats", "stop", "system", "tag", "top", "volume",
    )
    _DOCKER_COMPOSE_SUBCOMMANDS = (
        "build", "config", "cp", "down", "exec", "logs", "ls", "ps", "pull",
        "restart", "rm", "run", "start", "stop", "top", "up",
    )
    _TMUX_SUBCOMMANDS = (
        "attach-session", "detach-client", "kill-pane", "kill-server", "kill-session",
        "list-clients", "list-panes", "list-sessions", "list-windows", "new-session",
        "new-window", "rename-session", "rename-window", "select-pane",
        "select-window", "split-window", "switch-client",
    )
    _OPTIONS = {
        "arch": ("--help", "--version"),
        "base32": ("-d", "-i", "-w", "--decode", "--ignore-garbage", "--wrap"),
        "base64": ("-d", "-i", "-w", "--decode", "--ignore-garbage", "--wrap"),
        "basename": ("-a", "-s", "-z", "--multiple", "--suffix", "--zero"),
        "basenc": ("--base16", "--base32", "--base32hex", "--base64", "--base64url", "--decode"),
        "bc": ("-h", "-i", "-l", "-q", "-s", "-w", "--help", "--interactive", "--mathlib", "--quiet", "--standard", "--warn"),
        "clear": ("-T", "-V", "-x"),
        "command": ("-p", "-v", "-V"),
        "curl": ("-I", "-L", "-O", "-X", "-d", "-f", "-H", "-o", "-s", "-u", "--data", "--fail", "--header", "--location", "--output", "--request", "--silent", "--user"),
        "cut": ("-b", "-c", "-d", "-f", "-s", "--complement", "--delimiter", "--fields", "--only-delimited"),
        "date": ("-d", "-I", "-R", "-r", "-s", "-u", "--date", "--iso-8601", "--reference", "--rfc-email", "--set", "--utc"),
        "dd": ("bs=", "conv=", "count=", "if=", "iflag=", "obs=", "of=", "oflag=", "seek=", "skip=", "status=progress"),
        "df": ("-a", "-h", "-H", "-i", "-T", "--all", "--human-readable", "--inodes", "--print-type"),
        "diff": ("-N", "-q", "-r", "-u", "--brief", "--new-file", "--recursive", "--unified"),
        "dig": ("+answer", "+noall", "+short", "+tcp", "+trace", "@"),
        "dirname": ("-z", "--zero"),
        "du": ("-a", "-c", "-d", "-h", "-s", "--all", "--human-readable", "--max-depth", "--summarize"),
        "echo": ("-e", "-E", "-n"),
        "emacs": ("-Q", "-nw", "--daemon", "--debug-init", "--load"),
        "exec": ("-a", "-c", "-l"),
        "export": ("-f", "-n", "-p"),
        "file": ("-b", "-i", "-L", "-z", "--brief", "--dereference", "--mime", "--uncompress"),
        "grep": (
            "-E", "-F", "-H", "-R", "-i", "-n", "-r", "-v", "-w",
            "--color", "--exclude", "--include", "--ignore-case",
            "--line-number", "--recursive",
        ),
        "head": ("-c", "-n", "-q", "-v", "--bytes", "--lines", "--quiet", "--verbose"),
        "hostname": ("-A", "-d", "-f", "-F", "-i", "-I", "-s"),
        "htop": ("-C", "-d", "-p", "-u", "--help"),
        "id": ("-G", "-P", "-Z", "-g", "-n", "-r", "-u", "-z", "--group", "--groups", "--name", "--real", "--user", "--zero"),
        "install": ("-D", "-d", "-g", "-m", "-o", "-t", "-v", "--directory", "--mode", "--owner", "--target-directory", "--verbose"),
        "join": ("-1", "-2", "-a", "-e", "-i", "-j", "-o", "-t", "-v"),
        "kill": ("-9", "-15", "-L", "-l", "-s", "--list", "--signal", "--table"),
        "killall": ("-e", "-g", "-i", "-q", "-r", "-s", "-u", "-v", "-w"),
        "less": ("-F", "-I", "-N", "-R", "-S", "-X", "-f", "-i", "-n"),
        "ln": ("-T", "-f", "-n", "-s", "-t", "-v", "--force", "--no-dereference", "--symbolic", "--target-directory", "--verbose"),
        "man": ("-a", "-f", "-k", "-P", "-S", "-w", "--all", "--apropos", "--path", "--where"),
        "micro": ("-config-dir", "-debug", "-options", "-plugin"),
        "mkfifo": ("-m", "-Z", "--context", "--mode"),
        "mknod": ("-m", "-Z", "--context", "--mode"),
        "more": ("-d", "-f", "-l", "-p", "-s", "-u"),
        "nano": ("-B", "-E", "-l", "-m", "-R", "-w", "--backup", "--multibuffer", "--restricted", "--softwrap"),
        "nc": ("-k", "-l", "-u", "-v", "-w", "-z"),
        "nl": ("-b", "-d", "-f", "-h", "-i", "-n", "-s", "-v", "-w"),
        "nvim": ("-R", "-c", "-d", "-u", "--cmd", "--headless", "--listen"),
        "od": ("-A", "-N", "-S", "-a", "-b", "-c", "-d", "-j", "-o", "-t", "-v", "-x"),
        "paste": ("-d", "-s", "--delimiters", "--serial"),
        "pathchk": ("-p", "-P", "--portability"),
        "ping": ("-4", "-6", "-c", "-i", "-s", "-t", "-W"),
        "ps": ("-A", "-e", "-f", "-p", "-u", "aux", "--forest", "--sort"),
        "pwd": ("-L", "-P", "--logical", "--physical"),
        "readlink": ("-e", "-f", "-m", "-n", "-q", "-s", "-v", "--canonicalize", "--no-newline", "--quiet", "--silent", "--verbose"),
        "rmdir": ("-p", "-v", "--ignore-fail-on-non-empty", "--parents", "--verbose"),
        "scp": ("-3", "-B", "-C", "-i", "-J", "-l", "-P", "-p", "-q", "-r", "-v"),
        "screen": ("-D", "-R", "-S", "-X", "-c", "-d", "-h", "-list", "-ls", "-m", "-r"),
        "sed": ("-E", "-e", "-f", "-i", "-n", "-r", "--expression", "--file", "--in-place", "--quiet", "--regexp-extended", "--silent"),
        "sftp": ("-b", "-C", "-F", "-i", "-J", "-P", "-q", "-r", "-v"),
        "shred": ("-f", "-n", "-s", "-u", "-v", "-x", "--iterations", "--remove", "--size", "--verbose", "--zero"),
        "source": (),
        "split": ("-C", "-a", "-b", "-d", "-l", "-n", "-t", "--bytes", "--line-bytes", "--numeric-suffixes", "--number"),
        "ssh": ("-A", "-D", "-F", "-J", "-L", "-N", "-R", "-T", "-X", "-Y", "-i", "-l", "-p", "-v"),
        "stat": ("-L", "-c", "-f", "--format", "--printf", "--terse"),
        "su": ("-", "-c", "-l", "-m", "-p", "-s"),
        "sudo": ("-A", "-E", "-H", "-K", "-S", "-b", "-e", "-i", "-k", "-l", "-n", "-s", "-u", "-v"),
        "sysctl": ("-A", "-N", "-a", "-n", "-p", "-q", "-w", "--system"),
        "tac": ("-b", "-r", "-s", "--before", "--regex", "--separator"),
        "tail": ("-F", "-c", "-f", "-n", "--bytes", "--follow", "--lines"),
        "tee": ("-a", "-i", "--append", "--ignore-interrupts"),
        "time": ("-p",),
        "tmux": ("-2", "-C", "-L", "-S", "-V", "-f", "-u", "-v"),
        "top": ("-H", "-b", "-c", "-d", "-n", "-p", "-u", "-w"),
        "tr": ("-c", "-C", "-d", "-s", "-t", "--complement", "--delete", "--squeeze-repeats", "--truncate-set1"),
        "traceroute": ("-4", "-6", "-I", "-T", "-U", "-m", "-n", "-p", "-q", "-w"),
        "truncate": ("-c", "-o", "-r", "-s", "--io-blocks", "--no-create", "--reference", "--size"),
        "uname": ("-a", "-m", "-n", "-o", "-p", "-r", "-s", "-v"),
        "uniq": ("-c", "-d", "-f", "-i", "-s", "-u", "-w", "--all-repeated", "--count", "--ignore-case"),
        "unzip": ("-d", "-l", "-o", "-p", "-q", "-t", "-x"),
        "vi": ("-R", "-c", "-o", "-O", "-u", "-V"),
        "vim": ("-R", "-c", "-o", "-O", "-p", "-u", "-V"),
        "w": ("-h", "-i", "-f", "-s", "-u"),
        "wc": ("-L", "-c", "-l", "-m", "-w", "--bytes", "--chars", "--lines", "--max-line-length", "--words"),
        "wget": ("-O", "-P", "-c", "-q", "-r", "-N", "--continue", "--directory-prefix", "--mirror", "--output-document"),
        "which": ("-a", "-s"),
        "who": ("-a", "-b", "-H", "-m", "-q", "-r", "-u"),
        "whois": ("-H", "-I", "-l", "-m", "-p", "-r", "-R"),
        "xargs": ("-0", "-I", "-L", "-P", "-a", "-d", "-n", "-o", "-p", "-r", "-t", "--arg-file", "--delimiter", "--interactive", "--max-args", "--max-lines", "--max-procs", "--open-tty", "--replace", "--verbose"),
        "zip": ("-d", "-j", "-m", "-q", "-r", "-u", "-v", "-x"),
        "git": (
            "-C", "-c", "-p", "--help", "--no-pager", "--paginate", "--version",
        ),
        "ls": (
            "-1", "-A", "-F", "-R", "-S", "-a", "-d", "-h", "-l", "-r", "-t",
            "--all", "--almost-all", "--classify", "--color", "--directory",
            "--human-readable", "--reverse", "--sort",
        ),
        "cd": ("-L", "-P", "-e"),
        "pushd": ("-n",),
        "rm": (
            "-I", "-R", "-d", "-f", "-i", "-r", "-v",
            "--dir", "--force", "--interactive", "--recursive", "--verbose",
        ),
        "cp": (
            "-R", "-a", "-f", "-i", "-n", "-p", "-r", "-v",
            "--archive", "--force", "--interactive", "--no-clobber", "--parents",
            "--recursive", "--target-directory", "--verbose",
        ),
        "mv": (
            "-T", "-f", "-i", "-n", "-t", "-u", "-v",
            "--backup", "--force", "--interactive", "--no-clobber",
            "--target-directory", "--update", "--verbose",
        ),
        "cat": (
            "-A", "-E", "-T", "-b", "-n", "-s", "-v",
            "--number", "--show-all", "--show-ends", "--show-tabs", "--squeeze-blank",
        ),
        "grep": (
            "-E", "-F", "-H", "-R", "-i", "-n", "-r", "-v", "-w",
            "--color", "--exclude", "--include", "--ignore-case",
            "--line-number", "--recursive",
        ),
        "find": (
            "-L", "-P", "-delete", "-maxdepth", "-mindepth", "-mtime", "-name",
            "-print", "-size", "-type",
        ),
        "mkdir": ("-m", "-p", "-v", "--mode", "--parents", "--verbose"),
        "touch": ("-a", "-c", "-d", "-m", "-r", "-t", "--date", "--no-create", "--reference"),
        "chmod": ("-R", "-c", "-f", "-v", "--changes", "--recursive", "--silent", "--verbose"),
        "chown": ("-R", "-c", "-f", "-h", "-v", "--from", "--no-dereference", "--recursive"),
        "tar": (
            "-c", "-f", "-j", "-J", "-t", "-v", "-x", "-z",
            "--bzip2", "--create", "--extract", "--file", "--gzip", "--list",
            "--verbose", "--xz",
        ),
        "systemctl": ("--all", "--failed", "--no-pager", "--now", "--state", "--system", "--type", "--user"),
        "docker": ("-D", "-H", "--config", "--debug", "--tls", "--tlscacert", "--tlsverify"),
    }
    _GIT_SUBCOMMAND_OPTIONS = {
        "add": ("-A", "-N", "-f", "-i", "-n", "-p", "-u", "--all", "--dry-run", "--intent-to-add", "--patch", "--update"),
        "branch": ("-a", "-d", "-D", "-m", "-M", "-r", "-v", "--all", "--delete", "--move", "--remotes", "--verbose"),
        "checkout": ("-b", "-B", "--detach", "--force", "--ours", "--theirs", "--track"),
        "restore": ("--source", "--staged", "--worktree", "--patch"),
        "status": ("-b", "-s", "-u", "--branch", "--ignored", "--porcelain", "--short", "--untracked-files"),
        "switch": ("-c", "-C", "--detach", "--discard-changes", "--force-create", "--guess", "--track"),
    }
    _GIT_PATH_SUBCOMMANDS = frozenset({
        "add", "checkout", "clean", "diff", "grep", "log", "mv", "reset",
        "restore", "rm", "show", "stash",
    })
    _COMMAND_ARGUMENT_COMMANDS = frozenset({"command", "exec", "man", "which"})
    _ENV_VAR_COMMANDS = frozenset({"export"})
    _FILE_PATH_COMMANDS = frozenset({
        "basename", "cat", "cp", "cut", "diff", "dirname", "emacs", "file",
        "head", "install", "join", "less", "ln", "micro", "mkfifo", "mknod",
        "more", "mv", "nano", "nl", "nvim", "od", "paste", "pathchk",
        "readlink", "rm", "scp", "sed", "sftp", "shred", "source", "split",
        "stat", "tac", "tail", "tar", "touch", "unzip", "vi", "vim", "wc",
        "zip",
    })
    _DIRECTORY_PATH_COMMANDS = frozenset({"cd", "find", "mkdir", "pushd"})
    _PATH_AFTER_FIRST_ARG_COMMANDS = frozenset({"chmod", "chown", "grep"})
    _SUBCOMMAND_COMMANDS = {
        "tmux": CommandCompletionProfile(
            options=_OPTIONS["tmux"],
            subcommands=_TMUX_SUBCOMMANDS,
            subcommand_meta="tmux subcommand",
        ),
    }
    _GENERIC_COMMAND_PROFILES = {
        "arch": CommandCompletionProfile(options=_OPTIONS["arch"]),
        "base32": CommandCompletionProfile(options=_OPTIONS["base32"]),
        "base64": CommandCompletionProfile(options=_OPTIONS["base64"]),
        "basename": CommandCompletionProfile(options=_OPTIONS["basename"], complete_paths=True, path_after_nonoption_args=0),
        "basenc": CommandCompletionProfile(options=_OPTIONS["basenc"]),
        "bc": CommandCompletionProfile(options=_OPTIONS["bc"]),
        "clear": CommandCompletionProfile(options=_OPTIONS["clear"]),
        "command": CommandCompletionProfile(options=_OPTIONS["command"], complete_commands=True),
        "curl": CommandCompletionProfile(options=_OPTIONS["curl"]),
        "cut": CommandCompletionProfile(options=_OPTIONS["cut"], complete_paths=True, path_after_nonoption_args=0),
        "date": CommandCompletionProfile(options=_OPTIONS["date"]),
        "dd": CommandCompletionProfile(options=_OPTIONS["dd"]),
        "df": CommandCompletionProfile(options=_OPTIONS["df"], complete_paths=True, path_after_nonoption_args=0),
        "diff": CommandCompletionProfile(options=_OPTIONS["diff"], complete_paths=True, path_after_nonoption_args=0),
        "dig": CommandCompletionProfile(options=_OPTIONS["dig"]),
        "dirname": CommandCompletionProfile(options=_OPTIONS["dirname"], complete_paths=True, path_after_nonoption_args=0),
        "du": CommandCompletionProfile(options=_OPTIONS["du"], complete_paths=True, path_after_nonoption_args=0),
        "echo": CommandCompletionProfile(options=_OPTIONS["echo"]),
        "emacs": CommandCompletionProfile(options=_OPTIONS["emacs"], complete_paths=True, path_after_nonoption_args=0),
        "exec": CommandCompletionProfile(options=_OPTIONS["exec"], complete_commands=True),
        "export": CommandCompletionProfile(options=_OPTIONS["export"], complete_env_vars=True),
        "file": CommandCompletionProfile(options=_OPTIONS["file"], complete_paths=True, path_after_nonoption_args=0),
        "head": CommandCompletionProfile(options=_OPTIONS["head"], complete_paths=True, path_after_nonoption_args=0),
        "hostname": CommandCompletionProfile(options=_OPTIONS["hostname"]),
        "htop": CommandCompletionProfile(options=_OPTIONS["htop"]),
        "id": CommandCompletionProfile(options=_OPTIONS["id"]),
        "install": CommandCompletionProfile(options=_OPTIONS["install"], complete_paths=True, path_after_nonoption_args=0),
        "join": CommandCompletionProfile(options=_OPTIONS["join"], complete_paths=True, path_after_nonoption_args=0),
        "kill": CommandCompletionProfile(options=_OPTIONS["kill"]),
        "killall": CommandCompletionProfile(options=_OPTIONS["killall"]),
        "less": CommandCompletionProfile(options=_OPTIONS["less"], complete_paths=True, path_after_nonoption_args=0),
        "ln": CommandCompletionProfile(options=_OPTIONS["ln"], complete_paths=True, path_after_nonoption_args=0),
        "man": CommandCompletionProfile(options=_OPTIONS["man"], complete_commands=True),
        "micro": CommandCompletionProfile(options=_OPTIONS["micro"], complete_paths=True, path_after_nonoption_args=0),
        "mkfifo": CommandCompletionProfile(options=_OPTIONS["mkfifo"], complete_paths=True, path_after_nonoption_args=0),
        "mknod": CommandCompletionProfile(options=_OPTIONS["mknod"], complete_paths=True, path_after_nonoption_args=0),
        "more": CommandCompletionProfile(options=_OPTIONS["more"], complete_paths=True, path_after_nonoption_args=0),
        "nano": CommandCompletionProfile(options=_OPTIONS["nano"], complete_paths=True, path_after_nonoption_args=0),
        "nc": CommandCompletionProfile(options=_OPTIONS["nc"]),
        "nl": CommandCompletionProfile(options=_OPTIONS["nl"], complete_paths=True, path_after_nonoption_args=0),
        "nvim": CommandCompletionProfile(options=_OPTIONS["nvim"], complete_paths=True, path_after_nonoption_args=0),
        "od": CommandCompletionProfile(options=_OPTIONS["od"], complete_paths=True, path_after_nonoption_args=0),
        "paste": CommandCompletionProfile(options=_OPTIONS["paste"], complete_paths=True, path_after_nonoption_args=0),
        "pathchk": CommandCompletionProfile(options=_OPTIONS["pathchk"], complete_paths=True, path_after_nonoption_args=0),
        "ping": CommandCompletionProfile(options=_OPTIONS["ping"]),
        "ps": CommandCompletionProfile(options=_OPTIONS["ps"]),
        "pwd": CommandCompletionProfile(options=_OPTIONS["pwd"]),
        "readlink": CommandCompletionProfile(options=_OPTIONS["readlink"], complete_paths=True, path_after_nonoption_args=0),
        "rmdir": CommandCompletionProfile(options=_OPTIONS["rmdir"], complete_paths=True, directories_only=True, path_after_nonoption_args=0),
        "scp": CommandCompletionProfile(options=_OPTIONS["scp"], complete_paths=True, path_after_nonoption_args=0),
        "screen": CommandCompletionProfile(options=_OPTIONS["screen"]),
        "sed": CommandCompletionProfile(options=_OPTIONS["sed"], complete_paths=True, path_after_nonoption_args=0),
        "sftp": CommandCompletionProfile(options=_OPTIONS["sftp"], complete_paths=True, path_after_nonoption_args=0),
        "shred": CommandCompletionProfile(options=_OPTIONS["shred"], complete_paths=True, path_after_nonoption_args=0),
        "source": CommandCompletionProfile(complete_paths=True, path_after_nonoption_args=0),
        "split": CommandCompletionProfile(options=_OPTIONS["split"], complete_paths=True, path_after_nonoption_args=0),
        "stat": CommandCompletionProfile(options=_OPTIONS["stat"], complete_paths=True, path_after_nonoption_args=0),
        "su": CommandCompletionProfile(options=_OPTIONS["su"]),
        "sudo": CommandCompletionProfile(options=_OPTIONS["sudo"], complete_commands=True),
        "sysctl": CommandCompletionProfile(options=_OPTIONS["sysctl"]),
        "tac": CommandCompletionProfile(options=_OPTIONS["tac"], complete_paths=True, path_after_nonoption_args=0),
        "tail": CommandCompletionProfile(options=_OPTIONS["tail"], complete_paths=True, path_after_nonoption_args=0),
        "tee": CommandCompletionProfile(options=_OPTIONS["tee"]),
        "time": CommandCompletionProfile(options=_OPTIONS["time"], complete_commands=True),
        "tmux": _SUBCOMMAND_COMMANDS["tmux"],
        "top": CommandCompletionProfile(options=_OPTIONS["top"]),
        "tr": CommandCompletionProfile(options=_OPTIONS["tr"]),
        "traceroute": CommandCompletionProfile(options=_OPTIONS["traceroute"]),
        "truncate": CommandCompletionProfile(options=_OPTIONS["truncate"], complete_paths=True, path_after_nonoption_args=0),
        "uname": CommandCompletionProfile(options=_OPTIONS["uname"]),
        "uniq": CommandCompletionProfile(options=_OPTIONS["uniq"]),
        "unzip": CommandCompletionProfile(options=_OPTIONS["unzip"], complete_paths=True, path_after_nonoption_args=0),
        "vi": CommandCompletionProfile(options=_OPTIONS["vi"], complete_paths=True, path_after_nonoption_args=0),
        "vim": CommandCompletionProfile(options=_OPTIONS["vim"], complete_paths=True, path_after_nonoption_args=0),
        "w": CommandCompletionProfile(options=_OPTIONS["w"]),
        "wc": CommandCompletionProfile(options=_OPTIONS["wc"], complete_paths=True, path_after_nonoption_args=0),
        "wget": CommandCompletionProfile(options=_OPTIONS["wget"]),
        "which": CommandCompletionProfile(options=_OPTIONS["which"], complete_commands=True),
        "who": CommandCompletionProfile(options=_OPTIONS["who"]),
        "whois": CommandCompletionProfile(options=_OPTIONS["whois"]),
        "xargs": CommandCompletionProfile(options=_OPTIONS["xargs"], complete_commands=True),
        "zip": CommandCompletionProfile(options=_OPTIONS["zip"], complete_paths=True, path_after_nonoption_args=0),
    }
    _PATH_COMMANDS = frozenset({"ls", "rm", "cp", "mv", "cat", "mkdir", "touch", "tar"})
    _DIRECTORY_COMMANDS = frozenset({"cd", "pushd", "find"})
    _PATH_AFTER_FIRST_POSITION = frozenset({"chmod", "chown", "grep"})

    @classmethod
    def supports_command(cls, command_name: str) -> bool:
        return (
            command_name in cls._GENERIC_COMMAND_PROFILES
            or command_name in cls._SHELL_WRAPPER_COMMANDS
            or hasattr(cls, f"_complete_{command_name.replace('-', '_')}")
        )

    def __init__(self) -> None:
        self._shell_command_cache: list[str] = []
        self._shell_command_cache_time: float = 0.0
        self._shell_command_cache_path: str = ""
        self._git_subcommand_cache: list[str] = []
        self._git_subcommand_cache_time: float = 0.0

    @staticmethod
    def _extract_current_word(text: str) -> tuple[str, str]:
        if not text:
            return "", ""
        index = len(text) - 1
        while index >= 0 and text[index] != " ":
            index -= 1
        return text[: index + 1], text[index + 1 :]

    @classmethod
    def get_context(cls, text: str) -> ShellCompletionContext | None:
        prefix_text, word = cls._extract_current_word(text)
        if word.startswith(("@", "/")):
            return None

        stripped = prefix_text.rstrip()
        if not stripped:
            return ShellCompletionContext(command_name=None, word=word, args_before_current=())

        try:
            parts = shlex.split(stripped, posix=True)
        except ValueError:
            return None

        command_name: str | None = None
        args_before_current: list[str] = []
        for token in parts:
            if not token:
                continue
            if command_name is None:
                if token in cls._SHELL_WRAPPER_COMMANDS:
                    continue
                if cls._SHELL_ENV_ASSIGNMENT_RE.match(token):
                    continue
                command_name = token
                continue
            args_before_current.append(token)

        return ShellCompletionContext(
            command_name=command_name,
            word=word,
            args_before_current=tuple(args_before_current),
        )

    def _get_shell_command_names(self) -> list[str]:
        path_value = os.getenv("PATH", "")
        now = time.monotonic()
        if (
            self._shell_command_cache
            and self._shell_command_cache_path == path_value
            and now - self._shell_command_cache_time < 30.0
        ):
            return self._shell_command_cache

        commands: set[str] = set(self._SHELL_BUILTINS)
        for directory in path_value.split(os.pathsep):
            if not directory:
                continue
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        try:
                            if not entry.is_file():
                                continue
                        except OSError:
                            continue
                        if os.access(entry.path, os.X_OK):
                            commands.add(entry.name)
            except OSError:
                continue

        result = sorted(commands)
        self._shell_command_cache = result
        self._shell_command_cache_time = now
        self._shell_command_cache_path = path_value
        return result

    def _get_git_subcommand_names(self) -> list[str]:
        now = time.monotonic()
        if self._git_subcommand_cache and now - self._git_subcommand_cache_time < 30.0:
            return self._git_subcommand_cache

        subcommands: set[str] = set(self._GIT_FALLBACK_SUBCOMMANDS)
        if shutil.which("git"):
            try:
                proc = subprocess.run(
                    ["git", "help", "-a"],
                    capture_output=True,
                    text=True,
                    timeout=1.5,
                )
                output = f"{proc.stdout}\n{proc.stderr}"
                for line in output.splitlines():
                    match = re.match(r"^\s{2,}([a-z0-9][a-z0-9-]*)(?:\s|$)", line)
                    if match:
                        subcommands.add(match.group(1))
            except (subprocess.TimeoutExpired, OSError):
                pass

        result = sorted(subcommands)
        self._git_subcommand_cache = result
        self._git_subcommand_cache_time = now
        return result

    def get_completions(self, text: str, limit: int = 80):
        if Completion is None:
            return

        context = self.get_context(text)
        if context is None:
            return

        if context.command_name is None:
            yield from self._complete_command_name(context.word, limit)
            return

        yield from self._complete_for_command(context, limit)

    def _complete_command_name(self, word: str, limit: int):
        yield from self._complete_words(
            self._get_shell_command_names(),
            word,
            meta="command",
            limit=limit,
        )

    def _complete_for_command(self, context: ShellCompletionContext, limit: int):
        command_name = context.command_name or ""
        handler = getattr(self, f"_complete_{command_name.replace('-', '_')}", None)
        if handler is None:
            profile = self._GENERIC_COMMAND_PROFILES.get(command_name)
            if profile is None:
                return
            yield from self._complete_generic_command(command_name, context, profile, limit)
            return
        yield from handler(context, limit)

    @staticmethod
    def _non_option_args(context: ShellCompletionContext) -> list[str]:
        return [arg for arg in context.args_before_current if not arg.startswith("-")]

    @staticmethod
    def _complete_words(candidates, word: str, meta: str, limit: int):
        word_lower = word.lower()
        count = 0
        for candidate in sorted(candidates):
            candidate_lower = candidate.lower()
            if word_lower and not candidate_lower.startswith(word_lower):
                continue
            completion_text = f"{candidate} " if word_lower and candidate_lower == word_lower else candidate
            yield Completion(
                completion_text,
                start_position=-len(word),
                display=candidate,
                display_meta=meta,
            )
            count += 1
            if count >= limit:
                break

    def _option_completions(self, command_name: str, word: str, limit: int, include_on_empty: bool = False):
        if word and not word.startswith("-"):
            return
        if not word and not include_on_empty:
            return
        options = self._OPTIONS.get(command_name, ())
        yield from self._complete_words(options, word, meta=f"{command_name} option", limit=limit)

    @staticmethod
    def _env_var_completions(word: str, limit: int = 80):
        prefix = word
        if "=" in prefix:
            prefix = prefix.split("=", 1)[0]
        prefix_lower = prefix.lower()
        count = 0
        for name in sorted(os.environ):
            if prefix_lower and not name.lower().startswith(prefix_lower):
                continue
            yield Completion(
                f"{name}=",
                start_position=-len(word),
                display=name,
                display_meta="env var",
            )
            count += 1
            if count >= limit:
                break

    def _command_name_argument_completions(self, word: str, limit: int):
        yield from self._complete_words(
            self._get_shell_command_names(),
            word,
            meta="command",
            limit=limit,
        )

    def _complete_generic_command(
        self,
        command_name: str,
        context: ShellCompletionContext,
        profile: CommandCompletionProfile,
        limit: int,
    ):
        non_option_args = self._non_option_args(context)
        wants_non_option_word = not context.word.startswith("-")
        path_iter = None
        command_iter = None
        env_iter = None
        subcommand_iter = None

        if wants_non_option_word and profile.subcommands and len(non_option_args) == 0:
            subcommand_iter = self._complete_words(
                profile.subcommands,
                context.word,
                meta=profile.subcommand_meta,
                limit=limit,
            )

        if wants_non_option_word and profile.complete_commands and len(non_option_args) == 0:
            command_iter = self._command_name_argument_completions(context.word, limit)

        if wants_non_option_word and profile.complete_env_vars:
            env_iter = self._env_var_completions(context.word, limit)

        if (
            wants_non_option_word
            and profile.complete_paths
            and profile.path_after_nonoption_args is not None
            and len(non_option_args) >= profile.path_after_nonoption_args
        ):
            path_iter = self._path_completions(
                context.word,
                limit=min(limit, 30),
                directories_only=profile.directories_only,
            )

        yield from self._merge_completion_iters(
            self._option_completions(command_name, context.word, limit, include_on_empty=profile.options_on_empty),
            subcommand_iter,
            command_iter,
            env_iter,
            path_iter,
            limit=limit,
        )

    def _git_option_completions(self, subcommand: str | None, word: str, limit: int):
        if word and not word.startswith("-"):
            return
        options = list(self._OPTIONS.get("git", ()))
        if subcommand:
            options.extend(self._GIT_SUBCOMMAND_OPTIONS.get(subcommand, ()))
        yield from self._complete_words(sorted(set(options)), word, meta="git option", limit=limit)

    @staticmethod
    def _path_completions(word: str, limit: int = 30, directories_only: bool = False):
        expanded = os.path.expanduser(word) if word else "."
        if expanded.endswith("/"):
            search_dir = expanded
            prefix = ""
        elif not word:
            search_dir = "."
            prefix = ""
        else:
            search_dir = os.path.dirname(expanded) or "."
            prefix = os.path.basename(expanded)

        try:
            entries = os.listdir(search_dir)
        except OSError:
            return

        count = 0
        prefix_lower = prefix.lower()
        for entry in sorted(entries):
            if prefix and not entry.lower().startswith(prefix_lower):
                continue

            full_path = os.path.join(search_dir, entry)
            is_dir = os.path.isdir(full_path)
            if directories_only and not is_dir:
                continue

            if word.startswith("~"):
                display_path = "~/" + os.path.relpath(full_path, os.path.expanduser("~"))
            elif word and os.path.isabs(word):
                display_path = full_path
            elif word and "/" in word:
                display_path = os.path.relpath(full_path)
            else:
                display_path = entry

            if is_dir:
                display_path += "/"

            yield Completion(
                display_path,
                start_position=-len(word),
                display=entry + ("/" if is_dir else ""),
                display_meta="dir" if is_dir else _file_size_label(full_path),
            )
            count += 1
            if count >= limit:
                break

    @staticmethod
    def _merge_completion_iters(*iterables, limit: int = 80):
        seen: set[str] = set()
        count = 0
        for iterable in iterables:
            if iterable is None:
                continue
            for completion in iterable:
                if completion.text in seen:
                    continue
                seen.add(completion.text)
                yield completion
                count += 1
                if count >= limit:
                    return

    def _complete_git(self, context: ShellCompletionContext, limit: int):
        non_option_args = self._non_option_args(context)
        subcommand = non_option_args[0] if non_option_args else None
        if subcommand is None and not context.word.startswith("-"):
            yield from self._complete_words(
                self._get_git_subcommand_names(),
                context.word,
                meta="git subcommand",
                limit=limit,
            )
            return

        include_paths = bool(subcommand and subcommand in self._GIT_PATH_SUBCOMMANDS and not context.word.startswith("-"))
        yield from self._merge_completion_iters(
            self._git_option_completions(subcommand, context.word, limit),
            self._path_completions(context.word, limit=min(limit, 30)) if include_paths else None,
            limit=limit,
        )

    def _complete_ls(self, context: ShellCompletionContext, limit: int):
        yield from self._merge_completion_iters(
            self._option_completions("ls", context.word, limit, include_on_empty=True),
            self._path_completions(context.word, limit=min(limit, 30)),
            limit=limit,
        )

    def _complete_cd(self, context: ShellCompletionContext, limit: int):
        yield from self._merge_completion_iters(
            self._option_completions("cd", context.word, limit),
            self._path_completions(context.word, limit=min(limit, 30), directories_only=True),
            limit=limit,
        )

    def _complete_pushd(self, context: ShellCompletionContext, limit: int):
        yield from self._merge_completion_iters(
            self._option_completions("pushd", context.word, limit),
            self._path_completions(context.word, limit=min(limit, 30), directories_only=True),
            limit=limit,
        )

    def _complete_rm(self, context: ShellCompletionContext, limit: int):
        yield from self._merge_completion_iters(
            self._option_completions("rm", context.word, limit, include_on_empty=True),
            self._path_completions(context.word, limit=min(limit, 30)),
            limit=limit,
        )

    def _complete_cp(self, context: ShellCompletionContext, limit: int):
        yield from self._merge_completion_iters(
            self._option_completions("cp", context.word, limit, include_on_empty=True),
            self._path_completions(context.word, limit=min(limit, 30)),
            limit=limit,
        )

    def _complete_mv(self, context: ShellCompletionContext, limit: int):
        yield from self._merge_completion_iters(
            self._option_completions("mv", context.word, limit, include_on_empty=True),
            self._path_completions(context.word, limit=min(limit, 30)),
            limit=limit,
        )

    def _complete_cat(self, context: ShellCompletionContext, limit: int):
        yield from self._merge_completion_iters(
            self._option_completions("cat", context.word, limit, include_on_empty=True),
            self._path_completions(context.word, limit=min(limit, 30)),
            limit=limit,
        )

    def _complete_mkdir(self, context: ShellCompletionContext, limit: int):
        yield from self._merge_completion_iters(
            self._option_completions("mkdir", context.word, limit, include_on_empty=True),
            self._path_completions(context.word, limit=min(limit, 30), directories_only=True),
            limit=limit,
        )

    def _complete_touch(self, context: ShellCompletionContext, limit: int):
        yield from self._merge_completion_iters(
            self._option_completions("touch", context.word, limit, include_on_empty=True),
            self._path_completions(context.word, limit=min(limit, 30)),
            limit=limit,
        )

    def _complete_grep(self, context: ShellCompletionContext, limit: int):
        non_option_args = self._non_option_args(context)
        include_paths = len(non_option_args) >= 1 and not context.word.startswith("-")
        yield from self._merge_completion_iters(
            self._option_completions("grep", context.word, limit, include_on_empty=True),
            self._path_completions(context.word, limit=min(limit, 30)) if include_paths else None,
            limit=limit,
        )

    def _complete_find(self, context: ShellCompletionContext, limit: int):
        non_option_args = self._non_option_args(context)
        include_paths = len(non_option_args) == 0 and not context.word.startswith("-")
        yield from self._merge_completion_iters(
            self._option_completions("find", context.word, limit, include_on_empty=True),
            self._path_completions(context.word, limit=min(limit, 30), directories_only=True) if include_paths else None,
            limit=limit,
        )

    def _complete_chmod(self, context: ShellCompletionContext, limit: int):
        non_option_args = self._non_option_args(context)
        include_paths = len(non_option_args) >= 1 and not context.word.startswith("-")
        yield from self._merge_completion_iters(
            self._option_completions("chmod", context.word, limit, include_on_empty=True),
            self._path_completions(context.word, limit=min(limit, 30)) if include_paths else None,
            limit=limit,
        )

    def _complete_chown(self, context: ShellCompletionContext, limit: int):
        non_option_args = self._non_option_args(context)
        include_paths = len(non_option_args) >= 1 and not context.word.startswith("-")
        yield from self._merge_completion_iters(
            self._option_completions("chown", context.word, limit, include_on_empty=True),
            self._path_completions(context.word, limit=min(limit, 30)) if include_paths else None,
            limit=limit,
        )

    def _complete_tar(self, context: ShellCompletionContext, limit: int):
        yield from self._merge_completion_iters(
            self._option_completions("tar", context.word, limit, include_on_empty=True),
            self._path_completions(context.word, limit=min(limit, 30)) if not context.word.startswith("-") else None,
            limit=limit,
        )

    def _complete_systemctl(self, context: ShellCompletionContext, limit: int):
        non_option_args = self._non_option_args(context)
        if not non_option_args and not context.word.startswith("-"):
            yield from self._complete_words(
                self._SYSTEMCTL_SUBCOMMANDS,
                context.word,
                meta="systemctl subcommand",
                limit=limit,
            )
            return

        yield from self._option_completions("systemctl", context.word, limit, include_on_empty=True)

    def _complete_docker(self, context: ShellCompletionContext, limit: int):
        non_option_args = self._non_option_args(context)
        if not non_option_args and not context.word.startswith("-"):
            yield from self._complete_words(
                self._DOCKER_SUBCOMMANDS,
                context.word,
                meta="docker subcommand",
                limit=limit,
            )
            return

        if non_option_args[0] == "compose" and len(non_option_args) == 1 and not context.word.startswith("-"):
            yield from self._complete_words(
                self._DOCKER_COMPOSE_SUBCOMMANDS,
                context.word,
                meta="docker compose subcommand",
                limit=limit,
            )
            return

        yield from self._option_completions("docker", context.word, limit, include_on_empty=True)

    def _complete_ssh(self, context: ShellCompletionContext, limit: int):
        yield from self._option_completions("ssh", context.word, limit, include_on_empty=True)