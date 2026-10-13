# video — class recording → what the transcript misses

Three local scripts turn a recording into input for the note-writing agents: the screen (code as typed,
terminal output, `objdump` listings, whiteboard drawings) and a timestamped transcript. Nothing is
uploaded and **no Claude tokens are spent here**. Tokens are spent when an agent *reads* the frames,
which is why `keyframes.py` keeps as few, and as small, as it can without losing content.

| Script | Does | Dependencies (PEP-723, resolved by `uv run`) |
| --- | --- | --- |
| `pipeline.py` | Runs the other two, decides Whisper vs frames-only, writes `pipeline.json` | none |
| `transcribe.py` | Recording → `whisper.json` (timestamped phrases) | faster-whisper (+ CUDA wheels), mlx-whisper on Apple Silicon |
| `keyframes.py` | Recording → deduplicated frames + `index.md` | imageio-ffmpeg, numpy, pymupdf, rapidocr, onnxruntime(-directml) |

The scripts are standalone (no plugin library) and take **explicit paths**: everything is written under
`--out` and nowhere else, so a subject repo that holds the recording is never touched.

## Run

```bash
# the usual way: one command, frames-only automatically if there is no fast Whisper backend
uv run pipeline.py <video> --out <dir> --transcript <class.txt> --hotwords "C++ nullptr vtable"

# or the pieces
uv run transcribe.py <video> --out <dir>/whisper.json            # Whisper (GPU if present)
uv run keyframes.py  <video> --out <dir>/frames --whisper <dir>/whisper.json
```

Needs only **`uv`**: ffmpeg is bundled (`imageio-ffmpeg`, PyAV for faster-whisper), OCR is RapidOCR, and
the first Whisper run downloads the model (~1.6 GB) and, on NVIDIA, the CUDA libraries (~1 GB) into the
`uv` / Hugging Face caches. Run `keyframes.py` after `transcribe.py`: it then attaches the exact speech
to every frame and writes `transcript.md`.

### `pipeline.py <video> --out DIR [options]`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--transcript TXT` | – | Plain-text class transcript: proportional speech hints when there is no Whisper file |
| `--hotwords S` / `--language L` | – / `en` | Passed to Whisper (vocabulary hint; `auto` detects the language) |
| `--whisper auto\|force\|skip` | `auto` | `auto`: run Whisper only on a fast backend (CUDA / MLX), else **frames-only**. `force`: CPU allowed (~1 h per 2 h). `skip`: never |
| `--device auto\|cuda\|cpu` | `auto` | Whisper device (`--backend auto\|faster-whisper\|mlx` and `--model` also pass through) |
| `--ocr-device auto\|gpu\|cpu` | `auto` | OCR provider, see the matrix |
| `--reuse` | off | Skip a step whose output exists **and** belongs to this video (name + byte size; frames made without speech are redone once Whisper exists) |
| `--keep-cache`, `--no-pdf` | off | Passed to `keyframes.py` |

Outputs: `DIR/whisper.json`, `DIR/frames/…`, `DIR/pipeline.json`, `DIR/logs/{transcribe,keyframes}.log`.
`pipeline.json` records per step the status (`ok` / `reused` / `skipped` / `unavailable` / `failed`),
seconds, the Whisper backend + device + compute type + segment count, the OCR provider, frame counts,
and a `decisions` list in plain words. Exit 0 also when frames-only because no GPU exists; 1 when a step failed.

### `transcribe.py <video> --out FILE.json [options]`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--backend auto\|faster-whisper\|mlx` | `auto` | `auto`: Apple Silicon with `mlx_whisper` → mlx; else faster-whisper |
| `--device auto\|cuda\|cpu` | `auto` | faster-whisper device: CUDA if a device is visible, else CPU (int8) |
| `--model` | `large-v3-turbo` | An `mlx-community/whisper-<model>` repo is derived for mlx |
| `--hotwords S`, `--language L` | – / `en` | Decoding hint (`hotwords` for faster-whisper, `initial_prompt` for mlx) |
| `--require-fast` | off | Exit **3** instead of running on the CPU (also when the fast backend dies mid-decode) |

