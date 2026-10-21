# Execution policy — what you may run (binding on every agent)

The machine running a notes job is the student's own computer. It usually holds **live cloud, GitHub,
registry and cluster credentials**. Notes are written from the transcript, screen notes, instructor code
and official documentation. They are **not** produced by re-running the class. Running a command to
"capture real output" is a convenience. It is never a reason to touch real infrastructure, spend the
student's money, start heavy software, or open windows on their screen.

The policy is enforced twice, and both checks are active while a notes run's guard is set:

- `scripts/exec_guard_hook.py` blocks your Bash and PowerShell calls. It fails closed: when it cannot
  check a command, the command is blocked.
- `check_snippets.py` refuses to execute a forbidden `{run}`/`{compile}` fence and fails it. It runs the
  allowed fences with cloud, cluster, registry and git credentials removed from the environment, git
  limited to local repositories, and Git Bash, never WSL, as `bash` on Windows.

A refusal is final. **Do not look for another route to the same command.** That includes `bash -c`, a
script file, a Python `subprocess` call, `wsl`, `cmd /c`, an encoded PowerShell command, a different
binary name, or editing the hook. Rewrite the snippet as representative output instead.

## Never run

| Category | Examples |
| --- | --- |
| Cloud CLIs and SDK code | `aws`, `az`, `gcloud`, `eksctl`, `sam`, `cdk`, `pulumi`, `doctl`; code that imports `boto3`, `azure`, `google.cloud`, `kubernetes`, `docker`, `paramiko` |
| Infrastructure as code | `terraform`/`tofu` other than `fmt` and `version`. No `init` (downloads providers), `plan`, `apply`, `destroy`, `import`, `state` or `login`. `terragrunt`, `ansible*`, `vagrant`, `packer` |
| Clusters | `kubectl`, `helm`, `minikube`, `kind`, `k3d`, `kubeadm`, `argocd`, `flux`, `istioctl`, `kustomize` |
| Remote access and accounts | `ssh`, `scp`, `sftp`, `ssh-keygen`, `telnet`, `nc`, `gh`, `hub`, `glab`; `git clone/fetch/pull/push/ls-remote` against any URL; `git <cmd> --help` and `git help` (they open a browser page on Windows); `git credential*` |
| Host changes | `wsl`, `sudo`, `su`, `runas`; `apt`, `yum`, `dnf`, `brew`, `choco`, `winget`, `scoop`, `pip install`, `npm -g`, `cargo install`; `systemctl`, `service`, `crontab`, `schtasks`, `reg`, `setx`; power and disk commands |
| Windows, browsers, GUIs | `start`, `explorer`, `Start-Process`, `Invoke-Item`, `xdg-open`, `open`, `rundll32` |
| Secrets | anything under `~/.aws`, `~/.kube`, `~/.ssh`, `~/.azure`, `~/.docker/config.json`, `.git-credentials`, `.netrc`; `printenv`, bare `env`, token variables; cloud metadata endpoints (`169.254.169.254`) |
| Traffic | `nmap`, `masscan`, `hping3`; load tools such as `ab`, `hey`, `wrk`, `k6`, `locust`; `curl`/`wget`/`Invoke-WebRequest` that send data or auth (`-X POST/PUT/DELETE`, `-d`, `-F`, `-T`, `-u`, auth headers); `curl … \| sh` and `iex (iwr …)` |
| Other container engines | `podman`, `nerdctl`, `buildah`, `docker compose`/`docker-compose` |

## Docker, contained form only

Docker is allowed for small, short demos, and only in this form:

- **Images:** only `hello-world`, `alpine`, `busybox`, `nginx`, `httpd`, `redis`, `python`, `node`,
  `ubuntu`, `debian`, `curlimages/curl` and `traefik/whoami`, any tag. Prefer `-alpine` and `-slim` tags.
- **`docker run` must have all of:** `--rm`, `--name notes-<something>`, `--memory` of at most 512m and
  `--cpus` of at most 1.
- **Ports:** only `-p 127.0.0.1:HOST:CONTAINER`. No `-P`.
- **Host access:** none. No bind mounts (`-v /path:…`), no Docker socket, no `--privileged`, no
  `--network host`, no `--pid/--ipc/--userns host`, no `--cap-add`, no `--device`, no `--gpus`, no
  `--platform`, no `--restart`.
- **Detached containers (`-d`):** stop and remove them in the **same** command, for example
  `docker run -d … --name notes-web nginx:alpine; sleep 1; docker port notes-web; docker rm -f notes-web`.
- **Builds:** `docker build -t notes-<name> .` only, from a tiny Dockerfile on an allowlisted base. No
  `--push` and no `buildx`. Remove the image afterwards with `docker rmi notes-<name>`.
- **Cleanup commands:** `rm`, `stop`, `kill`, `exec`, `cp`, `rmi`, and `volume`/`network` `create` and
  `rm` only on objects named `notes-*`. Never touch the student's own containers, images or volumes. No
  `prune`, `system`, `login`, `push`, `tag`, `save`, `load`, `commit`, `context` or `-H`.
- **Read-only commands are fine:** `version`, `info`, `ps`, `images`, `inspect`, `logs`, `port`,
  `stats --no-stream`.

If Docker Desktop is not running, do not start it. Write the representative output instead.

## Allowed

- The plugin's own scripts (`uv run "<plugin>/scripts/…"`). Python only through `uv run`.
- Snippets that are plain text processing, pure Python, compiled toy programs, and file or permission
  demos inside a temporary directory.
- `git` on a **local** throwaway repository. A local bare repo can stand in for GitHub; prefix network
  subcommands with `GIT_ALLOW_PROTOCOL=file` (snippets get this automatically).
- Read-only lookups that need no credentials: `curl -sI https://docs.…`, `nslookup` or `dig` of a public
  name, `ping -c 2`, `traceroute -m 5`.
- `terraform fmt` and `terraform version`.

Keep everything small and short. Each snippet finishes in seconds, uses no more than a few hundred MB,
writes only under its snippet directory or a temp directory, and leaves nothing running.

## What to write instead

A snippet using a forbidden tool goes in a normal fence **without** `{run}`/`{compile}`. It is followed by
a `text` block introduced as representative output, built from the transcript, the screen notes, the
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
