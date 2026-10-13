# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "faster-whisper>=1.1",
#     "nvidia-cublas-cu12; (sys_platform == 'win32' or sys_platform == 'linux') and (platform_machine == 'AMD64' or platform_machine == 'x86_64')",
#     "nvidia-cudnn-cu12==9.*; (sys_platform == 'win32' or sys_platform == 'linux') and (platform_machine == 'AMD64' or platform_machine == 'x86_64')",
#     "mlx-whisper; sys_platform == 'darwin' and platform_machine == 'arm64'",
#     "imageio-ffmpeg",
# ]
# ///
"""
Timestamped transcript of a lecture recording, from its own audio -- Whisper, run locally.

Class transcripts often carry no timestamps, so a frame cannot be tied to what was being said.
This writes <out>.json, which keyframes.py takes via --whisper: each frame then gets the exact
speech around it and transcript.md gets frame markers inline.

Backends (--backend auto picks the first that works):
  * mlx            Apple Silicon only, mlx-whisper, large-v3-turbo on the GPU.      [unverified until
                   run on an Apple-Silicon Mac -- written against the mlx-whisper API]
  * faster-whisper CUDA float16 (NVIDIA, Windows or Linux), batch 16: minutes per class.  Windows
                   verified; Linux preload path [unverified until run on Linux].
  * faster-whisper CPU int8, batch 4: same text, ~1 h+ for a 2 h class.
VAD is on for faster-whisper (silence becomes a gap, not a "you you you" hallucination run).
Runs entirely locally -- no Claude tokens, no upload.

Exit codes: 0 ok · 1 failure · 2 usage / requested backend unavailable · 3 --require-fast and only
the CPU is available (or the fast backend failed).

Run:  uv run transcribe.py <video> --out whisper.json
      uv run transcribe.py <video> --out whisper.json --device cpu         # force the CPU path
      uv run transcribe.py <video> --out whisper.json --require-fast       # exit 3 instead of CPU
"""
from __future__ import annotations

import argparse
import gc
import ctypes
import importlib
import importlib.util
import inspect
import json
import os
import pathlib
import platform
import subprocess
import sys
import time
from collections import namedtuple
from typing import Any

# line-buffered so a backgrounded run still shows its progress live
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
# the model download is ~1.6 GB once; Windows can't symlink the HF cache, which is harmless
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

MODEL = "large-v3-turbo"
MLX_REPO = "mlx-community/whisper-{model}"      # mlx-community/whisper-large-v3-turbo
EXIT_FAILURE, EXIT_USAGE, EXIT_NOT_FAST = 1, 2, 3
Word = namedtuple("Word", "word start end")


def cuda_libraries() -> None:
    """The pip CUDA wheels drop cuBLAS/cuDNN in site-packages/nvidia/<lib>/{bin,lib}; ctranslate2
    only looks on the loader path.  Windows: add the DLL folders.  Linux/WSL: preload the .so."""
    for pkg in ("cublas", "cudnn"):
        try:
            mod = importlib.import_module(f"nvidia.{pkg}")
        except ImportError:
            continue
        for base in map(pathlib.Path, mod.__path__):
            for sub in ("bin", "lib"):
                d = base / sub
                if not d.is_dir():
                    continue
                if os.name == "nt":
                    os.add_dll_directory(str(d))
                    os.environ["PATH"] = f"{d}{os.pathsep}{os.environ.get('PATH', '')}"
                else:
                    for so in sorted(d.glob("lib*.so*")):
                        try:
                            ctypes.CDLL(str(so), mode=ctypes.RTLD_GLOBAL)
                        except OSError:
                            pass


def _word(w) -> Word:
    """faster-whisper words are objects, mlx-whisper words are dicts."""
    if isinstance(w, dict):
        return Word(w["word"], float(w["start"]), float(w["end"]))
    return Word(w.word, w.start, w.end)


def phrases(words) -> list[dict]:
    """Batched decoding returns ~30 s segments -- too coarse to tie a frame to a sentence.  Regroup
    the word timestamps into phrases: break at a pause, or at punctuation once a phrase is 6 s old,
    and never let one run past 15 s."""
    out: list[list] = []
    cur: list = []
    for w in map(_word, words):
        if cur and (w.start - cur[-1].end > 0.8 or w.end - cur[0].start > 15
                    or (w.start - cur[0].start > 6 and cur[-1].word.rstrip()[-1:] in ".?!,")):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return [{"start": round(c[0].start, 2), "end": round(c[-1].end, 2),
             "text": "".join(w.word for w in c).strip()} for c in out]


