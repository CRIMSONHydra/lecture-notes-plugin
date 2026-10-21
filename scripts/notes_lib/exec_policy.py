"""Deterministic parts of the execution policy: the floor, the fast path and the snippet sandbox.

Judgement lives in ``exec_classifier`` (an LLM decides whether a command is free of cost and light on the
machine). This module holds only what must never depend on a model:

* ``floor(cmd)``: a short list that no classifier verdict can override. It covers reading credentials or
  tokens, ``wsl``, privilege escalation, encoded PowerShell, and tampering with the policy itself. These
  guard against prompt injection: an agent that read a hostile transcript or web page can be talked into a
  command, and the classifier can be talked into approving it.
* ``fast_allow(cmd)``: read-only commands and the plugin's own scripts. They pass instantly, so the
  classifier is only asked about commands that can change something.
* ``sandbox_env()``: the environment ``{run}`` snippets execute in (no credentials, git limited to local repos).
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]

_SPLIT = re.compile(r"&&|\|\||;|\||\n|\r|\$\(|`|\)")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = {"env", "time", "exec", "nohup", "command", "builtin", "nice", "stdbuf", "xargs", "&", ".",
             "then", "do", "else", "if", "while", "until", "!", "{", "(", "timeout"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "pwsh", "powershell", "cmd"}

_FLOOR_WORDS = {"wsl": "WSL runs commands inside a Linux VM, outside every check",
                "wslconfig": "WSL runs commands inside a Linux VM, outside every check",
                "sudo": "privilege escalation", "su": "privilege escalation", "runas": "privilege escalation",
                "doas": "privilege escalation", "pkexec": "privilege escalation",
                "printenv": "an environment dump prints tokens"}
_CRED = re.compile(
    r"\.aws[/\\]|\.kube[/\\]|\.ssh[/\\]|\.docker[/\\]config\.json|\.git-credentials|\.netrc|\.azure[/\\]"
    r"|gcloud[/\\](credentials|application_default|legacy_credentials)|\.config[/\\]gh[/\\]|\.claude[/\\]\.credentials"
    r"|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|GITHUB_TOKEN|GH_TOKEN|ARM_CLIENT_SECRET|ANTHROPIC_API_KEY"
    r"|\$env:\w*(KEY|TOKEN|SECRET)|\b(dir|gci|ls|get-childitem)\s+env:"
    r"|169\.254\.169\.254|metadata\.google\.internal|fd00:ec2::254", re.I)
_TAMPER = re.compile(r"LECTURE_NOTES_CLASSIFIER|NOTES_EXEC_CLASSIFIER|exec-cache|exec-ledger|guard\.json"
                     r"|hooks\.json|exec_policy|exec_classifier|exec_cleanup|exec_guard_hook|guard_hook", re.I)
_ENCODED = re.compile(r"-(e|ec|enc|encodedcommand)", re.I)
_ENV_DUMP = re.compile(r"(^|[;&|\n(]\s*)(env|set|export\s+-p|declare\s+-p|declare\s+-x)\s*($|[;&|\n)>])", re.M)

_READ_ONLY = {"ls", "dir", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "sort", "uniq", "cut",
              "tr", "echo", "printf", "pwd", "cd", "true", "false", "test", "[", "stat", "file", "which",
              "type", "date", "basename", "dirname", "realpath", "readlink", "diff", "cmp", "comm", "md5sum",
              "sha256sum", "sha1sum", "jq", "column", "nl", "tac", "rev", "fold", "expand", "seq", "sleep",
              "get-content", "gc", "get-childitem", "gci", "select-string", "sls", "test-path", "get-item",
              "measure-object", "select-object", "where-object", "sort-object", "format-table", "format-list",
              "resolve-path", "split-path", "join-path", "write-output", "write-host", "get-location", "pwd"}
_GIT_READ = {"status", "log", "diff", "show", "rev-parse", "ls-files", "blame", "shortlog", "describe",
             "cat-file", "ls-tree", "reflog", "grep", "-h", "--version", "version"}


def _norm(word: str) -> str:
    w = word.strip("\"'(){}").replace("\\", "/").rsplit("/", 1)[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1"):
        if w.endswith(ext):
            return w[: -len(ext)]
    return w


def _words(simple: str) -> list[str]:
    try:
        return shlex.split(simple, posix=True)
    except ValueError:
        return simple.split()


_HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def strip_heredocs(text: str) -> str:
    """Drop here-document bodies: they are data written to a file or stdin, not commands."""
    out, end, dash = [], None, False
    for line in text.splitlines():
        if end is not None:
            if (line.strip() if dash else line) == end:
                end = None
            continue
        out.append(line)
        m = _HEREDOC.search(line)
        if m:
            dash, end = bool(m.group(1)), m.group(3)
    return "\n".join(out)


def _command_words(cmd: str) -> list[list[str]]:
    """Each simple command as a word list, wrappers and VAR=x prefixes removed; ``sh -c '…'`` is unfolded."""
    out: list[list[str]] = []
    for simple in _SPLIT.split(strip_heredocs(cmd)):
        words = _words(simple.strip())
        while words and (_ASSIGN.match(words[0]) or _norm(words[0]) in _WRAPPERS
                         or (words[0].startswith("-") and len(words) > 1) or re.fullmatch(r"\d+[smhd]?", words[0])):
            words = words[1:]
        if not words:
            continue
        out.append(words)
        if _norm(words[0]) in _SHELLS:
            low = [w.lower() for w in words]
            for flag in ("-c", "-command", "/c", "/k"):
                if flag in low and low.index(flag) + 1 < len(words):
                    out += _command_words(" ".join(words[low.index(flag) + 1:]))
    return out


def floor(cmd: str) -> str | None:
    """Refusal that no classifier verdict overrides, or None."""
    if _CRED.search(cmd):
        return "it touches a credential store, a token variable or a cloud metadata endpoint"
    if _TAMPER.search(cmd):
        return "it touches the plugin's own safety controls"
    if _ENV_DUMP.search(strip_heredocs(cmd)):
        return "an environment dump prints tokens"
    for words in _command_words(cmd):
        w = _norm(words[0])
        if w in _FLOOR_WORDS:
            return f"`{w}`: {_FLOOR_WORDS[w]}"
        if w == "env" and len(words) == 1:
            return "an environment dump prints tokens"
        if w in ("powershell", "pwsh") and any(_ENCODED.fullmatch(a) for a in words[1:]):
            return "encoded PowerShell commands cannot be checked"
    return None


def _under_plugin(path: str) -> bool:
    roots = [PLUGIN_ROOT] + [Path(v) for v in [os.environ.get("CLAUDE_PLUGIN_ROOT")] if v]
    try:
        p = Path(os.path.expanduser(path)).resolve()
        return any(p.is_relative_to(r.resolve()) for r in roots)
    except (OSError, ValueError):
        return False


def fast_allow(cmd: str) -> bool:
    """True when every part of ``cmd`` is read-only or one of the plugin's own scripts."""
    body = strip_heredocs(cmd)
    if re.search(r"(^|[^<>&0-9])>{1,2}(?!&|\s*/dev/null|\s*nul\b)", body):   # writes a file
        return False
    parts = _command_words(body)
    if not parts:
        return True
    for words in parts:
        w, args = _norm(words[0]), words[1:]
        if w in ("uv", "uvx"):
            rest = args[1:] if args[:1] == ["run"] else (args if w == "uvx" else None)
            while rest and rest[0].startswith("-"):
                rest = rest[2:] if rest[0] in ("--with", "--python", "-p", "--project", "--directory") else rest[1:]
            if rest and rest[0].endswith(".py") and _under_plugin(rest[0]):
                continue
            return False
        if w == "git":
            sub = next((a for a in args if not a.startswith("-")), "--version")
            if sub in _GIT_READ and "--help" not in args:
                continue
            return False
        if w == "sed" and any(a.startswith("-i") or a == "--in-place" for a in args):
            return False
        if w == "find" and any(a in ("-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprint") for a in args):
            return False
        if w in ("awk", "gawk") and "system(" in cmd:
            return False
        if w in _READ_ONLY or w in ("sed", "find", "awk", "gawk"):
            continue
        return False
    return True


