---
name: doctor
description: Check the environment for the notes plugin (uv, Chrome or Edge for PDFs, GPU and Whisper/OCR acceleration, GitHub reachability, compilers, cached KaTeX and Mermaid, the read-only guard), optionally install a fallback Chromium or clear a stale guard. Use when a PDF build cannot find a browser, a run left the guard stuck, or the user asks whether the setup is ready.
argument-hint: "[install-chromium] [clear-guard [run id]]"
allowed-tools: Bash(uv run *) Read
---

# /notes:doctor

Arguments: `$ARGUMENTS`

Commands are `uv run` with quoted paths, one per Bash call, with no `&&`, pipes or `$()`.
`${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` are substituted into this text and are NOT shell
environment variables: keep them inside the quoted command as written.

## Steps

1. **Parse** `$ARGUMENTS` for the two optional words `install-chromium` and `clear-guard` (optionally followed by
   a run id).
2. **Install first if asked.** With `install-chromium` (a download of about 150 MB, so only on request):
   `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/install_chromium.py" --data-dir "${CLAUDE_PLUGIN_DATA}"`
3. **Run the doctor.**
   `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" --data-dir "${CLAUDE_PLUGIN_DATA}" --json "${CLAUDE_PLUGIN_DATA}/doctor.json"`
   With `clear-guard` add `--clear-guard` to that command (`clear-guard <run id>` adds `--clear-guard "<run id>"`:
   needed when several runs are guarded, since each run has its own guard). Exit code 1 only means no browser was found; it is a
   finding, not a crash.
4. **Read** `${CLAUDE_PLUGIN_DATA}/doctor.json` and present one table: item, status (ok, advisory, problem), what
   it means, what to do. Cover these keys:
   - `uv`, `os`/`arch`: informational.
   - `chrome`: required for PDFs. If `ok` is false, say so plainly and offer `/notes:doctor install-chromium`
     (or installing Chrome, Edge or Chromium); on Linux the fallback may also need
     `playwright install-deps chromium`.
   - `gpu`, `whisper_fast`, `ocr_accel`: tell the user what the video step will do. An NVIDIA GPU that is
     visible gives fast Whisper (about 70 times realtime). If `gpu.nvidia` is false on Windows or Linux
     and `advice[]` says to switch the NVIDIA GPU on, repeat that advice: with the GPU off a lecture
     run skips Whisper (keyframes still work from the transcript) or, if forced, takes about an hour on CPU.
     On Apple Silicon the MLX backend is used; elsewhere CPU. These are heuristics, not measurements.
   - `network.github`: needed to fetch the instructor's code; show `rate_remaining` and mention the
     `GITHUB_TOKEN` environment variable if it is low.
   - `compilers`: used to run `{run}` snippets in notes; a missing one means those snippets are skipped, not
     failed.
   - `assets`: KaTeX and Mermaid are downloaded on the first PDF build that needs them and cached; offline
     builds need one prior online build.
   - `guard`: `guards[]` lists every active run guard (one per run, possibly from other projects or sessions).
     One whose run is no longer in progress is stale and blocks Write and Edit under that project's `resources/`
     until it expires (6 hours): offer `/notes:doctor clear-guard <run id>` for it, and never clear a guard
     whose run may still be going. If the user passed `clear-guard`, confirm it was cleared.
5. **Project check (optional).** If the working directory is inside a project (a parent holds
   `.notes-plugin-internal/profile.md`), say so and name the profile id; otherwise mention `/notes:init --profile <id>`. If the doctor printed that it migrated a legacy `.notes/` folder to `.notes-plugin-internal/`, say so.
6. **End with a verdict** in one line: ready for `/notes:lecture`, or the single next action.
