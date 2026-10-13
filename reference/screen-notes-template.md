# Screen notes — frame-reader output schema

The transcript loses everything that was only on screen: code as typed, compiler and terminal output,
listings, whiteboard drawings, slide text. The **frame-reader** converts keyframes into **screen notes**,
which the digest-builder then treats as the authority for code, output and diagrams (see source precedence
in `house-style.md`). Frames are read once for content; downstream, only the visuals step opens a JPG again, to crop a board
drawing you catalogued into a figure for the note.

Location: `<session_dir>/screen-notes/shard-K.md`, one file per shard of at most 60 frames. Validated by
`check_screen_notes.py` against the keyframes `frames.json`.

## 1. Inputs

- `index.md` in the frames directory: one entry per kept frame, `## NNN · hh:mm:ss · file.jpg`, with
  `full screen` or `detail - rest of screen in Frame NNN`, the stretches it was on screen, OCR `text:`
  (noisy, a hint only) and `said:` (about 30 words of speech while it was up).
- The JPGs named there. Open 10 to 20 per message in parallel.
- A **detail** frame shows only the part that was new; the rest of that screen is in the frame it names.
  Read the pair together.
- Each frame is the screen just before it changed, so it holds everything added while it was up.

## 2. Front matter

```yaml
shard: 1/3                  # K/N
frames: "001-060"           # inclusive range of frame numbers this shard owns
index: "E:/.../frames/<stem>/index.md"
read: [1, 2, 3, 5, 6]       # frame numbers you opened and described (also present as ## F### headings)
skipped:                    # frames in range you did not describe, each with a reason
  - {n: 4, reason: "desktop, nothing examinable"}
```

**Every frame in the range must be either read or skipped with a reason.** This is the guard against
silently dropping twenty minutes of content. Skip only frames whose index text and a glance show nothing
examinable (desktop, empty browser tab, meeting UI, a duplicate of an adjacent frame). **Never skip** code,
terminal, slide or diagram frames, even if they look repeated: transcribe what is new.

## 3. One section per read frame

````markdown
## F031 · 00:30:48 · detail-of-F027 · relevance high

- **Shows:** terminal after running the object-file listing.
- **Verbatim:**
  ```text
  $ ls
  copy_bracket  main  main.o  main.s
  ```
- **Diagram:** (none)
- **Crop:** (none)
- **Extended later by:** (none)
- **Said:** "previous caller right okay let me go to my iPad to explain this" (from index)
- **Concepts:** compile pipeline, build artefacts
````

Heading: `## F<NNN> · hh:mm:ss · full | detail-of-F<NNN> · relevance high|low`. Fields, in order:

| Field | Content | Rule |
| --- | --- | --- |
| Shows | one line: what kind of screen this is | |
| Verbatim | code, terminal text, slide text, **exactly** as visible, in a fenced block | preserve spelling, spacing, odd names; mark illegible parts `<unreadable>`; do not fix bugs or typos the instructor typed |
| Diagram | board or slide drawing, described so it can be **redrawn**: boxes, arrows, labels, values, addresses, order | positions ("left of", "below") and every number visible; no interpretation beyond what is drawn |
| Crop | for a board, slide or other visual: the box that contains the **drawing only**, as fractions of the frame image `x0,y0,x1,y1` (0 to 1, origin top-left); `(none)` when there is no diagram | exclude menu bars, toolbars, the desktop, the webcam tile and the terminal behind the board; err slightly wide, never cut a label; estimate from what you see, to the nearest 0.02 |
| Extended later by | `F<NNN>` of a later frame that shows the **same drawing with more on it** (a board that keeps growing, a stack that gets another frame), else `(none)`; add `extends F<NNN>` when this frame continues an earlier drawing | follow the whole chain: the digest embeds the last, fullest version |
| Said | the index's `said:` line, trimmed | context only; the Scaler transcript wins for speech |
| Concepts | short tags matching likely digest concepts | helps the digest-builder place the frame |

Relevance: `high` when it holds code, output, a diagram or slide content the notes may use; `low` when it
is context only (a file tree, a setting).

## 4. Rules

- **Transcribe, do not summarise.** When a screen holds code, the code must be recoverable from your
  notes to the character. A paraphrase is a defect.
- Terminal output is evidence: copy it exactly, including error text, addresses and byte counts.
- **Catalogue every visual.** Each board drawing, diagram, annotated slide and pedagogically important
  screen gets a `Diagram` description, a `Crop` box and its `Extended later by` link. A board that grows
  over ten frames is ten sections linked into one chain; the last frame is the one that matters, but the
  early ones still record what was added when. Never summarise a drawing as "a diagram" or "stack sketch":
  the downstream embed/redraw decision rests on your description.
- Terminal, editor and code screens are **not** visuals: transcribe them in `Verbatim`; `Crop` is `(none)`.
- Handwritten boards: describe what is drawn and written; if a label is ambiguous say so
  (`<unreadable: looks like 0x7ffc...>`), never guess a number.
- OCR text in the index is noisy (`Pointeas`, `ASSOmbly`): use it only to locate content, never copy it.
- Note changes between frames of one screen (a line added, a value edited): the later frame holds the
  superset; mention what changed only if it is the teaching point.
- No logistics, no commentary on the instructor, no opinions about correctness. Wrong code is copied
  wrong; the digest flags corrections.
- Write only your shard file. Never write under any `protect` path. Return at most 150 words:
  status, the file path, counts (frames read / skipped), flags (unreadable frames).

## 5. Complete minimal example (shard of 4 frames)

````markdown
---
shard: 1/1
frames: "001-004"
index: "E:/out/frames/example/index.md"
read: [1, 2, 4]
skipped:
  - {n: 3, reason: "meeting UI, nothing examinable"}
---

## F001 · 00:02:17 · full · relevance high

- **Shows:** handwritten board titled "L-3 Arrays".
- **Verbatim:** (none)
- **Diagram:** a row of ten adjacent boxes labelled 0 to 9 on the left; label `arr` under the first box;
  an arrow from `arr` to box 0; text "int arr[10]" above the row; "40 bytes" written under the row.
- **Crop:** 0.04,0.12,0.96,0.78
- **Extended later by:** (none)
- **Said:** "so an array name is basically the address of the first element" (from index)
- **Concepts:** array layout, decay

## F002 · 00:05:40 · full · relevance high

- **Shows:** editor with a C++ file.
- **Verbatim:**
  ```cpp
  #include <iostream>
  void f(int a[]) { std::cout << sizeof(a) << "\n"; }
  int main() {
      int arr[10];
      std::cout << sizeof(arr) << "\n";
      f(arr);
  }
  ```
- **Diagram:** (none)
- **Crop:** (none)
- **Extended later by:** (none)
- **Said:** "watch what sizeof prints inside the function" (from index)
- **Concepts:** array decay, sizeof

## F004 · 00:06:12 · detail-of-F002 · relevance high

- **Shows:** terminal below the editor after compile and run.
- **Verbatim:**
  ```text
  $ g++ main.cpp && ./a.out
  40
  8
  ```
- **Diagram:** (none)
- **Crop:** (none)
- **Extended later by:** (none)
- **Said:** "see, forty and then eight" (from index)
- **Concepts:** array decay, sizeof
````