# ==========================================================================
# Backend choice
# ==========================================================================
def apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def mlx_importable() -> bool:
    return importlib.util.find_spec("mlx_whisper") is not None


def cuda_device_count() -> int:
    """Visible CUDA devices (CUDA_VISIBLE_DEVICES=-1 hides them all).  0 on any failure."""
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def choose(backend: str, device: str, *, mac_arm: bool, mlx_ok: bool, cuda_n: int):
    """-> (plan, error).  plan = {backend, device, compute, batch, fast}; pure, so it is testable."""
    cpu = {"backend": "faster-whisper", "device": "cpu", "compute": "int8", "batch": 4, "fast": False}
    gpu = {"backend": "faster-whisper", "device": "cuda", "compute": "float16", "batch": 16,
           "fast": True}
    mlx = {"backend": "mlx", "device": "metal", "compute": "float16", "batch": 0, "fast": True}
    if backend == "mlx":
        if not (mac_arm and mlx_ok):
            return None, "--backend mlx needs Apple Silicon and the mlx-whisper package"
        return mlx, None
    if backend == "auto" and device == "auto" and mac_arm and mlx_ok:
        return mlx, None
    if device == "cuda":
        if cuda_n <= 0:
            return None, "--device cuda: no CUDA device visible (is the NVIDIA GPU switched on?)"
        return gpu, None
    if device == "cpu":
        return cpu, None
    return (gpu if cuda_n > 0 else cpu), None


# ==========================================================================
# Decoders
# ==========================================================================
def run_faster_whisper(video: pathlib.Path, plan: dict, model_name: str, language: str | None,
                       hotwords: str | None) -> tuple[list[dict], float]:
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    # Decode with the bundled ffmpeg and hand over the samples: faster-whisper's own PyAV decode
    # breaks whenever `av` and faster-whisper drift apart (av 19 vs faster-whisper 1.2.1 did).
    audio = decode_audio(video)
    duration = len(audio) / 16000.0
    model = WhisperModel(model_name, device=plan["device"], compute_type=plan["compute"])
    pipe = BatchedInferencePipeline(model=model)
    wanted = {"language": language, "vad_filter": True, "beam_size": 5, "word_timestamps": True,
              "batch_size": plan["batch"], "hotwords": hotwords or None}
    accepted = inspect.signature(pipe.transcribe).parameters
    segments, info = pipe.transcribe(
        audio, **{k: v for k, v in wanted.items() if k in accepted and v is not None})
    out, report = [], 600.0
    for s in segments:                         # the generator is where decoding really happens
        if s.words:
            out += phrases(s.words)
        else:
            out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
        if s.end >= report:
            print(f"      {s.end / 60:5.0f} min of {duration / 60:.0f} transcribed")
            report += 600
    return out, duration


def decode_audio(video: pathlib.Path):
    """16 kHz mono float32 via the bundled ffmpeg -- what both Whisper backends take as input
    (mlx-whisper would otherwise need an ffmpeg on PATH)."""
    import imageio_ffmpeg
    import numpy as np
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(video), "-vn", "-sn",
           "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def run_mlx(video: pathlib.Path, model_name: str, language: str | None,
            hotwords: str | None) -> tuple[list[dict], float]:
    import mlx_whisper

    audio = decode_audio(video)
    repo = model_name if "/" in model_name else MLX_REPO.format(model=model_name)
    res = mlx_whisper.transcribe(
        audio, path_or_hf_repo=repo, language=language, word_timestamps=True,
        initial_prompt=hotwords or None, condition_on_previous_text=False,
        hallucination_silence_threshold=2.0, verbose=None)
    out: list[dict] = []
    for s in res.get("segments", []):
        if s.get("words"):
            out += phrases(s["words"])
        elif s.get("text", "").strip():
            out.append({"start": round(s["start"], 2), "end": round(s["end"], 2),
                        "text": s["text"].strip()})
    return out, len(audio) / 16000.0


