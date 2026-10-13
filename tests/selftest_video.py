# /// script
# requires-python = ">=3.11"
# dependencies = ["imageio-ffmpeg", "numpy", "pymupdf", "pillow"]
# ///
"""
Self-test for scripts/video/{pipeline,transcribe,keyframes}.py.

    uv run tests/selftest_video.py                  # unit tests (seconds) + regression compare if present
    uv run tests/selftest_video.py --smoke          # + the 2 MB Templates clip through every CLI path
    uv run tests/selftest_video.py --regress        # + the P&R recording through pipeline.py (needs a
                                                    #   fast Whisper backend; ~6-8 min on a laptop GPU)
    options: --work DIR (default test-output/_r1/video)  --cpp-repo DIR (default $LECTURE_NOTES_CPP_REPO or
             <none> -- read-only; smoke/regress skip when it is absent)

The subject repo is only ever read: a stat snapshot of it is taken before and after the smoke and
regression runs and must be identical ("nothing written outside --out").
Exit 0 = every check passed (skips are listed, not hidden); 1 = a check failed.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import types
from contextlib import redirect_stdout

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

PLUGIN = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = PLUGIN / "scripts" / "video"
DEFAULT_REPO = "/nonexistent/cpp-subject"
TEMPLATES = "Generic_Containers_-_Class_Scaler_Academy"
PR = "Memory_Layout_-_Class_Scaler_Academy"

RESULTS: list[tuple[str, str, str]] = []          # (status, name, detail)


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append(("PASS" if cond else "FAIL", name, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)
    return cond


def skip(name: str, why: str) -> None:
    RESULTS.append(("SKIP", name, why))
    print(f"  [SKIP] {name}  -- {why}", flush=True)


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ==========================================================================
# Unit tests
# ==========================================================================
def test_transcribe(tr) -> None:
    print("transcribe.py")
    W = lambda w, s, e: types.SimpleNamespace(word=w, start=s, end=e)   # noqa: E731
    obj = [W(" hello", 0, .4), W(" world.", .5, .9), W(" next", 3.0, 3.3), W(" bit", 3.4, 3.8)]
    out = tr.phrases(obj)
    check("phrases: a 0.8 s+ pause starts a new phrase", len(out) == 2 and out[0]["text"] == "hello world."
          and out[1]["text"] == "next bit", str(out))
    # dict words (what mlx-whisper returns), no pause anywhere: only the "6 s old + punctuation" rule fires
    dic = [{"word": f" w{i}" + ("," if i == 13 else ""), "start": i * 0.5, "end": i * 0.5 + 0.45}
           for i in range(20)]
    ph = tr.phrases(dic)
    check("phrases: dict words (mlx); a comma after 6 s ends the phrase",
          len(ph) == 2 and ph[0]["text"].endswith("w13,") and ph[1]["text"].startswith("w14"), str(ph))
    long = [W(f" w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(60)]            # 30 s, no pauses
    check("phrases: no phrase runs past 15 s", all(p["end"] - p["start"] <= 15.5 for p in tr.phrases(long)))

    cases = [  # backend, device, mac_arm, mlx_ok, cuda_n -> (backend, device, fast) or None
        (("auto", "auto", False, False, 1), ("faster-whisper", "cuda", True)),
        (("auto", "auto", False, False, 0), ("faster-whisper", "cpu", False)),
        (("auto", "auto", True, True, 0), ("mlx", "metal", True)),
        (("auto", "auto", True, False, 0), ("faster-whisper", "cpu", False)),
        (("auto", "cpu", True, True, 1), ("faster-whisper", "cpu", False)),
        (("auto", "cuda", False, False, 1), ("faster-whisper", "cuda", True)),
        (("auto", "cuda", False, False, 0), None),
        (("mlx", "auto", False, False, 1), None),
        (("mlx", "auto", True, True, 0), ("mlx", "metal", True)),
        (("faster-whisper", "auto", True, True, 0), ("faster-whisper", "cpu", False)),
    ]
    for (b, d, mac, mlx, n), want in cases:
        plan, err = tr.choose(b, d, mac_arm=mac, mlx_ok=mlx, cuda_n=n)
        got = (plan["backend"], plan["device"], plan["fast"]) if plan else None
        check(f"choose({b}, {d}, mac={mac}, mlx={mlx}, cuda={n}) -> {want}", got == want,
              f"got {got}" + (f" ({err})" if err else ""))


def test_keyframes(kf) -> None:
    print("keyframes.py")
    A = ["DmlExecutionProvider", "CUDAExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"]
    lab = lambda dev, av: [x[0] for x in kf.ocr_candidates(dev, av)]            # noqa: E731
    check("ocr order: DirectML > CUDA > CoreML > CPU", lab("auto", A) == ["DirectML", "CUDA", "CoreML", "CPU"])
    check("ocr gpu == auto order", lab("gpu", A) == lab("auto", A))
    check("ocr cpu: CPU only", lab("cpu", A) == ["CPU"])
    check("ocr no GPU provider -> [CPU]", lab("auto", ["CPUExecutionProvider"]) == ["CPU"])
    check("ocr CoreML-only mac -> CoreML, CPU", lab("auto", ["CoreMLExecutionProvider", "CPUExecutionProvider"])
          == ["CoreML", "CPU"])

    # --- provider fallback with fake onnxruntime / RapidOCR -------------------------------------
    sys.modules["onnxruntime"] = types.SimpleNamespace(
        get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])
    built: list[str | None] = []

    def fake_engine(mode: str):
        def make(switch, cache_dir):
            built.append(switch)
            gpu = switch == "use_dml"
            attached = ["CPUExecutionProvider"] if (not gpu or mode == "shift") else \
                ["DmlExecutionProvider", "CPUExecutionProvider"]
            eng = types.SimpleNamespace(text_det=types.SimpleNamespace(
                session=types.SimpleNamespace(session=types.SimpleNamespace(get_providers=lambda: attached))),
                gpu=gpu, calls=0)
            if mode == "build" and gpu:
                raise RuntimeError("DML device lost while building")
            return eng
        return make

    def fake_image(mode: str):
        def run(eng, path):
            eng.calls += 1
            if eng.gpu and ((mode == "first" and eng.calls == 1) or (mode == "mid" and eng.calls == 2)):
                raise RuntimeError("DML inference failed")
            return {"txts": ["int main()"], "scores": [0.99], "boxes": [[[0, 0], [90, 0], [90, 20], [0, 20]]]}
        return run

    def frames(tmp: pathlib.Path, n: int = 3):
        out = []
        for i in range(n):
            f = kf.Frame(i * 10, [(i * 10, i * 10 + 5)])
            f.path, f.crop = tmp / f"cand_{i:05d}.jpg", (0, 0, 100, 100)
            out.append(f)
        return out

    for mode, dev, want_label, want_built in [
        ("ok", "auto", "DirectML", ["use_dml"]),
        ("build", "auto", "CPU", ["use_dml", None]),
        ("first", "auto", "CPU", ["use_dml", None]),
        ("mid", "auto", "CPU", ["use_dml", None]),
        ("shift", "auto", "CPU", ["use_dml"]),
        ("ok", "cpu", "CPU", [None]),
    ]:
        built.clear()
        kf.ocr_engine, kf.ocr_image = fake_engine(mode), fake_image(mode)
        with tempfile.TemporaryDirectory() as td, redirect_stdout(io.StringIO()):
            tmp = pathlib.Path(td)
            fr = frames(tmp)
            prov = kf.ocr(fr, tmp / "ocr.json", dev)
            again = kf.ocr(frames(tmp), tmp / "ocr.json", dev)
        check(f"ocr provider [{mode}/{dev}] -> {want_label}; engines built {want_built}",
              prov["label"] == want_label and built == want_built,
              f"label={prov['label']} built={built} failed={prov.get('failed')}")
        check(f"ocr [{mode}/{dev}] failures recorded, text filled, cache reused",
              bool(prov["failed"]) == (mode != "ok")
              and fr[0].lines == ["int main()"] and again["cached"] and again["label"] == want_label,
              f"failed={prov['failed']}")
    check("provider_str", kf.provider_str({"label": "DirectML", "actual": "DmlExecutionProvider"})
          == "DirectML [DmlExecutionProvider]" and kf.provider_str({"label": "CPU", "cached": True}) == "CPU (cached)")

    # --- speech sources ---------------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        wj, txt = tmp / "w.json", tmp / "t.txt"
        wj.write_text(json.dumps({"segments": [{"start": 0, "end": 4, "text": " alpha beta"},
                                               {"start": 100, "end": 104, "text": " gamma"}]}), encoding="utf-8")
        txt.write_text("Course: X\nDuration: 1\n=====\n" + " ".join(f"w{i}" for i in range(1000)), encoding="utf-8")
        label, said, segs = kf.load_speech(200.0, wj, txt)
        check("speech: Whisper JSON wins, exact timing", segs and said(2) == "alpha beta" and "gamma" in said(102))
        label, said, segs = kf.load_speech(200.0, None, txt)
        check("speech: transcript gives proportional hints, header stripped",
              segs is None and said(100).startswith("≈") and "Course" not in said(100))
        with redirect_stdout(io.StringIO()):
            label, said, segs = kf.load_speech(200.0, tmp / "missing.json", None)
        check("speech: nothing -> empty", said(5) == "" and segs is None)

    # --- states / prune / webcam mask on a synthetic recording ----------------------------------
    def screens(webcam: bool):
        n, H, W = 90, kf.TH, kf.TW
        A = np.full((n, H, W), 200, np.uint8)
        rects = {"A": (20, 30, 80, 70), "B": (150, 60, 210, 100), "C": (60, 110, 120, 150)}
        for t in range(n):
            for r in (["A"] if t < 30 else ["B"] if t < 60 else ["A", "C"]):
                x0, y0, x1, y1 = rects[r]
                A[t, y0:y1, x0:x1] = 30
        if webcam:
            rng = np.random.default_rng(0)
            A[:, 140:170, 260:300] = rng.integers(0, 255, (n, 30, 40), dtype=np.uint8)
        return A

    for webcam in (False, True):
        A = screens(webcam)
        cam, box = kf.layout(A)
        S = kf.Screen(A, cam, box, (1280, 720))
        st = kf.states(S, 0.02, 3)
        pr = kf.prune(S, list(st))
        check(f"synthetic recording (webcam={webcam}): 3 states, prune drops the contained one",
              len(st) == 3 and len(pr) == 2, f"states={len(st)} pruned={len(pr)} cam_px={int(cam.sum())}")
        if webcam:
            check("webcam tile masked", int(cam.sum()) > 800, f"{int(cam.sum())} px")
    check("hms", kf.hms(3725) == "01:02:05")


def test_hires(kf, pl) -> None:
    """PLAN 17: keyframes.py --hires-only regrabs native-resolution copies from frames.json + the video."""
    print("hires (keyframes --hires-only, pipeline reuse check)")
    import imageio_ffmpeg
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        video = tmp / "v.mp4"
        r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc=s=640x360:d=4:r=5", "-pix_fmt", "yuv420p", str(video)],
                           capture_output=True, text=True)
        check("synthetic 640x360 clip built", r.returncode == 0 and video.is_file(), r.stderr[:200])
        d = tmp / "frames"
        d.mkdir()
        (d / "001_00-00-02.jpg").write_bytes(b"\xff\xd8low\xff\xd9")          # the agent copy is never touched
        (d / "002_00-00-03.jpg").write_bytes(b"\xff\xd8low\xff\xd9")
        (d / "frames.json").write_text(json.dumps([
            {"n": 1, "t": 2, "file": "001_00-00-02.jpg", "region": [20, 10, 400, 200], "scale": 0.5},
            {"n": 2, "t": 3, "file": "002_00-00-03.jpg", "scale": 0.5}]), encoding="utf-8")   # no region
        check("hires status before: 0 of 2", kf.hr.status(d)["have"] == 0 and kf.hr.status(d)["total"] == 2)

        def hires_only() -> tuple[int, str]:
            argv, buf = sys.argv, io.StringIO()
            sys.argv = ["keyframes", str(video), "--out", str(d), "--hires-only"]
            try:
                with redirect_stdout(buf):
                    try:
                        kf.main()
                        rc = 0
                    except SystemExit as e:
                        rc = e.code
            finally:
                sys.argv = argv
            return rc, buf.getvalue()
        rc, text = hires_only()
        h1 = d / "hires" / "001_00-00-02.jpg"
        check("--hires-only writes hires/<same name> at scale 1.0 (region size, not the 0.5 scale)",
              h1.is_file() and Image.open(h1).size == (400, 200), str(Image.open(h1).size if h1.is_file() else None))
        check("--hires-only: a record without a region fails (exit 1), others still made",
              rc == 1 and kf.hr.status(d)["have"] == 1, f"rc={rc} {text[-200:]}")
        check("--hires-only leaves frames.json and the low-res copies alone",
              json.loads((d / "frames.json").read_text(encoding="utf-8"))[0]["scale"] == 0.5
              and (d / "001_00-00-02.jpg").read_bytes().startswith(b"\xff\xd8low"))
        m0 = h1.stat().st_mtime_ns
        hires_only()
        check("--hires-only is idempotent (existing copy skipped)", h1.stat().st_mtime_ns == m0)
        check("find_for: frame -> hires twin; the twin itself -> None",
              kf.hr.find_for(d / "001_00-00-02.jpg") == h1 and kf.hr.find_for(h1) is None
              and kf.hr.find_for(d / "002_00-00-03.jpg") is None)
        check("pipeline can see a reused frames dir lacks hires",
              kf.hr.status(d)["missing"] == ["002_00-00-03.jpg"] and hasattr(pl, "hr"))


def test_pipeline(pl) -> None:
    print("pipeline.py")
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        video = tmp / "v.mp4"
        video.write_bytes(b"x" * 100)
        wj = tmp / "whisper.json"
        check("whisper_ok: missing", pl.whisper_ok(wj, video) is None)
        wj.write_text(json.dumps({"video": "v.mp4", "segments": []}), encoding="utf-8")
        check("whisper_ok: valid (empty speech is still valid)", pl.whisper_ok(wj, video) is not None)
        wj.write_text(json.dumps({"video": "other.mp4", "segments": [{}]}), encoding="utf-8")
        check("whisper_ok: another video's file is rejected", pl.whisper_ok(wj, video) is None)
        fr = tmp / "frames"
        fr.mkdir()
        check("frames_ok: missing", pl.frames_ok(fr, video, False) is None)
        for n in ("index.md", "frames.json"):
            (fr / n).write_text("[]", encoding="utf-8")
        (fr / "meta.json").write_text(json.dumps({"video": "v.mp4", "video_bytes": 100, "speech": "none"}))
        check("frames_ok: valid", pl.frames_ok(fr, video, False) is not None)
        check("frames_ok: made without speech but Whisper exists now -> redo", pl.frames_ok(fr, video, True) is None)
        video.write_bytes(b"x" * 101)
        check("frames_ok: size changed -> redo", pl.frames_ok(fr, video, False) is None)


def test_help(tr, kf, pl) -> None:
    print("CLI surface (--help)")
    want = {
        "transcribe": ["--out", "--backend", "--device", "--model", "--hotwords", "--language", "--require-fast"],
        "keyframes": ["--out", "--whisper", "--transcript", "--ocr-device", "--destroy", "--min-dwell",
                      "--text-unit", "--px-unit", "--keep-cache", "--no-pdf", "--hires-only", "--no-hires"],
        "pipeline": ["--out", "--transcript", "--hotwords", "--language", "--whisper", "--device",
                     "--ocr-device", "--reuse", "--keep-cache"],
    }
    for name, mod in (("transcribe", tr), ("keyframes", kf), ("pipeline", pl)):
        buf, old = io.StringIO(), sys.argv
        sys.argv = [name, "--help"]
        try:
            with redirect_stdout(buf):
                mod.main()
        except SystemExit:
            pass
        finally:
            sys.argv = old
        miss = [f for f in want[name] if f not in buf.getvalue()]
        check(f"{name}.py --help lists the §3.6 flags", not miss, f"missing {miss}" if miss else "")


# ==========================================================================
# Integration: real CLIs
# ==========================================================================
def uv() -> str:
    import shutil
    return os.environ.get("UV") or shutil.which("uv") or str(pathlib.Path.home() / ".local" / "bin" / "uv.exe")


def run(script: str, *args, env: dict | None = None, timeout: int = 7200) -> tuple[int, str, float]:
    cmd = [uv(), "run", "--script", str(SCRIPTS / script), *map(str, args)]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, **(env or {})}, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or ""), time.time() - t0


def snapshot(root: pathlib.Path) -> dict[str, tuple[int, int]]:
    out = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            p = pathlib.Path(dirpath) / f
            try:
                st = p.stat()
                out[str(p)] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
    return out


def jload(p: pathlib.Path):
    return json.loads(p.read_text(encoding="utf-8"))


def smoke(repo: pathlib.Path, work: pathlib.Path) -> None:
    print("smoke: Templates clip (2 MB, ~291 s)")
    video = repo / "resources" / "videos" / f"{TEMPLATES}.mp4"
    if not video.is_file():
        return skip("smoke", f"{video} not found")
    before = snapshot(repo)
    w = work / "selftest-smoke"
    w.mkdir(parents=True, exist_ok=True)

    rc, out, secs = run("transcribe.py", video, "--out", w / "t.whisper.json", "--device", "cpu")
    d = jload(w / "t.whisper.json") if rc == 0 and (w / "t.whisper.json").exists() else {}
    check("transcribe --device cpu -> faster-whisper / cpu / int8",
          rc == 0 and (d.get("backend"), d.get("device"), d.get("compute_type")) == ("faster-whisper", "cpu", "int8")
          and isinstance(d.get("segments"), list), f"rc={rc} {secs:.0f}s {d.get('backend')}/{d.get('device')}/{d.get('compute_type')}")

    for dev in ("cpu", "auto"):
        rc, out, secs = run("keyframes.py", video, "--out", w / f"kf-{dev}", "--whisper", w / "t.whisper.json",
                            "--ocr-device", dev)
        meta = jload(w / f"kf-{dev}" / "meta.json") if rc == 0 else {}
        idx = (w / f"kf-{dev}" / "index.md").read_text(encoding="utf-8") if rc == 0 else ""
        want = ("CPU",) if dev == "cpu" else ("DirectML", "CUDA", "CoreML", "CPU")
        check(f"keyframes --ocr-device {dev}: completes, provider printed + recorded",
              rc == 0 and meta.get("ocr_provider") in want and "ocr provider:" in out and "**OCR:**" in idx
              and (w / f"kf-{dev}" / "frames.json").exists(), f"rc={rc} {secs:.0f}s provider={meta.get('ocr_provider')}")

    mac_mlx = sys.platform == "darwin"
    if mac_mlx:
        skip("--require-fast / frames-only without a GPU", "Apple Silicon has MLX; CUDA_VISIBLE_DEVICES does not hide it")
    else:
        nogpu = {"CUDA_VISIBLE_DEVICES": "-1"}
        rc, out, _ = run("transcribe.py", video, "--out", w / "nogpu.whisper.json", "--require-fast", env=nogpu)
        check("no GPU: transcribe --require-fast exits 3 and writes nothing",
              rc == 3 and not (w / "nogpu.whisper.json").exists(), f"rc={rc}")
        rc, out, secs = run("pipeline.py", video, "--out", w / "pl", "--whisper", "auto", "--ocr-device", "cpu", env=nogpu)
        pj = jload(w / "pl" / "pipeline.json") if (w / "pl" / "pipeline.json").exists() else {"steps": {}}
        t, k = pj["steps"].get("transcribe", {}), pj["steps"].get("keyframes", {})
        check("no GPU: pipeline --whisper auto -> frames-only, exit 0",
              rc == 0 and t.get("status") == "unavailable" and k.get("status") == "ok"
              and not (w / "pl" / "whisper.json").exists(), f"rc={rc} whisper={t.get('status')} frames={k.get('status')} {secs:.0f}s")
        rc, out, secs = run("pipeline.py", video, "--out", w / "pl", "--whisper", "auto", "--ocr-device", "cpu",
                            "--reuse", env=nogpu)
        pj = jload(w / "pl" / "pipeline.json")
        check("pipeline --reuse skips the finished keyframes", rc == 0 and pj["steps"]["keyframes"]["status"] == "reused",
              f"keyframes={pj['steps']['keyframes']['status']}")
        rc, out, secs = run("pipeline.py", video, "--out", w / "pl-skip", "--whisper", "skip", "--ocr-device", "cpu", "--no-pdf")
        check("pipeline --whisper skip", rc == 0 and jload(w / "pl-skip" / "pipeline.json")["steps"]["transcribe"]["status"] == "skipped")

    after = snapshot(repo)
    check("subject repo untouched by the smoke tests", before == after,
          f"{len(set(after) - set(before))} new, {sum(1 for k in before if before[k] != after.get(k))} changed")


def compare_pr(repo: pathlib.Path, pr: pathlib.Path) -> None:
    ref_frames = repo / "resources" / "frames" / PR / "frames.json"
    ref_whisper = repo / "resources" / f"{PR}.whisper.json"
    if not (ref_frames.exists() and ref_whisper.exists()):
        return skip("regression compare", "repo reference outputs not found")
    rf, rw = jload(ref_frames), jload(ref_whisper)
    ref_n, ref_det, ref_seg = len(rf), sum(1 for f in rf if f.get("detail_of")), len(rw["segments"])
    nf = jload(pr / "frames" / "frames.json")
    n, det = len(nf), sum(1 for f in nf if f.get("detail_of"))
    segs = len(jload(pr / "whisper.json")["segments"])
    print(f"  reference (repo): {ref_n} frames, {ref_det} detail crops, {ref_seg} segments")
    print(f"  this run        : {n} frames, {det} detail crops, {segs} segments")
    check("regression: frames 149 +/- 3", abs(n - 149) <= 3 and abs(n - ref_n) <= 3, f"{n} (repo {ref_n})")
    check("regression: detail crops 23 +/- 3", abs(det - 23) <= 3 and abs(det - ref_det) <= 3, f"{det} (repo {ref_det})")
    check("regression: Whisper segments 629 +/- 5 %", abs(segs - 629) <= 0.05 * 629 and abs(segs - ref_seg) <= 0.05 * ref_seg,
          f"{segs} (repo {ref_seg})")


def regress(repo: pathlib.Path, work: pathlib.Path, run_it: bool) -> None:
    print("regression: Memory Layout (2 h 16 m)")
    pr = work / "pr-profile"
    video = repo / "resources" / "videos" / f"{PR}.mp4"
    if run_it:
        if not video.is_file():
            return skip("regression", f"{video} not found")
        before = snapshot(repo)
        rc, out, secs = run("pipeline.py", video, "--out", pr, "--reuse",
                            "--transcript", repo / "resources" / f"{PR}.txt",
                            "--hotwords", "C++ std::cout nullptr vtable RBP RSP objdump g++ order book cache line pointer reference stack heap assembly")
        print(out[-1500:])
        check("pipeline.py on the P&R recording exits 0", rc == 0, f"rc={rc} {secs / 60:.1f} min")
        pj = jload(pr / "pipeline.json") if (pr / "pipeline.json").exists() else {"steps": {"transcribe": {}}}
        if pj["steps"]["transcribe"].get("status") == "unavailable":
            skip("regression counts", "no fast Whisper backend (GPU off?) -- not waiting an hour on the CPU")
        else:
            compare_pr(repo, pr)
        check("subject repo untouched by the regression run", before == snapshot(repo))
    elif (pr / "frames" / "frames.json").exists() and (pr / "whisper.json").exists():
        compare_pr(repo, pr)
    else:
        skip("regression compare", f"no {pr}: run the pipeline (Verify block) or pass --regress")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--regress", action="store_true")
    ap.add_argument("--work", type=pathlib.Path, default=PLUGIN / "test-output" / "_r1" / "video")
    ap.add_argument("--cpp-repo", type=pathlib.Path,
                    default=pathlib.Path(os.environ.get("LECTURE_NOTES_CPP_REPO", DEFAULT_REPO)))
    a = ap.parse_args()
    work, repo = a.work.resolve(), a.cpp_repo
    work.mkdir(parents=True, exist_ok=True)

    tr, kf, pl = load("transcribe"), load("keyframes"), load("pipeline")
    test_transcribe(tr)
    test_keyframes(kf)
    test_pipeline(pl)
    test_hires(kf, pl)
    test_help(tr, kf, pl)
    if a.smoke:
        smoke(repo, work)
    regress(repo, work, a.regress)

    n = {s: sum(1 for r in RESULTS if r[0] == s) for s in ("PASS", "FAIL", "SKIP")}
    print(f"\n{n['PASS']} passed, {n['FAIL']} failed, {n['SKIP']} skipped")
    for s, name, detail in RESULTS:
        if s != "PASS":
            print(f"  {s}: {name} -- {detail}")
    sys.exit(1 if n["FAIL"] else 0)


if __name__ == "__main__":
    main()