Exit codes: 0 ok · 1 failure · 2 usage / requested backend unavailable · 3 `--require-fast` and CPU only.
Output JSON: `video, model, backend, device, compute_type, language, hotwords, duration, elapsed_s, segments[{start,end,text}]`.
A mid-decode CUDA/MLX error retries on the CPU unless `--device`/`--backend` was forced or `--require-fast` is set.

### `keyframes.py <video> --out DIR [options]`

`--whisper FILE` and `--transcript TXT` feed the speech column (Whisper wins). `--ocr-device auto|gpu|cpu`
picks the OCR provider. `--no-pdf` skips `frames.pdf`. Tuning flags are in the table at the end.

`--hires-only` regrabs just `DIR/hires/` (native-resolution twins, scale 1.0, JPEG q:v 2) for an existing frames dir from its
`frames.json` (`t`, `region`, `file`) and the video: no selection, no OCR, no GPU, idempotent. `--no-hires` skips the twins in a full run.

## OS / CPU / GPU matrix

| Stage | Windows | macOS Apple Silicon | macOS Intel | Linux |
| --- | --- | --- | --- | --- |
| Decode | CPU, bundled ffmpeg | same | same | same |
| OCR | DirectML (any DX12 GPU) → CPU | CoreML → CPU | CPU (CoreML attempted) | CPU |
| Whisper | CUDA fp16 → CPU int8 | MLX turbo → CPU int8 | CPU int8 | CUDA fp16 → CPU int8 |
| `--whisper auto` | CUDA present → run, else frames-only | MLX present → run | frames-only | CUDA present → run, else frames-only |

Decode never uses the GPU: NVDEC (`-hwaccel cuda` + `scale_cuda`) measured 4× *slower* than the CPU on
these mostly-static screens. Linux deliberately ships the CPU `onnxruntime`: OCR on a GPU saves only
~2–4 min per class; install `onnxruntime-gpu` yourself and `auto` will pick CUDA up.

**Verification status** — only what was run is claimed:

| Path | Status |
| --- | --- |
| Windows: DirectML OCR, CPU OCR, faster-whisper CUDA fp16 and CPU int8, `--require-fast`, pipeline | **Verified** (Windows 11, RTX 4070 laptop GPU, uv 0.9) |
| macOS Apple Silicon: `mlx-whisper` backend, CoreML OCR | *Unverified until run on that OS.* Written against the mlx-whisper / RapidOCR APIs, with CPU fallback on any failure |
| Linux: CUDA library preload (`ctypes` RTLD_GLOBAL), CUDA OCR | *Unverified until run on that OS.* Same code as the Windows path minus `add_dll_directory`; falls back to CPU |
| macOS Intel | *Unverified*: CPU paths only |

How the provider is chosen (OCR): `onnxruntime.get_available_providers()` → DirectML, CUDA, CoreML in that
order, then CPU. If the engine fails to build, throws on its first image, or ONNX Runtime quietly attaches the
CPU instead, the next provider is tried and the **provider actually used** is recorded in `frames/meta.json`
(`ocr`), on every entry of `frames.json` (`ocr_provider`), in a line of `index.md`, and on stdout.

## Outputs — `DIR/frames/`

| File | What it is |
| --- | --- |
| `index.md` | **Read first.** Per frame: time, every stretch it was on screen, OCR'd text, what was said |
| `NNN_hh-mm-ss.jpg` | The frames — what the agent opens with the Read tool |
| `frames.pdf` | The same frames with captions, for you to flip through (not for agents) |
| `frames.json` | A list, one entry per frame: region, scale, gain, text, speech, `ocr_provider` |
| `meta.json` | The run: video name + size, counts, token estimate, OCR provider record, speech source, parameters |
| `hires/NNN_hh-mm-ss.jpg` | Same region at scale 1.0, for note figures (`class_figure.py` uses it). **Agents never read it** |
| `transcript.md` | Whisper speech in ~30 s paragraphs with `▶ Frame NNN` markers (when `--whisper` was given) |

## How `keyframes.py` decides

Measured on three lectures (1280×720 recordings):