_STRIP_PREFIXES = ("AWS_", "AZURE_", "ARM_", "GOOGLE_", "GCLOUD_", "CLOUDSDK_", "DIGITALOCEAN_", "DO_",
                   "TF_VAR_", "TF_TOKEN_", "VAULT_", "KUBE", "HELM_", "GITHUB_", "GH_", "GITLAB_", "NPM_TOKEN",
                   "PYPI_", "TWINE_", "DOCKER_", "SSH_", "OPENAI_", "ANTHROPIC_", "HF_")


def sandbox_env(base: dict[str, str] | None, scratch: Path) -> dict[str, str]:
    """A copy of ``base`` with credentials removed and every client pointed at an empty config."""
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if not k.upper().startswith(_STRIP_PREFIXES) and "TOKEN" not in k.upper() and "SECRET" not in k.upper()}
    empty = scratch / ".no-credentials"
    env.update({
        "AWS_CONFIG_FILE": os.devnull, "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
        "AWS_EC2_METADATA_DISABLED": "true", "KUBECONFIG": os.devnull,
        "CLOUDSDK_CONFIG": str(empty / "gcloud"), "AZURE_CONFIG_DIR": str(empty / "azure"),
        "DOCKER_CONFIG": str(empty / "docker"), "GH_CONFIG_DIR": str(empty / "gh"),
        "GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
        "GIT_ASKPASS": "", "SSH_ASKPASS": "", "GIT_SSH_COMMAND": "false",
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",
    })
    return env
