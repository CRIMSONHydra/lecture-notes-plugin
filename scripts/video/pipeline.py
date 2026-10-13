# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Lecture recording -> timestamped speech + deduplicated keyframes, in one command.

Runs transcribe.py (Whisper) and keyframes.py (screen -> frames) as separate `uv run` steps -- each
has its own dependencies -- and records what happened in <out>/pipeline.json.

  <out>/whisper.json     timestamped speech (transcribe.py)           [absent when frames-only]
  <out>/frames/          index.md, NNN_*.jpg, frames.json, meta.json, frames.pdf, transcript.md
  <out>/frames/hires/    native-resolution twin of every NNN_*.jpg (figure source; agents never read it)
  <out>/pipeline.json    timings, backend, device, OCR provider, decisions
  <out>/logs/*.log       full output of each step

An audio-only input (.m4a .mp3 .wav .aac .ogg .flac) runs Whisper only: no keyframes. --transcript is optional.

Whisper policy (--whisper):
  auto   run it only if a fast backend exists (CUDA GPU / Apple-Silicon MLX): transcribe.py is
         started with --require-fast, and exit code 3 means "CPU only" -> frames-only, with a
         notice.  Speech then comes from --transcript (proportional hints), if given.
  force  run it even on the CPU (~1 h for a 2 h class).
  skip   never run it.
--reuse skips a step whose output already exists *and* belongs to this video.  A reused frames/ that
lacks hires/ (older run) gets only its hires copies regrabbed from the video (keyframes.py --hires-only,
CPU ffmpeg, no GPU lock).

GPU lock (PLAN 16.2): Whisper and OCR share one GPU, so a run that has real work to do first takes the
cross-process lock <data>/locks/gpu.lock (--lock-dir DIR overrides <data>/locks, --no-gpu-lock skips it) and
waits for any other pipeline to finish. Stale locks (owner PID dead) are taken over. Reused steps never wait.

Exit codes: 0 ok (frames-only because there is no fast backend is still ok) · 1 a step failed.

Run:  uv run pipeline.py <video> --out DIR [--transcript TXT] [--hotwords S] [--language en]
                         [--whisper auto|force|skip] [--device auto|cuda|cpu]
                         [--ocr-device auto|gpu|cpu] [--reuse] [--keep-cache]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import time
import atexit

# line-buffered so a backgrounded run still shows its progress live
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from notes_lib import hires as hr  # noqa: E402
from notes_lib import platform as plat  # noqa: E402
from notes_lib.lock import FileLock  # noqa: E402
EXIT_NOT_FAST = 3


def find_uv() -> str:
    """uv exports its own path as $UV inside `uv run`; otherwise PATH, then the default install."""
    for c in (os.environ.get("UV"), shutil.which("uv"),
              str(pathlib.Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"))):
        if c and pathlib.Path(c).exists():
            return c
    sys.exit("error: `uv` not found (https://docs.astral.sh/uv/getting-started/installation/)")


def run_step(name: str, cmd: list[str], log: pathlib.Path) -> tuple[int, float]:
    """Run a child, echo its output live (prefixed) and keep it in a log file."""
    print(f"[{name}] $ {' '.join(cmd[3:] if len(cmd) > 3 else cmd)}", flush=True)
    t0 = time.time()
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    with open(log, "w", encoding="utf-8", newline="\n") as lf:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", bufsize=1, env=env)
        assert p.stdout is not None
        for line in p.stdout:
            line = line.rstrip("\r\n")
            print(f"  [{name}] {line}", flush=True)
            lf.write(line + "\n")
            lf.flush()
        rc = p.wait()
    return rc, time.time() - t0


def load_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".flac"}


def whisper_ok(path: pathlib.Path, video: pathlib.Path) -> dict | None:
    """The Whisper JSON, if it is complete and made from this video."""
    d = load_json(path) if path.is_file() else None
    if isinstance(d, dict) and isinstance(d.get("segments"), list) and d.get("video") == video.name:
        return d
    return None


def frames_ok(frames: pathlib.Path, video: pathlib.Path, want_whisper: bool) -> dict | None:
    """meta.json of finished keyframes of this video (and, if Whisper exists now, made with it)."""
    meta = load_json(frames / "meta.json") if (frames / "meta.json").is_file() else None
    if not (isinstance(meta, dict) and (frames / "index.md").is_file()
            and (frames / "frames.json").is_file()):
        return None
    if meta.get("video") != video.name or meta.get("video_bytes") != video.stat().st_size:
        return None
    if want_whisper and meta.get("speech") != "whisper":
        return None
    return meta


def main() -> None:
    if os.environ.get("NOTES_NO_VIDEO", "").strip() not in ("", "0"):
        print("video processing is disabled (NOTES_NO_VIDEO is set): nothing was run", flush=True)
        sys.exit(4)
    ap = argparse.ArgumentParser(description="Recording -> Whisper transcript + keyframes.")
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, required=True, help="output directory")
    ap.add_argument("--transcript", type=pathlib.Path,
                    help="plain-text class transcript (proportional speech hints without Whisper)")
    ap.add_argument("--hotwords", default="", help="course vocabulary as a Whisper decoding hint")
    ap.add_argument("--language", default="en")
    ap.add_argument("--whisper", choices=("auto", "force", "skip"), default="auto")
    ap.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto", help="Whisper device")
    ap.add_argument("--backend", choices=("auto", "faster-whisper", "mlx"), default="auto")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--ocr-device", choices=("auto", "gpu", "cpu"), default="auto")
    ap.add_argument("--reuse", action="store_true", help="skip steps whose output already exists")
    ap.add_argument("--existing-whisper", type=pathlib.Path, default=None,
                    help="an earlier whisper.json of this video (read-only): copied to <out>/whisper.json and reused")
    ap.add_argument("--keep-cache", action="store_true", help="keep keyframes' survey + OCR cache")
    ap.add_argument("--no-gpu-lock", action="store_true", help="do not serialise on <data>/locks/gpu.lock")
    ap.add_argument("--lock-dir", type=pathlib.Path, default=None, help="lock directory (default <data>/locks)")
    ap.add_argument("--no-pdf", action="store_true", help="skip frames.pdf")
    a = ap.parse_args()

    video, out = a.video.resolve(), a.out.resolve()
    if not video.is_file():
        sys.exit(f"error: no such video: {video}")
    (out / "logs").mkdir(parents=True, exist_ok=True)
    audio_only = video.suffix.lower() in AUDIO_EXT
    uv = find_uv()
    whisper_json, frames_dir = out / "whisper.json", out / "frames"
    t_all = time.time()
    rec: dict = {
        "schema": 1, "video": video.name, "video_bytes": video.stat().st_size, "out": str(out),
        "started": dt.datetime.now().isoformat(timespec="seconds"),
        "platform": {"os": sys.platform, "machine": platform.machine(),
                     "python": platform.python_version()},
        "options": {k: (str(v) if isinstance(v, pathlib.Path) else v) for k, v in vars(a).items()},
        "steps": {}, "decisions": [], "warnings": [], "ok": True}

    def save() -> None:
        rec["total_s"] = round(time.time() - t_all, 1)
        rec["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        (out / "pipeline.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False),
                                           encoding="utf-8", newline="\n")

    lock_dir = a.lock_dir or (plat.default_data_dir() / "locks")
    gpu_lock = FileLock(lock_dir / "gpu.lock", timeout=None, stale_after=None, poll=1.0,
                        on_wait=lambda o: print(f"[gpu-lock] waiting for another pipeline (pid {(o or {}).get('pid')})",
                                                flush=True))
    atexit.register(gpu_lock.release)

    def need_gpu() -> None:
        """Take the GPU lock before the first step that really runs (idempotent)."""
        if a.no_gpu_lock or gpu_lock.held:
            return
        gpu_lock.acquire()
        rec["gpu_lock"] = {"path": str(gpu_lock.path), "waited_s": round(gpu_lock.waited, 1)}
        print(f"[gpu-lock] acquired after {gpu_lock.waited:.1f}s", flush=True)

    def decide(msg: str) -> None:
        rec["decisions"].append(msg)
        print(f"[decision] {msg}", flush=True)

    print(f"pipeline: {video.name} -> {out}  (whisper={a.whisper}, device={a.device}, "
          f"ocr={a.ocr_device}, reuse={a.reuse})", flush=True)

    # ---- 1. Whisper ---------------------------------------------------------------------------
    if a.existing_whisper and a.whisper != "skip" and not whisper_ok(whisper_json, video):
        if a.existing_whisper.is_file() and whisper_ok(a.existing_whisper, video):
            import shutil
            shutil.copyfile(a.existing_whisper, whisper_json)
            decide(f"whisper: adopted {a.existing_whisper.name} (existing output for this video)")
        else:
            decide(f"whisper: --existing-whisper {a.existing_whisper} ignored (missing or not from this video)")
    a.reuse = a.reuse or bool(a.existing_whisper and whisper_ok(whisper_json, video))
    step: dict = {"output": str(whisper_json)}
    rec["steps"]["transcribe"] = step
    if a.whisper == "skip":
        step.update(status="skipped", reason="--whisper skip")
        decide("whisper: skipped (--whisper skip)")
    elif a.reuse and (w := whisper_ok(whisper_json, video)):
        step.update(status="reused", reason="whisper.json exists for this video")
        decide("whisper: reused existing whisper.json")
    else:
        cmd = [uv, "run", "--script", str(HERE / "transcribe.py"), str(video),
               "--out", str(whisper_json), "--backend", a.backend, "--device", a.device,
               "--model", a.model, "--language", a.language]
        if a.hotwords:
            cmd += ["--hotwords", a.hotwords]
        if a.whisper == "auto":
            cmd += ["--require-fast"]
        need_gpu()
        rc, secs = run_step("transcribe", cmd, out / "logs" / "transcribe.log")
        step.update(exit_code=rc, seconds=round(secs, 1))
        if rc == 0 and (w := whisper_ok(whisper_json, video)):
            step.update(status="ok")
            decide(f"whisper: ran ({w.get('backend')} on {w.get('device')}, {w.get('compute_type')})")
        elif rc == EXIT_NOT_FAST:
            step.update(status="unavailable",
                        reason="no fast backend (no CUDA GPU / MLX) -- frames-only")
            decide("whisper: no fast backend (exit 3) -> frames-only; speech column falls back to "
                   "--transcript proportional hints" if a.transcript else
                   "whisper: no fast backend (exit 3) -> frames-only, no speech column "
                   "(pass --transcript for proportional hints; --whisper force runs the CPU)")
        else:
            step.update(status="failed", reason=f"transcribe.py exited {rc}")
            rec["ok"] = False
            rec["warnings"].append(f"transcribe.py failed (exit {rc}); see logs/transcribe.log -- "
                                   "continuing with frames only")
            decide(f"whisper: FAILED (exit {rc}) -> continuing frames-only")
    w = whisper_ok(whisper_json, video)
    if w:
        step.update(backend=w.get("backend"), device=w.get("device"), compute_type=w.get("compute_type"),
                    model=w.get("model"), segments=len(w["segments"]), audio_s=w.get("duration"),
                    elapsed_s=w.get("elapsed_s"))
    save()

    # ---- 2. Keyframes -------------------------------------------------------------------------
    step = {"output": str(frames_dir)}
    rec["steps"]["keyframes"] = step
    meta = frames_ok(frames_dir, video, want_whisper=bool(w)) if a.reuse else None
    if audio_only:
        step.update(status="skipped", reason="audio-only input: no frames")
        decide("keyframes: skipped (audio-only input)")
        if not w:
            rec["ok"] = False
            rec["warnings"].append("audio-only input has no speech without Whisper: re-run with --whisper force")
        meta = None
    elif meta:
        step.update(status="reused", reason="frames/ exists for this video")
        decide("keyframes: reused existing frames/")
        hs = hr.status(frames_dir)
        if hs["missing"]:
            cmd = [uv, "run", "--script", str(HERE / "keyframes.py"), str(video), "--out", str(frames_dir),
                   "--hires-only"]
            rc, secs = run_step("hires", cmd, out / "logs" / "hires.log")
            hs = hr.status(frames_dir)
            step["hires"] = {"exit_code": rc, "seconds": round(secs, 1), "have": hs["have"], "total": hs["total"]}
            decide(f"hires: regrabbed {hs['have']}/{hs['total']} native-resolution frames for the reused frames/"
                   if rc == 0 else f"hires: regrab FAILED (exit {rc}); figures fall back to the low-res frames")
    else:
        cmd = [uv, "run", "--script", str(HERE / "keyframes.py"), str(video), "--out", str(frames_dir),
               "--ocr-device", a.ocr_device]
        if w:
            cmd += ["--whisper", str(whisper_json)]
        if a.transcript:
            cmd += ["--transcript", str(a.transcript.resolve())]
        if a.keep_cache:
            cmd += ["--keep-cache"]
        if a.no_pdf:
            cmd += ["--no-pdf"]
        need_gpu()
        rc, secs = run_step("keyframes", cmd, out / "logs" / "keyframes.log")
        step.update(exit_code=rc, seconds=round(secs, 1))
        meta = load_json(frames_dir / "meta.json") if rc == 0 else None
        if meta:
            step.update(status="ok")
        else:
            step.update(status="failed", reason=f"keyframes.py exited {rc}")
            rec["ok"] = False
            decide(f"keyframes: FAILED (exit {rc}); see logs/keyframes.log")
    if meta:
        step.update(frames=meta.get("frames"), detail_crops=meta.get("detail_crops"),
                    image_tokens_k=meta.get("image_tokens_k"), ocr_provider=meta.get("ocr_provider"),
                    speech=meta.get("speech"), keyframes_elapsed_s=meta.get("elapsed_s"))
    save()

    # ---- summary ------------------------------------------------------------------------------
    t, k = rec["steps"]["transcribe"], rec["steps"]["keyframes"]
    print("\n=== pipeline summary ===")
    print(f"whisper   : {t['status']}"
          + (f" - {t.get('backend')} on {t.get('device')} ({t.get('compute_type')}), "
             f"{t.get('segments')} segments" if t.get("segments") else f" - {t.get('reason', '')}"))
    print(f"keyframes : {k['status']}" + (f" - {k.get('reason')}" if k['status'] == 'skipped' else "")
          + (f" - {k.get('frames')} frames ({k.get('detail_crops')} detail crops), "
             f"ocr={k.get('ocr_provider')}, speech={k.get('speech')}" if k.get("frames") is not None else ""))
    print(f"total     : {rec['total_s']:.0f}s -> {out / 'pipeline.json'}", flush=True)
    sys.exit(0 if rec["ok"] else 1)


if __name__ == "__main__":
    main()
