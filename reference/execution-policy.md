# Execution policy — what you may run (binding on every agent)

The machine running a notes job is the student's own computer. It usually holds **live cloud, GitHub,
registry and cluster credentials**, plus the student's own files, containers and images. Notes are written
from the transcript, screen notes, instructor code and official documentation. They are **not** produced by
re-running the class. Running a command to capture real output is a convenience, never a requirement.

## The two rules

1. **No cost.** Nothing may create, change or bill a cloud or SaaS resource, call a paid API, use an
   account, log in, push, publish or send anything. Anonymous read-only GETs of public docs are fine.
2. **Light load.** A command finishes in seconds, stays within about 1 CPU and 512 MB, writes little and
   only under a temp or snippet directory, starts no daemons, VMs, clusters, GUIs or browsers, and leaves
   nothing running. If Docker Desktop is not already running, do not start it.

A third rule follows from both: **change nothing of the student's.** Do not touch their files, repos,
remotes, containers, images, packages or services.

## How it is enforced (while a run's guard is set)

| Layer | What it does |
| --- | --- |
| Floor (code, `exec_policy.floor`) | Always blocks reading credentials or tokens, `sudo` and other privilege escalation, encoded PowerShell, environment dumps, and touching the plugin's own safety controls. `wsl …`, `bash -c …` and `pwsh -Command …` are unwrapped first, so the command they carry is what gets checked. No verdict overrides the floor. |
| Fast path (code, `exec_policy.fast_allow`) | Read-only commands (`ls`, `grep`, `cat`, `git log`, …), also inside `wsl`, and the plugin's own `uv run` scripts pass at once. |
| Classifier (`exec_classifier`, Claude Haiku 5.5 via `claude -p`) | Judges every other command and every `{run}` snippet against the two rules. It answers **allow**, **deny**, or **rewrite**: a rewrite comes back as "run exactly this instead". Typical rewrites add `--rm --name notes-… --memory 256m --cpus 0.5` and a label to a `docker run`, disable credential prompts on network git and `gh` calls, or fix the toolchain (below). Run that exact command; it is pre-approved. Verdicts are cached. If the classifier is unavailable, the answer is deny. |
| Cleanup (`exec_cleanup`) | Every approval records what the command creates and how to remove it. When you stop, a hook carries out those removals: only `notes-*` Docker objects and paths inside the temp or work directory. Files are removed in Python, so this works on any OS (Git Bash `/tmp` and `/c/…` paths are mapped). Things created inside WSL are removed inside WSL. Then Docker objects labelled with the run are swept. `guard.py clear` repeats the sweep for the whole run. |
| Snippets (`check_snippets.py`) | Classifies a note's fences in one batch before anything runs. A denied fence fails with the reason. Allowed fences run with credentials removed from the environment, git limited to local repositories, and Git Bash, never the WSL launcher, as `bash` on Windows. |

A block is final. **Do not look for another route to the same command.** That includes `bash -c`, a
script file, a Python `subprocess` call, `wsl`, `cmd /c`, a different binary name, a local copy of the
profile or the plugin, or editing a hook. Rewrite the snippet as representative output instead.

## Course material from GitHub

Reading course material is allowed: anonymous downloads (`curl`, `Invoke-WebRequest`), shallow clones into a
temp or work directory, and **read-only `gh`** (`gh api` GETs, `gh repo view`, `gh repo clone`,
`gh release download`, `gh run view`) on repos the course references. The plugin collects those from the
profile, the text files in `resources/` and the notes, and hands them to the classifier. Anything that
writes to GitHub (`gh pr/issue/release create`, `gh repo create/delete/fork/edit`, `gh api -X POST …`,
secrets, `git push` to a real remote) is denied. Network git and `gh` calls get credential prompts
disabled, so a typo fails instead of opening a login window.

## Toolchain and platforms

Commands are judged for the machine they run on (Windows, macOS or Linux; Bash or PowerShell). The
classifier is told how tools resolve here, probed once a day:

- Python behind uv, so a bare `python` becomes `uv run python …`.
- Node behind fnm, nvm or volta, so `node …` becomes `fnm exec --using default node …`.
- Tools that exist only inside WSL, so `gh …` becomes `wsl gh …`. On Windows, a command that is better run
  in Linux can run as `wsl … <command>`; the command inside is judged like any other.
- Git Bash rewriting `/path` arguments for Windows programs, so `MSYS_NO_PATHCONV=1` is added.

A rewrite that fixes the toolchain is returned like any other rewrite: run the exact command given.

## What is typically denied

Terraform/OpenTofu beyond `fmt` and `version`; `aws`, `az`, `gcloud` and cloud SDK code; `kubectl`,
`helm`, `minikube` and `kind` against a cluster; `ssh` to other hosts; anything that writes to GitHub;
`docker login/push/compose/prune` and uncontained `docker run`; WSL management (`--shutdown`,
`--unregister`, …); package installs; `curl` that sends data; `--help` pages that open a browser; anything
heavy or long-running. The classifier judges by the two rules, not by this list. A command not listed here
can still be denied, and a contained Docker demo on a small image can be allowed.

## What to write instead

A snippet that may not run goes in a normal fence **without** `{run}`/`{compile}`. It is followed by a
`text` block introduced as representative output, built from the transcript, the screen notes, the
instructor code and the official docs:

````markdown
```bash
terraform plan -out tfplan
```

Representative output (from the class demo and the Terraform docs; not run on this machine):

```text
Plan: 3 to add, 0 to change, 0 to destroy.
```
````

Never claim that representative output was captured live, and never invent a flag or a field to make it
look real. In your return, list the snippets you left as representative (`snippets run/representative`).

## Setup

The classifier needs the Claude Code CLI, logged in (`claude` once, interactively). The plugin finds it on
PATH, under fnm or npm, or at `LECTURE_NOTES_CLAUDE`. It runs `claude-haiku-5-5` by default
(`LECTURE_NOTES_CLASSIFIER_MODEL` overrides it), with no tools, no settings, no MCP servers and a $0.10 cap per
call.
