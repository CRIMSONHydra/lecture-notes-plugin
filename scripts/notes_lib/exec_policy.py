"""Deterministic parts of the execution policy: the floor, the fast path and the snippet sandbox.

Judgement lives in ``exec_classifier`` (an LLM decides whether a command is free of cost and light on the
machine). This module holds only what must never depend on a model:

* ``floor(cmd)``: a short list that no classifier verdict can override. It covers reading credentials or
  tokens, privilege escalation, encoded PowerShell, environment dumps, and redirecting the policy itself
  through environment variables; in ``strict`` mode (the plugin's own agents) also the guard, the hooks, the
  ledger and the cache. These guard against prompt injection: an agent that read a hostile transcript or web
  page can be talked into a command, and the classifier can be talked into approving it. Wrappers
  (``wsl …``, ``sh -c …``, ``env -S …``, ``eval …``, ``xargs``, ``timeout``) are unwrapped and ``VAR=…; … $VAR``
  is expanded in shell order, so the command that really runs is what gets checked.
* ``fast_allow(cmd)``: read-only commands and the plugin's own scripts. They pass instantly, so the
  classifier is only asked about commands that can change something. Anything this module cannot parse
  with confidence is *not* fast: the classifier sees it.
* ``sandbox_env()``: the environment ``{run}`` snippets execute in (no credentials, git limited to local repos).
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]

# command separators: `&&`, `||`, `;`, `|`, newlines, `$(`, backticks, `)`, process substitution `<(` `>(`,
# a plain `(` (subshell / PowerShell sub-expression) and a single `&` (background) that is not a redirect
_SPLIT = re.compile(r"&&|\|\||;|\||\n|\r|\$\(|`|\)|<\(|>\(|\(|(?<![<>&0-9])&(?![>&])")
_SEG = re.compile(r"(&&|\|\||;|\||\n|\r|(?<![<>&0-9])&(?![>&]))")      # the same, keeping the separators
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = {"time", "exec", "nohup", "command", "builtin", "setsid", "busybox",
             "&", "then", "do", "else", "if", "while", "until", "!", "{", "("}
# wrapper options that take a value (the value is skipped, never treated as the command)
_WRAP_OPTS = {"env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
              "timeout": {"-s", "--signal", "-k", "--kill-after"},
              "xargs": {"-I", "-i", "-d", "--delimiter", "-n", "--max-args", "-P", "--max-procs", "-L", "--max-lines",
                        "-E", "--eof", "-a", "--arg-file", "-s", "--max-chars"},
              "stdbuf": {"-o", "--output", "-e", "--error", "-i", "--input"},
              "nice": {"-n", "--adjustment"}, "ionice": {"-c", "--class", "-n", "--classdata", "-p", "--pid"},
              "watch": {"-n", "--interval", "-d", "--differences"}, "flock": {"-w", "--wait", "--timeout", "-E"},
              "chrt": {"-p"}}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "pwsh", "powershell", "cmd"}
_INTERPRETERS = _SHELLS | {"python", "python3", "py", "perl", "ruby", "node", "osascript"}

_FLOOR_WORDS = {"sudo": "privilege escalation", "su": "privilege escalation", "runas": "privilege escalation",
                "doas": "privilege escalation", "pkexec": "privilege escalation", "gsudo": "privilege escalation",
                "elevate": "privilege escalation", "printenv": "an environment dump prints tokens"}
_CRED_FILES = re.compile(
    r"\.(aws|ssh|kube|azure|gnupg)(?![\w-])|kubeconfig\b|\.docker[/\\]|\.git-credentials|\.netrc|\.npmrc|\.pypirc"
    r"|gcloud[/\\](credentials|application_default|legacy_credentials|access_tokens)|\.config[/\\]gh[/\\]"
    r"|GitHub CLI[/\\]hosts\.yml|\.claude[/\\]\.credentials|\.terraform\.d[/\\]credentials|credentials\.tfrc\.json"
    r"|/proc/\w+/environ|169\.254\.169\.254|metadata\.google\.internal|fd00:ec2::254"
    r"|\b(dir|gci|ls|get-childitem|get-item|gi|gc|get-content|cat|type)\s+env:", re.I)
_CRED_VARS = re.compile(
    r"\$\{?\w*(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|CREDENTIAL|PRIVATE_KEY|ACCESS_KEY|CLIENT_SECRET)\w*"
    r"|\$env:\w*(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)
_TAMPER = re.compile(r"LECTURE_NOTES_CLASSIFIER|NOTES_EXEC_CLASSIFIER|exec-cache|exec-ledger|guard\.json|clear-guard"
                     r"|[\\/]guards\b"
                     r"|hooks\.json|exec_policy|exec_classifier|exec_cleanup|exec_guard_hook|guard_hook", re.I)
_OVERRIDE = re.compile(r"(?:^|[\s;&|(]|\$env:)(NOTES_EXEC_CLASSIFIER|LECTURE_NOTES_CLASSIFIER_CMD|LECTURE_NOTES_CLASSIFIER_MODEL"
                       r"|LECTURE_NOTES_CLAUDE|LECTURE_NOTES_DATA|CLAUDE_PLUGIN_DATA|CLAUDE_PLUGIN_ROOT)\s*=", re.I)
_ENV_DUMP = re.compile(r"(^|[;&|\n(]\s*)(env|set|export\s+-p|declare\s+-p|declare\s+-x)\s*($|[;&|\n)>])", re.M)
_RUNAS = re.compile(r"-verb\s+runas\b", re.I)
# a prefix assignment of one of these changes what a "read-only" command actually runs
_DANGEROUS_PREFIX = re.compile(r"^(PATH|PATHEXT|LD_|DYLD_|PYTHON|UV_|GIT_|BASH_ENV|ENV|PERL5|NODE_OPTIONS|RUBYOPT"
                               r"|SHELL|IFS|PS4|PROMPT_COMMAND|HOME|COMSPEC|PSMODULEPATH|TMPDIR|TEMP|TMP|XDG_|CDPATH"
                               r"|GLOBIGNORE|JAVA_TOOL_OPTIONS|_JAVA_OPTIONS|SSL_CERT|SSLKEYLOGFILE)", re.I)

_READ_ONLY = {"ls", "dir", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "sort", "uniq", "cut",
              "tr", "echo", "printf", "pwd", "cd", "true", "false", "test", "[", "stat", "file", "which",
              "type", "date", "basename", "dirname", "realpath", "readlink", "diff", "cmp", "comm", "md5sum",
              "sha256sum", "sha1sum", "jq", "column", "nl", "tac", "rev", "fold", "expand", "seq", "sleep",
              "get-content", "gc", "get-childitem", "gci", "select-string", "sls", "test-path", "get-item",
              "measure-object", "select-object", "where-object", "sort-object", "format-table", "format-list",
              "resolve-path", "split-path", "join-path", "write-output", "write-host", "get-location", "pwd",
              "where", "whoami", "hostname", "uname", "id", "ps", "tree", "du", "df", "nproc", "free", "uptime",
              "ver", "less", "more", "strings", "od", "xxd", "hexdump", "getconf", "locale"}
# PowerShell cmdlets whose arguments can carry a script block or sub-expression that runs code
_PS_CMDLETS = {"get-content", "gc", "get-childitem", "gci", "select-string", "sls", "test-path", "get-item",
               "measure-object", "select-object", "where-object", "sort-object", "format-table", "format-list",
               "resolve-path", "split-path", "join-path", "write-output", "write-host", "get-location"}
_GIT_READ = {"status", "log", "diff", "show", "rev-parse", "ls-files", "blame", "shortlog", "describe",
             "cat-file", "ls-tree", "grep", "-h", "--version", "version"}
_UV_FLAGS = {"--quiet", "-q", "--script", "--no-project", "--offline", "--isolated", "--no-sync", "--frozen",
             "--locked", "--no-cache", "--refresh", "--no-progress"}
_UV_FLAGS_VAL = {"--with"}
_WIN_PATH = re.compile(r"[A-Za-z]:\\|\.\\|\\\\")


def _norm(word: str) -> str:
    word = word.lstrip("$")                               # $'sudo' (after quote removal: $sudo) is sudo
    w = word.strip("\"'(){}").replace("\\", "/").rsplit("/", 1)[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1"):
        if w.endswith(ext):
            return w[: -len(ext)]
    return w


def _words(simple: str) -> list[str]:
    if _WIN_PATH.search(simple):                           # C:\x\y.exe would lose its backslashes in POSIX mode
        simple = simple.replace("\\", "/")
    try:
        return shlex.split(simple, posix=True)
    except ValueError:
        return simple.split()


_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def strip_heredocs(text: str) -> str:
    """Drop here-document bodies that are plain data (written to a file or piped into a non-shell program).

    A body is kept, and so checked as commands, when the heredoc feeds a shell or interpreter (``bash <<EOF``),
    when its delimiter is unquoted and the body contains ``$(``, a backtick or ``${`` (bash would expand them),
    or when the terminator never appears (then nothing is data)."""
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = _HEREDOC.search(line)
        if not m:
            i += 1
            continue
        dash, quoted, end = bool(m.group(1)), bool(m.group(2)), m.group(3)
        j = next((k for k in range(i + 1, len(lines)) if (lines[k].strip() if dash else lines[k]) == end), None)
        if j is None:
            i += 1
            continue
        body = lines[i + 1:j]
        receiver = [_norm(w) for w in _words(line[:m.start()])]
        feeds_shell = any(w in _INTERPRETERS for w in receiver)
        expands = not quoted and any(("$(" in b or "`" in b or "${" in b) for b in body)
        if feeds_shell or expands:
            i += 1                                          # keep the body: it is (or becomes) commands
            continue
        i = j + 1                                           # skip body and terminator
    return "\n".join(out)


_WSL_VAL = {"-d", "--distribution", "-u", "--user", "--cd", "--distribution-id", "--shell-type"}


def wsl_parts(args: list[str]) -> tuple[str | None, list[str]]:
    """``(distribution, inner command)`` of a ``wsl`` invocation; inner is [] for management calls."""
    distro, i = None, 0
    while i < len(args):
        a = args[i]
        if a == "--":
            return distro, args[i + 1:]
        if a in ("-d", "--distribution") and i + 1 < len(args):
            distro = args[i + 1]
            i += 2
        elif a in _WSL_VAL:
            i += 2
        elif a in ("-e", "--exec", "--system", "~"):
            i += 1
        elif a.startswith("-"):
            return distro, []
        else:
            return distro, args[i:]
    return distro, []


def _wsl_inner(args: list[str]) -> list[str]:
    return wsl_parts(args)[1]


def _unwrap(words: list[str]) -> list[str] | None:
    """Drop assignments and wrappers (with their option values) in front of the real command.
    Returns None when the command is hidden in a way that cannot be read (``env -S`` is unfolded, ``eval`` too)."""
    while words:
        w0 = _norm(words[0])
        if _ASSIGN.match(words[0]) or w0 in _WRAPPERS or re.fullmatch(r"\d+(\.\d+)?[smhd]?", words[0]) \
                or (words[0].startswith("-") and len(words) > 1):          # a stray wrapper flag
            words = words[1:]
            continue
        if w0 in _WRAP_OPTS:
            opts = _WRAP_OPTS.get(w0, set())
            rest = words[1:]
            while rest and rest[0].startswith("-") and rest[0] != "--":
                flag = rest[0]
                if w0 == "env" and (flag == "-S" or flag == "--split-string") and len(rest) > 1:
                    return _unwrap(_words(rest[1]) + rest[2:])          # the string is the command
                if flag.startswith("-S") and w0 == "env" and len(flag) > 2:
                    return _unwrap(_words(flag[2:]) + rest[1:])
                if flag in opts and len(rest) > 1:
                    rest = rest[2:]
                elif re.fullmatch(r"-[a-zA-Z]\S+", flag) and flag[:2] in opts:
                    rest = rest[1:]                                       # attached value (-I{} -oL -n2)
                else:
                    rest = rest[1:]
            if rest and rest[0] == "--":
                rest = rest[1:]
            if w0 == "timeout" and rest:                                  # DURATION
                rest = rest[1:]
            if w0 == "flock" and rest:                                    # LOCKFILE
                rest = rest[1:]
            words = rest
            continue
        if w0 == "eval":
            return _unwrap(_words(" ".join(words[1:])))
        return words
    return words


def _command_words(cmd: str) -> list[list[str]]:
    """Each simple command as a word list, wrappers and VAR=x prefixes removed; ``sh -c '…'``, ``wsl …``,
    ``env -S '…'`` and ``eval '…'`` are unfolded."""
    out: list[list[str]] = []
    for simple in _SPLIT.split(strip_heredocs(cmd)):
        words = _unwrap(_words(simple.strip()))
        if not words:
            continue
        if _norm(words[0]) == "wsl":
            inner = _wsl_inner(words[1:])
            if inner:                          # judge what runs inside Linux, not the launcher
                out += _command_words(shlex.join(inner))
                continue
        out.append(words)
        if _norm(words[0]) in _SHELLS:          # -c, combined POSIX flags (-lc, -ec), -Command, /c, /k
            for j, w in enumerate(words[1:], 1):
                if w.lower() in ("-command", "/c", "/k") or re.fullmatch(r"-[a-z]*c[a-z]*", w):
                    if j + 1 < len(words):
                        out += _command_words(" ".join(words[j + 1:]))
                    break
    return out


def _prefix_assignments(simple: str) -> list[tuple[str, str]]:
    """``NAME=value`` words at the start of a simple command (``export``/``local`` allowed)."""
    words = _words(simple.strip())
    if words and _norm(words[0]) in ("export", "local", "declare", "typeset"):
        words = words[1:]
    out = []
    for w in words:
        if not _ASSIGN.match(w):
            break
        name, _, val = w.partition("=")
        out.append((name, val))
    return out


def _subst_outside_single_quotes(text: str, env: dict[str, str]) -> str:
    parts = text.split("'")
    for i in range(0, len(parts), 2):                      # even parts are outside single quotes
        parts[i] = re.sub(r"\$\{(\w+)\}|\$(\w+)", lambda m: env.get(m.group(1) or m.group(2), m.group(0)), parts[i])
    return "'".join(parts)


def expand_vars(cmd: str) -> str:
    """Substitute ``$NAME``/``${NAME}`` the way the shell will: with the value of the latest *statement*
    assignment before the use (``PL="/x"; uv run "$PL/qa.py"``). A prefix assignment (``P=x cmd $P``) does not
    apply to its own arguments; a value built by command substitution makes the name unknown; nothing inside
    single quotes is expanded."""
    env: dict[str, str] = {}
    out = []
    for part in _SEG.split(cmd):
        if part is None:
            continue
        if _SEG.fullmatch(part):
            out.append(part)
            continue
        seg = _subst_outside_single_quotes(part, env)
        pre = _prefix_assignments(seg)
        words = _words(seg.strip())
        if words and _norm(words[0]) in ("export", "local", "declare", "typeset"):
            words = words[1:]
        pure = bool(pre) and len(pre) == len(words)
        for name, val in pre:
            if "$(" in val or "`" in val or "${" in val:
                env.pop(name, None)
            elif pure:
                env[name] = val
        out.append(seg)
    return "".join(out)


def _guard_control(words: list[str]) -> bool:
    """A plugin script invocation that sets or clears the run guard (``guard.py set|clear``, ``doctor.py --clear-guard``)."""
    names = {_norm(w) for w in words}
    return bool(names & {"guard.py", "doctor.py"}) and bool(names & {"set", "clear", "--clear-guard"})


def floor(cmd: str, strict: bool = False, sandboxed: bool = False) -> str | None:
    """Refusal that no classifier verdict overrides, or None.

    Always: credential files and cloud metadata, token-like variables (unless ``sandboxed``: snippets run
    with those variables removed), redirecting the plugin's own controls through environment variables,
    environment dumps, privilege escalation, encoded PowerShell. ``strict`` (the plugin's own agents): also
    setting or clearing the guard, and any non-read-only command that names the plugin's safety files,
    ledger or cache."""
    cmd = expand_vars(cmd)
    parts = _command_words(cmd)
    dequoted = "\n".join(" ".join(w) for w in parts)      # ~/.a''ws/credentials is ~/.aws/credentials
    for text in (cmd, dequoted):
        if _CRED_FILES.search(text) or (not sandboxed and _CRED_VARS.search(text)):
            return "it touches a credential store, a token variable or a cloud metadata endpoint"
    if _OVERRIDE.search(cmd):
        return "it redirects the plugin's safety controls through an environment variable"
    if _ENV_DUMP.search(strip_heredocs(cmd)):
        return "an environment dump prints tokens"
    if _RUNAS.search(cmd):
        return "`-Verb RunAs`: privilege escalation"
    for words in parts:
        w = _norm(words[0])
        if w in _FLOOR_WORDS:
            return f"`{w}`: {_FLOOR_WORDS[w]}"
        if w == "env" and len(words) == 1:
            return "an environment dump prints tokens"
        if w in ("powershell", "pwsh") and any(
                re.fullmatch(r"-e[a-z]*", a, re.I) and "encodedcommand".startswith(a[1:].lower()) for a in words[1:]):
            return "encoded PowerShell commands cannot be checked"
        if strict and _guard_control(words):
            return "it sets or clears the run guard, which only the orchestrator does"
    if strict and _TAMPER.search(cmd) and not fast_allow(cmd):
        return "it touches the plugin's own safety controls (the guard, the hooks, the ledger or the cache)"
    return None


_VERSION_DIR = re.compile(r"\d+(\.\d+)+")


def plugin_roots() -> list[Path]:
    """The plugin's install root(s). Claude Code keeps every installed version side by side
    (``…/plugins/cache/<marketplace>/<plugin>/<version>/``) and agents started under an older version keep
    using its paths, so a sibling version directory counts too."""
    roots = [PLUGIN_ROOT] + [Path(v) for v in [os.environ.get("CLAUDE_PLUGIN_ROOT")] if v]
    for r in list(roots):
        if _VERSION_DIR.fullmatch(r.name):
            roots.append(r.parent)
    return roots


def _under_plugin(path: str) -> bool:
    """An existing file inside the plugin. A path that does not exist (an unexpanded ``$P/qa.py`` resolved
    against the cwd) is never trusted."""
    try:
        p = Path(os.path.expanduser(path)).resolve()
        return p.is_file() and any(p.is_relative_to(r.resolve()) for r in plugin_roots())
    except (OSError, ValueError):
        return False


def _uv_plugin_script(args: list[str]) -> bool:
    """``uv run [known flags] <plugin>/x.py …``: only the plugin's own interpreter/env choices."""
    if args[:1] != ["run"]:
        return False
    rest = args[1:]
    while rest and rest[0].startswith("-"):
        flag, _, _val = rest[0].partition("=")
        if flag in _UV_FLAGS:
            rest = rest[1:]
        elif flag in _UV_FLAGS_VAL:
            rest = rest[1:] if "=" in rest[0] else rest[2:]
        else:
            return False                                  # --python, --directory, --index, … : not fast
    return bool(rest) and rest[0].endswith(".py") and _under_plugin(rest[0])


def _read_only_ok(w: str, args: list[str], body: str) -> bool:
    """Per-command flags that make a read-only command write or execute."""
    if w == "sed":
        return not any(re.match(r"-[a-zA-Z]*i", a) or a.startswith("--in-place") or a in ("-f", "--file") for a in args)
    if w == "find":
        return not any(a in ("-exec", "-execdir", "-delete", "-ok", "-okdir", "-fls") or a.startswith("-fprint") for a in args)
    if w in ("awk", "gawk"):
        return "system(" not in body and not any(a in ("-f", "-i", "-l", "--file", "--include", "--load") for a in args)
    if w == "sort":
        return not any(a.startswith(("-o", "--output", "--compress-program")) for a in args)
    if w == "uniq":
        return len([a for a in args if not a.startswith("-")]) < 2
    if w == "rg":
        return not any(a.startswith("--pre") for a in args)
    if w == "xxd":
        return not any(a in ("-r", "-revert") for a in args)
    if w == "jq":
        return not any("env" in a.lower() or "$ENV" in a for a in args)
    if w in _PS_CMDLETS:
        return not any(("{" in a or "(" in a or "$" in a) for a in args)
    return True


def fast_allow(cmd: str) -> bool:
    """True when every part of ``cmd`` is read-only or one of the plugin's own scripts."""
    body = strip_heredocs(expand_vars(cmd))
    if re.search(r"(?<![<>])>{1,2}(?!&)(?!\s*/dev/null)(?!\s*nul\b)", body):   # writes a file (also 2>f, &>f)
        return False
    if _RUNAS.search(body):
        return False
    for simple in _SEG.split(body):
        if simple and not _SEG.fullmatch(simple):
            if any(_DANGEROUS_PREFIX.match(n) for n, _ in _prefix_assignments(simple)):
                return False                              # PATH=/evil ls, PYTHONPATH=… uv run …
    parts = _command_words(body)
    if not parts:
        return True
    for words in parts:
        w, args = _norm(words[0]), words[1:]
        if w in ("uv", "uvx"):
            if w == "uv" and _uv_plugin_script(args):
                continue
            return False
        if w == "git":
            sub = next((a for a in args if not a.startswith("-")), "--version")
            if sub in _GIT_READ and not any(a == "--help" or a in ("-c", "--config-env") or a.startswith(("-O", "-o", "--output", "--exec-path"))
                                             for a in args):
                continue
            return False
        if w in _READ_ONLY or w in ("sed", "find", "awk", "gawk"):
            if _read_only_ok(w, args, body):
                continue
        return False
    return True


_SCRIPT_EXT = (".py", ".sh", ".bash", ".ps1", ".js", ".mjs", ".rb", ".pl")


def script_files(cmd: str, cwd: str | None, limit: int = 4, max_chars: int = 120_000) -> dict[str, str]:
    """Contents of local scripts a command runs (outside the plugin), so the classifier judges the code, not the
    name. Follows ``cd`` within the command; skips missing or plugin files. A script longer than ``max_chars``
    is left out rather than cut, so the classifier's "unknown script" rule applies to it."""
    out: dict[str, str] = {}
    here = Path(cwd) if cwd else None
    for simple in _SPLIT.split(strip_heredocs(expand_vars(cmd))):
        words = _words(simple.strip())
        if words and words[0] == "cd" and len(words) > 1:
            target = Path(os.path.expanduser(words[1]))
            here = target if target.is_absolute() or here is None else here / target
            continue
        for w in words[1:] if words else []:
            if not w.lower().endswith(_SCRIPT_EXT) or w.startswith("-"):
                continue
            p = Path(os.path.expanduser(w))
            if not p.is_absolute() and here is not None:
                p = here / p
            try:
                if not p.is_file() or p.stat().st_size > max_chars or _under_plugin(str(p)):
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(text) <= max_chars:
                out[p.as_posix()] = text
            if len(out) >= limit:
                return out
    return out


_STRIP_PREFIXES = ("AWS_", "AZURE_", "ARM_", "GOOGLE_", "GCLOUD_", "CLOUDSDK_", "DIGITALOCEAN_", "DO_",
                   "TF_VAR_", "TF_TOKEN_", "VAULT_", "KUBE", "HELM_", "GITHUB_", "GH_", "GITLAB_", "NPM_TOKEN",
                   "PYPI_", "TWINE_", "DOCKER_", "SSH_", "OPENAI_", "ANTHROPIC_", "HF_", "STRIPE_", "TWILIO_",
                   "SENDGRID_", "SLACK_", "DATABASE_URL", "REDIS_URL", "MONGO")
_STRIP_PARTS = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|CREDENTIAL|PRIVATE_KEY|ACCESS_KEY|_PWD$|^PGPASS", re.I)


def sandbox_env(base: dict[str, str] | None, scratch: Path) -> dict[str, str]:
    """A copy of ``base`` with credentials removed and every client pointed at an empty config."""
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if not k.upper().startswith(_STRIP_PREFIXES) and not _STRIP_PARTS.search(k)}
    empty = scratch / ".no-credentials"
    tmp = scratch / ".tmp"                               # mktemp and friends stay inside the snippet's scratch dir
    tmp.mkdir(parents=True, exist_ok=True)
    env.update({
        "TMPDIR": str(tmp), "TMP": str(tmp), "TEMP": str(tmp),
        "PYTHONUNBUFFERED": "1",                         # stdout and stderr interleave as they would on a terminal
        "AWS_CONFIG_FILE": os.devnull, "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
        "AWS_EC2_METADATA_DISABLED": "true", "KUBECONFIG": os.devnull,
        "CLOUDSDK_CONFIG": str(empty / "gcloud"), "AZURE_CONFIG_DIR": str(empty / "azure"),
        "DOCKER_CONFIG": str(empty / "docker"), "GH_CONFIG_DIR": str(empty / "gh"),
        "GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
        "GIT_ASKPASS": "", "SSH_ASKPASS": "", "GIT_SSH_COMMAND": "false",
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",
    })
    return env
