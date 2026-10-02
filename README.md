# YuE2 ABC Editor (standalone)

A local, self-contained editor for the ABC notation dialect used by
[YuE2](https://github.com/multimodal-art-projection/YuE). It gives you the full
piano-roll editing experience of the **ComfyUI-FL-YuE2** custom node — draw,
move and resize notes, arrange sections, set chords, tempo and key, audition
pitches, preview playback, import and export `.abc` — **without running
ComfyUI, without PyTorch, and without a GPU.**

Load a score, edit it visually, save it as `.abc`, and hand that file to YuE2
whenever and wherever you actually run it.

---

## Why this works without a GPU

In the original custom node the piano roll is *already* pure browser
JavaScript, but every edit is round-tripped to Python:

```
ABC text ──POST /fl_yue2/score/validate──▶ Python (abc_score.py) ──▶ piano-roll JSON
                                               ▲
        each edit ──POST /fl_yue2/score/build──┘
```

Those two endpoints only existed as `aiohttp` routes on ComfyUI's
`PromptServer`. The parser behind them, `yue2/abc_score.py`, imports nothing
but `re`, `dataclasses` and `fractions`.

So this project replaces only the **transport**:

| In ComfyUI-FL-YuE2 | Here |
| --- | --- |
| `api.fetchApi("/fl_yue2/score/validate")` | `fetch("/fl_yue2/score/validate")` |
| `PromptServer.instance.routes.post(...)` | `http.server` routes in `server.py` |
| `import { app } from "../../scripts/app.js"` | removed |
| ComfyUI node widget held the score | `localStorage` + import/export |

The piano roll (`web/piano_roll.js`) is carried over **byte-for-byte**. The
score logic (`app/score.py`) is a faithful port of `yue2/score_editor.py`, and
`app/abc_score.py` is a verbatim copy — verified by the test suite to produce
identical results.

---

## Requirements

- macOS, Linux or Windows
- Python 3.9+ (tested on 3.14)
- A modern browser (Chrome, Safari, Firefox — anything with WebAudio + SVG)

Nothing else. No `pip install` is needed to *use* the editor.

---

## Quick start

```bash
cd abc-editor-standalone
./run.sh
```

Your browser opens at <http://127.0.0.1:8188>. Use `--port` and `--no-browser`
to change that:

```bash
./run.sh --port 9000 --no-browser
```

Or without the helper script:

```bash
python3 server.py
```

### Optional: set up the virtual environment manually

If you plan to run the test suite, or prefer an explicit venv:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # only needed for tests
.venv/bin/python server.py
```

`run.sh` does this automatically on first run.

---

## Using the editor

| Action | How |
| --- | --- |
| Add a note | Click empty space in the grid |
| Move a note | Drag it (pitch and time) |
| Resize a note | Drag the white handle on its right edge |
| Delete | Right-click a note, or select and press <kbd>Delete</kbd> |
| Select many | <kbd>Shift</kbd>-drag a box, or <kbd>Shift</kbd>-click to toggle |
| Move a selection | Drag any selected note |
| Nudge | Arrow keys; <kbd>Shift</kbd>+<kbd>↑</kbd>/<kbd>↓</kbd> for an octave |
| Play / pause | <kbd>Space</kbd> |
| Scrub | Drag the timeline or the playhead |
| Undo / redo | <kbd>⌘</kbd>+<kbd>Z</kbd> / <kbd>⌘</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> |
| Hear a pitch | Click a piano key, or drag a note (auditions as you change pitch) |
| Expand | **Expand / close** for a near-fullscreen editor; <kbd>Esc</kbd> exits |

**Also available:** tempo, key transposition, snap values, the
**Instrumental / Sung melody** switch, per-section arrangement
(add / duplicate / reorder / remove, add bars), the **Harmony** chord lane with
per-bar root and quality, **Center notes**, **Instrumental only**, **Clear
melody**, **New blank score**, and **Advanced ABC** for raw text editing.

**Import / Export** read and write `.abc` files. Scores are also saved to your
browser automatically, so a refresh does not lose work.

---

## The ABC dialect

This is deliberately **not** a general ABC editor. It speaks the restricted
two-voice dialect YuE2 consumes, and rejects anything else with an explanatory
error. A score must look like:

```
X:1
T:
M:4/4
L:1/16
Q:1/4=88
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:Am
% verse
V: Vocal
"Am7"z16|"Dm7"z16|"G7"z16|"Cmaj7"z16|
V: Ins
E2A2c4B2A2G4|F2A2d4c2A2F4|G2B2d4c2B2A4|E2G2c4B2G2E4|
```

Key rules:

- Exactly two voices, `Vocal` and `Ins`, each **monophonic** (one note at a time)
- Header lines 1–8 are positional: `X:1`, blank `T:`, `M:`, `L:`, `Q:`, the two
  `V:` definitions, `K:` — in that order
- Chord symbols (`"Am7"`) belong in the `Vocal` voice only
- Sections are `% name` comments; each group is 1–4 bars per voice
- `Z`/`Z2`/`Z3`/`Z4` compress whole-bar rests

If you import ABC from MuseScore or another source, expect the strict parser to
reject it — that is the same behavior the ComfyUI node has, and it is what keeps
scores valid for YuE2. Scores with mid-song key or meter changes load into
**Advanced ABC** instead of the grid, and are preserved exactly.

---

## Project layout

```
abc-editor-standalone/
├── app/
│   ├── abc_score.py      copied verbatim from ComfyUI-FL-YuE2/yue2/
│   └── score.py          inspect_score / build_score (ComfyUI removed)
├── web/
│   ├── index.html        page shell
│   ├── style.css         editor styles
│   ├── piano_roll.js     byte-identical to ComfyUI-FL-YuE2/web/
│   └── score_editor.js   standalone shell (node plumbing removed)
├── tests/
│   ├── test_score.py     score logic (no browser needed)
│   └── browser_check.py  end-to-end via Chrome DevTools Protocol
├── server.py             stdlib HTTP server: static files + score endpoints
├── run.sh                create venv, then serve
└── requirements.txt      test dependencies only
```

### Endpoints

Same contract as the ComfyUI node, so the browser code needed only a transport swap:

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| `POST` | `/fl_yue2/score/validate` | `{"score_abc": "..."}` | parsed score document |
| `POST` | `/fl_yue2/score/build` | score document | rebuilt ABC + document |
| `GET` | `/fl_yue2/score/default` | – | `{"score_abc": "..."}` |

Errors return HTTP 400 with `{"error": "..."}`, matching the original routes.

---

## Tests

```bash
.venv/bin/python tests/test_score.py        # 14 checks, no browser
```

For the browser test, start the server on port **8199** and headless Chrome:

```bash
.venv/bin/python server.py --port 8199 --no-browser &
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --headless=new --disable-gpu --remote-debugging-port=9222 \
    --remote-allow-origins='*' --user-data-dir=/tmp/chrome-abc about:blank &
.venv/bin/python tests/browser_check.py
```

It asserts the editor boots, the piano roll renders real SVG geometry, chords
and arrangement controls exist, notes and tempo commit through `/build`,
monophonic overlaps are correctly rejected, undo/redo works, audition runs, and
invalid ABC surfaces a readable error. It also writes
`docs/screenshot.png`.

---

## Scope and limitations

This editor **only** reads and writes ABC notation. It does not generate audio,
does not load YuE2 models, and does not render your score into music — that
still happens in ComfyUI (or wherever you run YuE2). The piano roll's Play
button is a simple synthesizer preview of the melody and chords, not a preview
of YuE2's finished production.

Not included, matching the upstream node: MIDI import/export, audio
transcription, and instrument/sound selection (those live in YuE2's style
prompt).

---

## Credits

The piano roll, score model and ABC dialect come from
[ComfyUI-FL-YuE2](https://github.com/facok/ComfyUI-FL-YuE2). This project is a
repackaging of that editor as a standalone local tool; the original license
terms apply to the ported files.
