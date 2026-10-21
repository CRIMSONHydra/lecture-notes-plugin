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
| Floor (code, `exec_policy.floor`) | Always blocks reading credentials or tokens, `wsl`, `sudo` and other privilege escalation, encoded PowerShell, environment dumps, and touching the plugin's own safety controls. No verdict overrides it. |
| Fast path (code, `exec_policy.fast_allow`) | Read-only commands (`ls`, `grep`, `cat`, `git log`, …) and the plugin's own `uv run` scripts pass at once. |
| Classifier (`exec_classifier`, Claude Haiku via `claude -p`) | Judges every other command and every `{run}` snippet against the two rules. It answers **allow**, **deny**, or **rewrite**: a rewrite comes back as "run exactly this instead", typically adding `--rm --name notes-… --memory 256m --cpus 0.5` and a label to a `docker run`. Run that exact command; it is pre-approved. Verdicts are cached. If the classifier is unavailable, the answer is deny. |
| Cleanup (`exec_cleanup`) | Every approval records what the command creates and how to remove it. When you stop, a hook runs those removals. It accepts only plain removals of `notes-*` objects or temp paths, then sweeps Docker objects labelled with the run. `guard.py clear` repeats the sweep for the whole run. |
| Snippets (`check_snippets.py`) | Classifies a note's fences in one batch before anything runs. A denied fence fails with the reason. Allowed fences run with credentials removed from the environment, git limited to local repositories, and Git Bash, never the WSL launcher, as `bash` on Windows. |

A block is final. **Do not look for another route to the same command.** That includes `bash -c`, a
script file, a Python `subprocess` call, `wsl`, `cmd /c`, a different binary name, a local copy of the
profile or the plugin, or editing a hook. Rewrite the snippet as representative output instead.

## What is typically denied

Terraform/OpenTofu beyond `fmt` and `version`; `aws`, `az`, `gcloud` and cloud SDK code; `kubectl`,
`helm`, `minikube` and `kind`; `ssh`, `gh`, and git against a URL; `docker login/push/compose/prune` and
uncontained `docker run`; package installs; `curl` that sends data; `--help` pages that open a browser;
anything heavy or long-running. The classifier judges by the two rules, not by this list. A command not
listed here can still be denied, and a contained Docker demo on a small image can be allowed.

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