# ==========================================================================
# main
# ==========================================================================
def main() -> None:
    if os.environ.get("NOTES_NO_VIDEO", "").strip() not in ("", "0"):
        print("video processing is disabled (NOTES_NO_VIDEO is set): nothing was run", flush=True)
        sys.exit(4)
    ap = argparse.ArgumentParser(description="Timestamped Whisper transcript of a lecture video.")
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, required=True, help="output JSON file")
    ap.add_argument("--backend", choices=("auto", "faster-whisper", "mlx"), default="auto")
    ap.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    ap.add_argument("--model", default=MODEL, help=f"Whisper model ({MODEL})")
    ap.add_argument("--hotwords", default="", help="course vocabulary as a decoding hint")
    ap.add_argument("--language", default="en", help="ISO code, or 'auto' to detect (en)")
    ap.add_argument("--require-fast", action="store_true",
                    help="exit 3 instead of falling back to the CPU")
    a = ap.parse_args()

    video, out = a.video.resolve(), a.out.resolve()
    if not video.is_file():
        print(f"error: no such video: {video}")
        sys.exit(EXIT_USAGE)
    language = None if a.language.lower() == "auto" else a.language
    hotwords = a.hotwords.strip() or None

    mac_arm = apple_silicon()
    mlx_ok = mac_arm and mlx_importable()
    cuda_n = 0
    if a.backend != "mlx" and a.device != "cpu":
        cuda_libraries()
        cuda_n = cuda_device_count()
    plan, err = choose(a.backend, a.device, mac_arm=mac_arm, mlx_ok=mlx_ok, cuda_n=cuda_n)
    if err:
        print(f"error: {err}")
        sys.exit(EXIT_USAGE)
    if not plan["fast"]:
        if a.device == "auto":
            print("no CUDA device visible (is the NVIDIA GPU switched on?) -- CPU only, "
                  "expect ~1 h for a 2 h class")
        if a.require_fast:
            print("--require-fast: no fast backend (CUDA / MLX) available -- exiting 3")
            sys.exit(EXIT_NOT_FAST)

    def decode(p: dict) -> tuple[list[dict], float]:
        if p["backend"] == "mlx":
            return run_mlx(video, a.model, language, hotwords)
        return run_faster_whisper(video, p, a.model, language, hotwords)

    def decode_shrinking(p: dict) -> tuple[list[dict], float]:
        """CUDA out of memory (8 GB laptop cards, long classes): retry on the GPU with a smaller batch
        before giving up on it.  Any other error, or OOM at batch 2, propagates."""
        while True:
            try:
                return decode(p)
            except Exception as e:
                oom = "out of memory" in str(e).lower()
                if not (oom and p["device"] == "cuda" and p["batch"] > 2):
                    raise
                p["batch"] //= 2
                gc.collect()
                print(f"CUDA out of memory -- retrying on the GPU with batch_size {p['batch']}", flush=True)

    def label(p: dict) -> str:
        return f"{p['backend']} on {p['device']} ({p['compute']})"

    print(f"transcribing {video.name} with {a.model}: {label(plan)}")
    t0 = time.time()
    try:
        segs, duration = decode_shrinking(plan)
    except Exception as e:                     # cuBLAS/cuDNN load errors surface mid-decode
        if not plan["fast"] or a.device != "auto" or a.backend == "mlx":
            raise                              # CPU already, or the caller insisted on this backend
        why = f"{type(e).__name__}: {str(e).splitlines()[0][:120] if str(e) else ''}"
        if a.require_fast:
            print(f"fast backend failed ({why}) -- --require-fast, exiting 3")
            sys.exit(EXIT_NOT_FAST)
        print(f"{plan['backend']}/{plan['device']} failed ({why}) -- retrying on CPU")
        plan, t0 = choose("faster-whisper", "cpu", mac_arm=mac_arm, mlx_ok=mlx_ok, cuda_n=0)[0], time.time()
        segs, duration = decode(plan)
    took = time.time() - t0

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    tmp.write_text(json.dumps({
        "video": video.name, "model": a.model, "backend": plan["backend"], "device": plan["device"],
        "compute_type": plan["compute"], "language": language or "auto", "hotwords": hotwords,
        "duration": round(duration, 2), "elapsed_s": round(took), "segments": segs},
        ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    os.replace(tmp, out)
    print(f"done: {len(segs)} segments, {duration / 60:.0f} min of audio in {took / 60:.1f} min "
          f"({duration / max(took, 1):.0f}x realtime) [{label(plan)}] -> {out}")


if __name__ == "__main__":
    main()