| Stage | What happens | P&R (136 min) | P&R 2 (168 min) | Memory + STL (122 min) |
| --- | --- | --- | --- | --- |
| Survey | 1 fps grey 320×180 thumbnails; mask webcam, crop dead borders | box 1024×716 | full frame | full frame |
| States | Keep a screen just before it is *replaced* or *eroded*, if up ≥ 3 s | 306 | 274 | 251 |
| Prune | Drop states whose ink is all still present in another | 283 | 259 | 223 |
| Cover | OCR text + pen strokes; keep a frame only if it adds new content | 149 | 121 | 138 |
| Package | Small new region → **detail crop**; big handwriting → **downscaled** | ≈89k tokens | ≈104k tokens | ≈111k tokens |

The webcam is masked twice: once for the whole class, and per 5-minute window by 20×20 cell (a cell
that changes in over half the seconds is a camera; content — even a scrolled board — stays under 20 %).
A tile that only appears for part of the class would otherwise keep the screen from ever looking still.
When the webcam moves around between layouts, no global crop is safe and frames stay full width.

Typing, drawing and appended output never create a frame on their own — the later screen contains them.
A window dragged around, a vim mode change or a toggled-back tab adds nothing, so it is dropped and its
time is credited to the frame that covers it. Drawings count as seen only if **one** kept frame shows the
same strokes, in place or scrolled: pooling pixels across different screens would hide new diagrams.

## Cost

Measured on the author's Windows 11 laptop (RTX 4070 Laptop GPU, 8 GB); a 2 h class.

| Step | Time | Notes |
| --- | --- | --- |
| Survey decode | ~50 s | bundled ffmpeg 7.1 on the CPU; a 430 kbps screen recording decodes at ~10k fps |
| OCR | ~2–3 min | RapidOCR on the GPU via DirectML, ~0.7 s per candidate (CPU ~1.6 s); cached with `--keep-cache` |
| Whisper | ~1–2 min | `large-v3-turbo`, float16 on CUDA — 72–156× realtime; the first run also downloads ~2.6 GB. CPU int8: ~1 h+ |
| GPU decode? | no | NVDEC measured 4× *slower* than the CPU on these mostly-static screens |
| Disk | ~12 MB per class | frames + PDF; the survey cache (~470 MB) is deleted unless `--keep-cache` |

## Tuning (`keyframes.py`)

| Flag | Default | Raise it when… |
| --- | --- | --- |
| `--text-unit` | 4 | too many near-identical terminal frames (OCR phrases needed per frame) |
| `--px-unit` | 0.0025 | too many whiteboard frames (share of the screen in new strokes per frame) |
| `--destroy` | 0.02 | states are captured too eagerly (share of the screen that must be lost) |
| `--min-dwell` | 3 | flickering transitions get captured (seconds a state must stay up) |
| `--keep-cache` | off | you are tuning: re-runs then skip decoding and OCR and take ~40 s |

Lower `--px-unit` if a small handwritten annotation went missing.

## Troubleshooting

- **`no CUDA device visible`** — the NVIDIA GPU is off (laptops: switch it on) or the driver is missing.
  `pipeline.py --whisper auto` then goes frames-only on its own; `--whisper force` runs the CPU
  (same text, ~1 h+ for 2 h). `CUDA_VISIBLE_DEVICES=-1` simulates this.
- **cuBLAS/cuDNN load errors** — `transcribe.py` retries on the CPU (or exits 3 with `--require-fast`).
  To keep the GPU on Windows, run just `transcribe.py` inside WSL2 Ubuntu (it preloads the CUDA libraries there too).
- **OCR provider shows `CPU` although a GPU exists** — look at `meta.json` → `ocr.failed` for the reason, and
  `ocr.available` for what ONNX Runtime offered. On Windows the `onnxruntime-directml` wheel must be the
  only onnxruntime installed (`uv run` guarantees this).
- **The recordings can be MPEG-TS, not MP4**, despite the name — concatenated HLS from the Scaler++
  extension. Both scripts read them as they are; grabs seek 15 s early so decoding passes a keyframe.
- **Agents cannot render `frames.pdf`** — they read the JPGs; the PDF is for people.
- **Reusing an output directory for another recording** is safe: the survey/OCR cache is stamped with the
  video's size and mtime and thrown away when it changes.
