"""Execution policy: what an agent, or a ``{run}`` snippet, may execute during a notes run.

Notes are written from the transcript, the instructor code and official docs. They are never produced by
re-running the class against real infrastructure on the student's machine, which usually holds live cloud,
GitHub and registry credentials. ``classify(cmd)`` returns a refusal reason, or None when the command is
allowed. The rules (``reference/execution-policy.md`` is the prose version agents read):

* never: cloud CLIs and SDKs, IaC beyond ``fmt``/``version``, Kubernetes tooling, remote shells, ``gh``,
  git network commands without ``GIT_ALLOW_PROTOCOL=file``, git help pages and credential helpers, WSL,
  privilege escalation, package installs, GUI and browser openers, credential stores, scanners and load
  tools, ``curl``/``wget`` that send data or reach a metadata endpoint, pipe-to-shell installers;
* ``docker`` only in a contained form: allowlisted small images, ``--rm``, a ``notes-`` name, at most
  512 MiB and 1 CPU, loopback-only ports, no host access, cleanup in the same command when detached.
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
             "then", "do", "else", "if", "while", "until", "!", "{", "("}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "pwsh", "powershell", "cmd"}
_SCRIPT_EXT = (".sh", ".bash", ".ps1", ".cmd", ".bat")

NEVER = {
    # cloud CLIs
    "aws": "a cloud CLI", "awscli": "a cloud CLI", "az": "a cloud CLI", "azure-cli": "a cloud CLI", "gcloud": "a cloud CLI", "gsutil": "a cloud CLI",
    "bq": "a cloud CLI", "eksctl": "a cloud CLI", "sam": "a cloud CLI", "cdk": "a cloud CLI",
    "cdktf": "a cloud CLI", "pulumi": "a cloud CLI", "doctl": "a cloud CLI", "oci": "a cloud CLI",
    "ibmcloud": "a cloud CLI", "terragrunt": "an IaC tool",
    # orchestration and config management
    "kubectl": "Kubernetes tooling", "kubeadm": "Kubernetes tooling", "helm": "Kubernetes tooling",
    "minikube": "Kubernetes tooling", "kind": "Kubernetes tooling", "k3d": "Kubernetes tooling",
    "k3s": "Kubernetes tooling", "k9s": "Kubernetes tooling", "kustomize": "Kubernetes tooling",
    "argocd": "Kubernetes tooling", "flux": "Kubernetes tooling", "istioctl": "Kubernetes tooling",
    "ansible": "config management", "ansible-playbook": "config management",
    "ansible-galaxy": "config management", "vagrant": "a VM manager", "packer": "an image builder",
    "podman": "a container engine", "nerdctl": "a container engine", "buildah": "a container engine",
    "docker-compose": "multi-container orchestration",
    # remote access and accounts
    "ssh": "a remote shell", "scp": "a remote copy", "sftp": "a remote copy", "ssh-copy-id": "a remote shell",
    "ssh-keygen": "a key generator writing to ~/.ssh", "telnet": "a remote shell", "gh": "the GitHub CLI",
    "hub": "a GitHub client", "glab": "a GitLab client", "nc": "a raw socket tool", "ncat": "a raw socket tool",
    "netcat": "a raw socket tool",
    # host changes
    "wsl": "WSL", "wslconfig": "WSL", "sudo": "privilege escalation", "su": "privilege escalation",
    "runas": "privilege escalation", "doas": "privilege escalation", "pkexec": "privilege escalation",
    "apt": "a package install", "apt-get": "a package install", "aptitude": "a package install",
    "yum": "a package install", "dnf": "a package install", "zypper": "a package install",
    "pacman": "a package install", "brew": "a package install", "choco": "a package install",
    "winget": "a package install", "scoop": "a package install", "snap": "a package install",
    "flatpak": "a package install", "systemctl": "a service manager", "service": "a service manager",
    "crontab": "a scheduler", "schtasks": "a scheduler", "reg": "a registry editor", "setx": "a persistent env change",
    "shutdown": "a power command", "reboot": "a power command", "halt": "a power command",
    "poweroff": "a power command", "mkfs": "a disk tool", "diskpart": "a disk tool", "fdisk": "a disk tool",
    "format": "a disk tool",
    # GUI and browser openers
    "start": "a GUI/browser opener", "explorer": "a GUI/browser opener", "xdg-open": "a GUI/browser opener",
    "open": "a GUI/browser opener", "rundll32": "a GUI/browser opener", "mshta": "a GUI/browser opener",
    "start-process": "a GUI/browser opener", "saps": "a GUI/browser opener", "invoke-item": "a GUI/browser opener",
    "ii": "a GUI/browser opener", "cmdkey": "a credential store",
    # scanners and load generators
    "nmap": "a network scanner", "masscan": "a network scanner", "hping3": "a packet generator",
    "ab": "a load generator", "hey": "a load generator", "wrk": "a load generator", "siege": "a load generator",
    "vegeta": "a load generator", "k6": "a load generator", "locust": "a load generator",
    # credential dumps
    "printenv": "an environment dump (tokens)",
}

_CRED = re.compile(
    r"\.aws[/\\]|\.kube[/\\]|\.ssh[/\\]|\.docker[/\\]config\.json|\.git-credentials|\.netrc|\.azure[/\\]"
    r"|gcloud[/\\](credentials|application_default|legacy_credentials)|\.config[/\\]gh[/\\]"
    r"|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|GITHUB_TOKEN|GH_TOKEN|ARM_CLIENT_SECRET"
    r"|\$env:\w*(KEY|TOKEN|SECRET|AWS|AZURE|GITHUB)|\b(dir|gci|ls|get-childitem)\s+env:"
    r"|169\.254\.169\.254|metadata\.google\.internal|fd00:ec2::254", re.I)
_PIPE_SH = re.compile(r"\b(curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b[^|\n]*\|\s*"
                      r"(sudo\s+)?(ba|z|da)?sh\b|\b(iex|invoke-expression)\b", re.I)

DOCKER_IMAGES = {"hello-world", "alpine", "busybox", "nginx", "httpd", "redis", "python", "node", "ubuntu",
                 "debian", "curlimages/curl", "traefik/whoami"}
DOCKER_MAX_MEM_MIB = 512
DOCKER_MAX_CPUS = 1.0
NAME_PREFIX = "notes-"
_DOCKER_READ = {"version", "info", "ps", "images", "inspect", "logs", "port", "top", "diff", "history"}
_DOCKER_OWN = {"rm", "stop", "kill", "restart", "start", "exec", "cp", "rmi"}
_DOCKER_VAL = {"-e", "--env", "--env-file", "-m", "--memory", "--memory-swap", "--memory-reservation", "--cpus",
               "--cpu-shares", "--cpuset-cpus", "--name", "-p", "--publish", "-v", "--volume", "--mount", "-w",
               "--workdir", "--network", "--net", "--network-alias", "-u", "--user", "--entrypoint", "-h",
               "--hostname", "-l", "--label", "--restart", "--pids-limit", "--cap-add", "--cap-drop", "--device",
               "--platform", "--ulimit", "--add-host", "--dns", "--health-cmd", "--health-interval",
               "--stop-timeout", "--stop-signal", "--pid", "--ipc", "--uts", "--userns", "--cgroupns",
               "--security-opt", "--tmpfs", "--shm-size", "--log-driver", "--log-opt", "--cidfile", "--gpus",
               "--runtime", "--expose", "--link", "--ip", "--mac-address", "--pull"}
_DOCKER_HOSTILE = {"--privileged", "-P", "--publish-all", "--cap-add", "--device", "--gpus", "--security-opt",
                   "--runtime", "--platform", "--pid", "--ipc", "--uts", "--userns", "--cgroupns",
                   "--volumes-from", "--oom-kill-disable"}
_GIT_NET = {"clone", "fetch", "pull", "push", "ls-remote", "submodule", "remote", "archive", "request-pull",
            "send-email", "svn", "p4"}
_PY_IMPORT = re.compile(r"^\s*(from|import)\s+(boto3|botocore|aioboto3|aiobotocore|azure|google\.cloud"
                        r"|googleapiclient|kubernetes|docker|paramiko|fabric|github|pulumi|ansible|hvac)\b", re.M)
_EXEC_CALL = re.compile(r"\b(subprocess|os\.system|os\.popen|os\.exec\w*|os\.spawn\w*|pty\.spawn|system|popen"
                        r"|execv\w*|ProcessBuilder|Runtime\.getRuntime|child_process|exec\.Command)\b")
_LITERAL = re.compile(r"""(?P<q>["'])(?P<s>(?:\\.|(?!(?P=q)).){1,400})(?P=q)""")


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


def _mib(v: str) -> float | None:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([bkmg]?)i?b?", v.strip().lower())
    if not m:
        return None
    n, u = float(m.group(1)), m.group(2)
    return {"b": n / 2**20, "": n / 2**20, "k": n / 1024, "m": n, "g": n * 1024}[u]


def _image_ok(ref: str) -> bool:
    name = ref.split("@", 1)[0]
    if ":" in name.rsplit("/", 1)[-1]:
        name = name.rsplit(":", 1)[0]
    for pre in ("docker.io/library/", "docker.io/", "library/"):
        if name.startswith(pre):
            name = name[len(pre):]
    return name in DOCKER_IMAGES


def _docker_run(args: list[str], full: str) -> str | None:
    flags: dict[str, list[str]] = {}
    image = None
    i = 0
    while i < len(args):
        a = args[i]
        if not a.startswith("-") or a == "-":
            image = a
            break
        key, val = (a.split("=", 1) + [None])[:2] if a.startswith("--") else (a, None)
        if key in _DOCKER_HOSTILE:
            return f"docker run {key} gives the container host access"
        if val is None and key in _DOCKER_VAL:
            i += 1
            val = args[i] if i < len(args) else ""
        flags.setdefault(key, []).append(val or "")
        i += 1
    if image is None or not _image_ok(image):
        return (f"docker image {image!r} is not on the small-image allowlist "
                f"({', '.join(sorted(DOCKER_IMAGES))})")
    if "--rm" not in flags:
        return "docker run needs --rm"
    names = flags.get("--name", [])
    if not names or not names[-1].startswith(NAME_PREFIX):
        return f"docker run needs --name {NAME_PREFIX}<something>"
    mem = [_mib(v) for v in flags.get("-m", []) + flags.get("--memory", [])]
    if not mem or mem[-1] is None or mem[-1] > DOCKER_MAX_MEM_MIB:
        return f"docker run needs --memory of at most {DOCKER_MAX_MEM_MIB}m"
    try:
        cpus = float((flags.get("--cpus") or ["x"])[-1])
    except ValueError:
        cpus = None
    if cpus is None or cpus > DOCKER_MAX_CPUS:
        return f"docker run needs --cpus of at most {DOCKER_MAX_CPUS:g}"
    for p in flags.get("-p", []) + flags.get("--publish", []):
        if not p.startswith("127.0.0.1:"):
            return "docker run may publish ports only on 127.0.0.1 (-p 127.0.0.1:HOST:CONTAINER)"
    for n in flags.get("--network", []) + flags.get("--net", []):
        if n == "host" or n.startswith("container:"):
            return "docker run --network host/container: shares the host network"
    for v in flags.get("-v", []) + flags.get("--volume", []):
        src = v.split(":", 1)[0]
        if not src.startswith(NAME_PREFIX):
            return f"docker run may mount only named volumes called {NAME_PREFIX}* (no bind mounts)"
    for m in flags.get("--mount", []):
        if "type=bind" in m or "docker.sock" in m or not re.search(rf"(source|src)={NAME_PREFIX}", m):
            if "type=tmpfs" not in m:
                return f"docker run --mount may use only tmpfs or named volumes called {NAME_PREFIX}*"
    for r in flags.get("--restart", []):
        if r not in ("", "no"):
            return "docker run --restart would keep the container alive after the run"
    if "-d" in flags or "--detach" in flags or any(re.fullmatch(r"-[a-z]*d[a-z]*", k) for k in flags):
        name = re.escape(names[-1])
        if not re.search(rf"docker(\.exe)?\s+(rm\s+-f|stop|kill)\s+([^;&|\n]*\s)?{name}\b", full):
            return f"a detached container must be removed in the same command (docker rm -f {names[-1]})"
    return None


def _docker(args: list[str], full: str) -> str | None:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-H", "--host", "-c", "--context", "--config") or args[i].startswith(("--host=", "--context=")):
            return "docker must use the local default engine (no -H/--context/--config)"
        i += 1
    if i == len(args):
        return None
    sub, rest = args[i], args[i + 1:]
    if sub in _DOCKER_READ:
        return None
    if sub in ("image", "container", "network", "volume"):
        if not rest:
            return None
        op, rest = rest[0], rest[1:]
        if op in ("ls", "list", "inspect", "history", "logs", "port", "top"):
            return None
        if op in ("rm", "create", "stop", "kill") and sub != "image":
            targets = [a for a in rest if not a.startswith("-")]
            if targets and all(t.startswith(NAME_PREFIX) for t in targets):
                return None
            return f"docker {sub} {op} may only touch objects named {NAME_PREFIX}*"
        if op == "pull" and sub == "image":
            sub = "pull"
        elif op == "run" and sub == "container":
            return _docker_run(rest, full)
        else:
            return f"docker {sub} {op} is not allowed (only ls/inspect, and create/rm of {NAME_PREFIX}* objects)"
    if sub == "pull":
        refs = [a for a in rest if not a.startswith("-")]
        if refs and all(_image_ok(r) for r in refs):
            return None
        return f"docker pull is limited to the small-image allowlist ({', '.join(sorted(DOCKER_IMAGES))})"
    if sub in ("run", "create"):
        return _docker_run(rest, full) if sub == "run" else "docker create is not allowed; use docker run --rm"
    if sub in _DOCKER_OWN:
        targets = [a for a in rest if not a.startswith("-")]
        if sub in ("exec", "cp"):
            targets = targets[:1] if sub == "exec" else [t.split(":", 1)[0] for t in targets if ":" in t]
        if targets and all(t.startswith(NAME_PREFIX) for t in targets):
            return None
        return f"docker {sub} may only touch containers/images named {NAME_PREFIX}*"
    if sub == "build":
        if any(a == "--push" or a.startswith("--output") or a == "-o" for a in rest):
            return "docker build may not push or export"
        tags = [rest[j + 1] for j, a in enumerate(rest[:-1]) if a in ("-t", "--tag")]
        tags += [a.split("=", 1)[1] for a in rest if a.startswith("--tag=")]
        if not tags or not all(t.startswith(NAME_PREFIX) for t in tags):
            return f"docker build needs -t {NAME_PREFIX}<name> so the image can be cleaned up"
        return None
    if sub == "stats" and "--no-stream" in rest:
        return None
    return (f"`docker {sub}` is not allowed (login, push, compose, system, prune and friends touch your "
            "account or delete your own data)")


def _git(args: list[str], seg_env: dict[str, str], full: str, local_git: bool = False) -> str | None:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-C", "-c") else 1
    if "--help" in args or (i < len(args) and args[i] == "help"):
        return "git help pages open a browser on Windows; use `git <cmd> -h` for the short usage text"
    if i == len(args):
        return None
    sub = args[i]
    if sub.startswith("credential"):
        return "git credential helpers can open a login window"
    if sub in _GIT_NET:
        if sub == "remote" and (len(args) <= i + 1 or args[i + 1] not in ("update", "prune", "show")):
            return None
        if sub == "submodule" and not any(a in ("update", "add", "sync", "foreach") for a in args[i + 1:]):
            return None
        local = local_git or seg_env.get("GIT_ALLOW_PROTOCOL") == "file" or re.search(
            r"(^|[\s;&])export\s+GIT_ALLOW_PROTOCOL=file\b", full)
        if not local:
            return (f"git {sub} may reach a real remote; prefix it with GIT_ALLOW_PROTOCOL=file "
                    "(local bare repos only)")
    return None


def _terraform(args: list[str]) -> str | None:
    sub = next((a for a in args if not a.startswith("-")), None)
    if sub in (None, "fmt", "version") or args[:1] in (["-version"], ["--version"], ["-help"], ["-h"]):
        return None
    return f"terraform/tofu {sub} needs providers or touches real state; only fmt and version may run"


_AUTH = re.compile(r"authorization|token|cookie|api[-_]?key", re.I)


def _curl(word: str, args: list[str]) -> str | None:
    ps = word in ("invoke-webrequest", "invoke-restmethod", "iwr", "irm")
    for j, a in enumerate(args):
        nxt = args[j + 1] if j + 1 < len(args) else ""
        if ps:                                   # PowerShell parameters are case-insensitive
            al = a.lower()
            if al == "-method" and nxt.lower() not in ("get", "head"):
                return f"{word} may only GET or HEAD"
            if al in ("-body", "-infile", "-credential", "-form", "-token", "-authentication"):
                return f"{word} may not send data or credentials"
            if al == "-headers" and _AUTH.search(nxt):
                return f"{word} may not send auth headers"
            continue
        if (a in ("-X", "--request", "--method") and nxt.upper() not in ("GET", "HEAD")) or re.match(
                r"--(request|method)=(?!(GET|HEAD)$)", a, re.I):
            return f"{word} may only GET or HEAD"
        if a in ("-d", "-F", "-T", "-u", "-b", "--json", "--form", "--upload-file", "--user", "--cookie",
                 "--oauth2-bearer", "--post-data", "--post-file", "--body-data", "--body-file",
                 "--http-user", "--http-password", "--password") or a.startswith(
                 ("--data", "--form-", "--post-", "--body-", "--user=", "--password=")):
            return f"{word} may not send data or credentials"
        if a in ("-H", "--header") and _AUTH.search(nxt) or a.startswith("--header=") and _AUTH.search(a):
            return f"{word} may not send auth headers"
    return None


def _python_source(src: str) -> str | None:
    m = _PY_IMPORT.search(src)
    if m:
        return f"code imports {m.group(2)}, which reaches cloud, cluster or remote APIs"
    return _source_reason(src)


def _source_reason(src: str) -> str | None:
    """Generic check for any language: process-spawning code that names a forbidden command."""
    if _CRED.search(src):
        return "code touches a credential store or a cloud metadata endpoint"
    if not _EXEC_CALL.search(src):
        return None
    for m in _LITERAL.finditer(src):
        first = _norm((m.group("s").split() or [""])[0])
        if first in NEVER or first in ("docker", "terraform", "tofu", "curl", "wget"):
            r = classify(m.group("s")) if first in ("docker", "terraform", "tofu", "curl", "wget") else NEVER[first]
            if r:
                return f"code spawns `{first}` ({r}); run it from the shell so the policy can check it, or don't"
    return None


def _script_file(path: str, cwd: str | None) -> Path | None:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute() and cwd:
        p = Path(cwd) / p
    try:
        return p if p.is_file() and p.stat().st_size < 512_000 else None
    except OSError:
        return None


def _under_plugin(p: Path) -> bool:
    roots = [PLUGIN_ROOT] + [Path(v) for v in [os.environ.get("CLAUDE_PLUGIN_ROOT")] if v]
    try:
        rp = p.resolve()
        return any(rp.is_relative_to(r.resolve()) for r in roots)
    except OSError:
        return False


def _simple(simple: str, full: str, cwd: str | None, depth: int, local_git: bool = False) -> str | None:
    words = _words(simple)
    env: dict[str, str] = {}
    while words:
        w = words[0]
        if _ASSIGN.match(w):
            k, v = w.split("=", 1)
            env[k] = v
            words = words[1:]
        elif _norm(w) == "env" and all(_ASSIGN.match(x) or x.startswith("-") for x in words[1:]):
            return "an environment dump can print tokens"
        elif _norm(w) in _WRAPPERS:
            words = words[1:]
        elif _norm(w) == "timeout":                 # timeout [-flags] DURATION CMD...
            words = words[1:]
            while words and words[0].startswith("-"):
                words = words[1:]
            words = words[1:]
        else:
            break
    if not words:
        return None
    word, args = _norm(words[0]), words[1:]
    if word in NEVER:
        return f"`{word}` is {NEVER[word]}"
    if word == "docker":
        return _docker(args, full)
    if word in ("terraform", "tofu"):
        return _terraform(args)
    if word == "git":
        return _git(args, env, full, local_git)
    if word in ("curl", "wget", "invoke-webrequest", "invoke-restmethod", "iwr", "irm"):
        return _curl(word, args)
    if word in ("pip", "pip3") and args[:1] in (["install"], ["download"]):
        return "pip install changes the environment; use `uv run --with PKG`"
    if word in ("npm", "pnpm", "yarn", "bun") and ("-g" in args or "--global" in args or args[:1] == ["global"]):
        return f"{word} global installs change the machine"
    if word in ("cargo", "go", "gem", "dotnet") and args[:1] in (["install"], ["tool"]):
        return f"{word} install changes the machine"
    if word == "uv" and (args[:2] == ["tool", "install"] or (args[:2] == ["pip", "install"] and "--system" in args)):
        return "uv tool/system installs change the machine"
    if word in ("uv", "uvx") and depth < 3:
        i = 1 if word == "uv" and args[:1] == ["run"] else 0
        rest = args[i:] if word == "uv" else args
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] in ("--with", "--python", "-p", "--project", "--directory",
                                           "--env-file", "--from", "--with-requirements") else rest[1:]
        if word == "uv" and i == 0:
            return None
        if rest:
            return _simple(shlex.join(rest), full, cwd, depth + 1, local_git)
        return None
    if word.startswith("python") or word == "py":
        if "-c" in args and args.index("-c") + 1 < len(args):
            return _python_source(args[args.index("-c") + 1])
        target = next((a for a in args if not a.startswith("-")), None)
        f = _script_file(target, cwd) if target else None
        if f and not _under_plugin(f):
            try:
                return _python_source(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                return None
        return None
    if word.endswith(".py"):
        f = _script_file(words[0], cwd)
        if f and not _under_plugin(f):
            return _python_source(f.read_text(encoding="utf-8", errors="replace"))
        return None
    if word in _SHELLS and depth < 3:
        for flag in ("-c", "-command", "/c", "/k", "-encodedcommand", "-e", "-enc"):
            low = [a.lower() for a in args]
            if flag in low:
                if flag in ("-encodedcommand", "-e", "-enc"):
                    return "encoded PowerShell commands cannot be checked"
                rest = " ".join(args[low.index(flag) + 1:])
                return classify(rest, cwd=cwd, _depth=depth + 1, local_git=local_git)
        target = next((a for a in args if not a.startswith("-")), None)
        if target and target.lower().endswith(_SCRIPT_EXT):
            f = _script_file(target, cwd)
            if f and not _under_plugin(f):
                return classify(f.read_text(encoding="utf-8", errors="replace"), cwd=cwd, _depth=depth + 1, local_git=local_git)
        return None
    if word.endswith(_SCRIPT_EXT[:2]) and depth < 3:
        f = _script_file(words[0], cwd)
        if f and not _under_plugin(f):
            return classify(f.read_text(encoding="utf-8", errors="replace"), cwd=cwd, _depth=depth + 1, local_git=local_git)
    return None


_HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def _strip_heredocs(text: str) -> str:
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


def classify(cmd: str, cwd: str | None = None, _depth: int = 0, local_git: bool = False) -> str | None:
    """Refusal reason for a shell (Bash or PowerShell) command line or script text, or None if allowed.

    ``local_git``: git network commands are allowed because the caller already confines git to local
    repos (``sandbox_env`` sets ``GIT_ALLOW_PROTOCOL=file`` and removes the credential helper).
    """
    if _CRED.search(cmd):
        return "it touches a credential store, a token variable or a cloud metadata endpoint"
    if _PIPE_SH.search(cmd):
        return "piping a download into a shell (or Invoke-Expression) runs unreviewed code"
    body = _strip_heredocs(cmd)
    for simple in _SPLIT.split(body):
        r = _simple(simple.strip(), body, cwd, _depth, local_git)
        if r:
            return r
    return None


def snippet_reason(lang: str, code: str) -> str | None:
    """Refusal reason for a ``{run}``/``{compile}`` fence, or None."""
    lang = (lang or "").lower()
    if lang in ("bash", "sh", "shell", "zsh", "console", "powershell", "pwsh", "ps1", "bat", "cmd"):
        return classify(code, local_git=True)
    if lang in ("python", "py", "python3"):
        return _python_source(code)
    return _source_reason(code)


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
