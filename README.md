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

### Mouse and keyboard

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
(add / duplicate / reorder / remove, add bars), the **Harmony** chord lane
(see [Seeing and editing the harmony](#seeing-and-editing-the-harmony)),
**Center notes**, **Instrumental only**, **Clear melody**, **New blank score**,
and **Advanced ABC** for raw text editing.

**Import / Export** read and write `.abc` files. Scores are also saved to your
browser automatically, so a refresh does not lose work.

### First things to know

Two controls on the top bar cause most of the confusion when you open a score
for the first time. Check them before anything else.

**1. `Melody` switches which voice you are looking at.**

YuE2 scores have exactly two monophonic voices, and the piano roll shows **one
at a time**:

| Voice | Holds |
| --- | --- |
| `Instrumental` | The non-sung melody |
| `Sung melody` | The vocal line **and all chord symbols** |

The editor always opens on **Instrumental**. If you loaded a sung song, the
grid can look almost empty even though nothing is wrong — the notes are simply
in the other voice. Switch to **Sung melody** to see them.

> **Real example.** A 78-bar ballad had 356 vocal notes but only 33
> instrumental notes, all of them in the intro. Opening the editor on the
> default voice showed an apparently empty grid. Switching to **Sung melody**
> revealed 94 notes in the verse, 149 in the chorus, and 140 in the final
> chorus.

**2. `Section` switches which part of the song is drawn.**

The piano roll draws **one section at a time**, not the whole score. The status
line reports the *total* bar count, while the grid shows only the selected
section — so a score can legitimately read "78 bars" while you count 10 on
screen.

That is a deliberate design decision inherited from the ComfyUI node: drawing
every bar of a long song as one horizontal strip would be unreadable. Use the
`Section` dropdown to move between parts:

```
Section: [ 1. intro        ▼ ]
          1. intro          ← 10 bars
          2. verse          ←  8 bars
          3. pre-chorus     ←  9 bars
          4. chorus         ← 16 bars
          ...
```

Each entry shows its own bar count in the grid labels (`Bar 1` … `Bar 10`), so
you can always tell which part of the song you are editing. Playback, looping
and note edits all apply to the selected section and voice only.

### A basic editing round-trip

1. **Import ABC** and pick a `.abc` file.
2. Set `Melody` to the voice you want to edit.
3. Set `Section` to the part you want to work on.
4. Edit notes by dragging. Every change is re-validated and written back to the
   ABC text as you go.
5. **Export ABC** to write the file back out.

If the piano roll is disabled and the status line mentions *key or meter
changes*, see [Scores with meter or key changes](#scores-with-meter-or-key-changes).

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

## Harmony: chords and bass

This is the part of the format that surprises people most, so it is worth
stating plainly.

### There is no bass voice and no chord voice

YuE2 scores have **exactly two voices**, and both are **melodic**:

| Voice | Purpose |
| --- | --- |
| `Vocal` | The sung melody **and every chord symbol** |
| `Ins` | The instrumental melody |

There is **no third voice** for a bass line, and **no voice for stacked chord
notes**. If you are coming from a DAW or from MuseScore, this is the mental
model to drop.

### How harmony is actually expressed

YuE2 does not read harmony from extra notes. It reads it from **two inputs**:

1. **Chord symbols** written inside the `Vocal` voice
2. **The style prompt** in YuE2's Compose node

YuE2 *generates* the bass, the chord voicings and the instrumentation from
those. You direct the harmony; you do not spell it out note by note.

A score with no chord symbols at all is completely valid — YuE2 will harmonise
from the style prompt alone. Adding symbols gives you control over the
progression, not a requirement.

### Writing chord symbols

A chord symbol sits in double quotes at the start of the `Vocal` line, at the
position where the chord changes:

```
V: Vocal
"Am7"z16|"Dm7"z16|"G7"z16|"Cmaj7"z16|
```

Chord symbols go in `Vocal` **only**. Putting them in `Ins` is a hard error:

```
Native chord symbols belong in Vocal, not Ins
```

**Supported qualities:**

| | |
| --- | --- |
| Major | `C` (bare root) |
| Minor | `Cm` |
| Diminished / augmented | `Cdim` `Caug` |
| Sevenths | `C7` `Cmaj7` `Cm7` `Cdim7` `Cm7b5` |
| Suspended | `Csus4` `Csus2` `C7sus4` |
| Sixths | `C6` `Cm6` |
| Minor-major | `Cm(maj7)` |

**Supported roots:** `C C# Db D Eb E F F# G Ab A Bb B` — plus double accidentals
(`Cbb`, `F##`) if you need them.

### Bass lines: use slash chords

This is the closest thing to writing a bass part, and it is genuinely useful.
A slash chord names a bass note under the chord:

```
"C#m/G#"    C#m chord with G# in the bass
"F#m/C#"    F#m chord with C# in the bass
"Am7/G"     Am7 with G in the bass
```

Adding a bass note is how you get a descending or walking bass without a bass
voice existing. In a minor key, inversions on the tonic and subdominant are the
usual starting point.

### Seeing and editing the harmony

The chord panel is easy to miss, because it starts **collapsed**. Below the
piano roll there are two `▸` disclosure triangles:

```
Section: [ 1. intro ▼ ]   Melody: [ Instrumental ▼ ]   ...

  ▸ Arrange sections
  ▸ Harmony (optional)     ← click this one
```

Click **Harmony (optional)** to open it. Each bar of the *current section* gets
a card:

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ Bar 11          │  │ Bar 12          │  │ Bar 13          │
│ C#m             │  │ A               │  │ E               │
│ Root: [C#  ▼]   │  │ Root: [A   ▼]   │  │ Root: [E   ▼]   │
│ Type: [m   ▼]   │  │ Type: [    ▼]   │  │ Type: [    ▼]   │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

- **Root** — `C C# Db D Eb E F F# G Ab A Bb B`. Choosing `—` **deletes** the
  chord for that bar.
- **Type** — `(blank)` = major, `m`, `dim`, `aug`, `7`, `maj7`, `m7`, `dim7`,
  `m7b5`, `sus4`, `sus2`, `6`, `m6`, `7sus4`, `m(maj7)`.

Every change is validated and written straight back into the ABC text.

**Change `Type` before `Root`.** If you want a completely different chord, set
the quality first and the root second. The two dropdowns are wired so that each
one re-reads the other's current value, and setting the type first makes the
final symbol unambiguous.

At the bottom of the panel, **Remove chords (free harmony)** clears every chord
and lets YuE2 harmonise from the style prompt alone.

**After changing a chord, switch section and back to refresh the cards.** The
edit is saved immediately — the ABC text and the score data both update — but
the card's own label is not redrawn until the panel is rebuilt, which happens
when you change the `Section` or `Melody` dropdown. Until then a card can show
the old symbol while the score already holds the new one.

To confirm an edit landed, check the **status line** or switch to **Advanced
ABC** and look at the `Vocal` line: the chord symbol in quotes is authoritative.

### Things to know about the Harmony panel

- **It follows the selected section.** Open it on `intro` and you see 10 cards;
  switch to `chorus` and you see 16. There is no whole-song view of the chords
  in the panel — use the `Section` dropdown to move around. Note that this is
  the same section selector that controls the grid, so changing it changes both.
- **Chords stay in the score even while the panel is collapsed.** Collapsing it
  is purely visual.
- **Mid-bar chord changes are preserved but not editable here.** A chord that
  changes on beat 3 rather than beat 1 survives a round-trip through the editor,
  but the per-bar cards can only represent one chord per bar. When the score
  contains any, the panel says so and points you at **Advanced ABC**.
- **You do not need to close the panel** before editing notes, or vice versa.

### A worked example

Starting from a plain C#m ballad melody with no harmony, here is a progression
that follows the drama of a standard verse / pre-chorus / chorus form:

| Section | Progression | Why |
| --- | --- | --- |
| intro | `C#m` (pedal) | Holds tension, lets the melody enter alone |
| verse | `C#m – A – E – B` | `i – VI – III – VII`, the natural minor loop |
| pre-chorus | `F#m – A – B – G#` | Lands on the **dominant**, so the chorus feels inevitable |
| chorus | `C#m – A – E – B` | Returns home with a wider, more open voicing |

In ABC, the verse becomes:

```
% verse
V: Vocal
"C#m"z16|"A"z16|"E"z16|"B"z16|
```

Applying this to a 78-bar score adds 78 chord symbols and **does not change a
single note** of the melody. Verify that yourself: the note count before and
after must be identical.

### Chords are not verified musically

The parser checks that a symbol is *well-formed* and belongs to a supported
vocabulary. It does **not** check that the chord fits your key, or that the
progression makes sense. `"H7"` is rejected; `"C#m"` over an A major melody is
accepted without complaint. That judgement is yours.

---

## Scores with meter or key changes

If you load a score that changes `M:` (meter) or `K:` (key) **after the
header**, the piano roll is disabled and you see:

> Key or meter changes within the song: original notation preserved in Advanced ABC.

**This is not an error.** It is a warning, and it is shown with the normal
status style — a real error renders with a red bar instead. Your file loaded
correctly and the text is intact.

### Why it happens

The piano roll stores **one** value for the bar length across the whole song:

```python
"bar_ticks": int(score.voices["Vocal"].bars[0][1] * 256)   # note: bars[0]
```

It reads the first bar and assumes every other bar matches. A score mixing 3/4
and 4/4 has no single bar length, so the grid cannot place notes and the editor
steps aside rather than drawing something wrong.

The detection is a single check on every line after the 8-line header:

```python
grid_available = not any(line.startswith(("M:", "K:")) or "[K:" in line for line in lines[8:])
```

### What still works

| | Piano roll | Advanced ABC |
| --- | --- | --- |
| Edit notes with the mouse | ❌ | ❌ |
| Edit the ABC text | ❌ | ✅ |
| Load and save `.abc` | ✅ | ✅ |
| **Send the score to YuE2** | ✅ | ✅ |

**Your music is not lost.** The score parses, the notes are in memory, and YuE2
accepts the file. You only lose the visual grid.

### If you want the piano roll anyway

Convert the score to a single meter by removing the inner `M:` lines and
padding each odd-meter bar to the main meter.

Worked example — one 3/4 bar inside a 4/4 song with `L:1/32`:

```
BEFORE                          AFTER
V: Vocal                        V: Vocal
M:3/4                           g4a8z4f4g4z8|
g4a8z4f4g4|
V: Ins                          V: Ins
M:3/4                           Z|
Z|
V: Vocal                        V: Vocal
M:4/4                           a6a6a4a4d4d4f4-|...
a6a6a4a4d4d4f4-|...
```

Two things changed:

1. The inner `M:` lines are deleted (the header `M:4/4` now governs everything).
2. The 3/4 bar gains a rest. With `L:1/32`, a 4/4 bar is **32 units** and a 3/4
   bar is **24** — so it needs 8 more, and `z8` supplies exactly that.

`Z` (whole-bar rest) needs no padding: it always fills whatever the bar length
is.

**Be aware of the trade-off:** this changes your song. The affected bar becomes
longer, and every later note shifts by the padding. It is a real edit, not a
display fix. Do it only when the odd meter is not essential to the arrangement.

If the odd meter *is* musical, the honest answer is to leave the score in
**Advanced ABC**. It is valid, YuE2 will use it, and you can still edit the
text by hand.

> A proper fix would require the grid to model a *list* of bar lengths — a time
> signature map, as a DAW does. That is a substantial change to both
> `app/score.py` and `web/piano_roll.js`, not a patch.

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
├── requirements.txt      test dependencies only
└── *.abc                 example scores (see below)
```

### Example scores

`alejandra_es_solo_amor.abc` is a 78-bar ballad in C#m at 125 BPM: 356 vocal
notes, 33 instrumental notes, seven sections, **no chord symbols**. All in 4/4,
so the piano roll opens normally. A good file for learning the editor, and a
good "before" example for adding harmony.

`alejandra_es_solo_amor_con_armonia.abc` is the same score with 78 chord
symbols added — the melody is byte-for-byte identical, and only the harmony
differs. Compare the two to see exactly what adding a progression changes.

### Endpoints

Same contract as the ComfyUI node, so the browser code needed only a transport swap:

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| `POST` | `/fl_yue2/score/validate` | `{"score_abc": "..."}` | parsed score document |
| `POST` | `/fl_yue2/score/build` | score document | rebuilt ABC + document |
| `GET` | `/fl_yue2/score/default` | – | `{"score_abc": "..."}` |

Errors return HTTP 400 with `{"error": "..."}`, matching the original routes.

---

## A composition workflow

If you are new to this, here is an order of work that avoids redoing things. The
guiding idea: **decide the things the parser cannot change for you before you
draw a lot of notes.**

### 1. Fix the skeleton first

Tempo, meter and key are cheap to set now and tedious to change later, because
altering the meter can disable the piano roll and altering the key moves every
note.

Set `M:`, `Q:`, `K:` in the header, and keep **one meter for the whole song**.
If you need an odd bar somewhere, decide now whether you are willing to lose the
grid for it.

### 2. Lay out sections before notes

Sections are `% name` comments, and the arrangement controls can duplicate,
reorder and remove them. Sketching the form early means you can duplicate a
chorus instead of redrawing it.

```
% intro        % verse        % pre-chorus        % chorus
```

A conventional shape to start from: `intro – verse – pre-chorus – chorus –
verse – pre-chorus – chorus`. Once the sections exist, the piano roll's
`Section` dropdown lets you work on one at a time.

### 3. Write the melody

| Voice | What goes here |
| --- | --- |
| `Vocal` | The sung line (and later, the chord symbols) |
| `Ins` | The instrumental line |

Both are **monophonic** — one note at a time. Overlapping notes are rejected
with `notes overlap. Each melody can play one note at a time.` This is not a
limitation to work around; it is what the format is.

Remember the `Melody` switch on the top bar when a part looks empty.

### 4. Add harmony

Once the melody holds up on its own, open **Harmony (optional)** and set chords
per bar, or add slash chords for bass movement. A progression that follows the
form:

```
intro        C#m                        (pedal)
verse        C#m – A – E – B            i – VI – III – VII
pre-chorus   F#m – A – B – G#           ends on the dominant
chorus       C#m – A – E – B            home, wider
```

Verify the melody is untouched afterwards — the note count must be the same
before and after.

### 5. Preview, then export

**Play** auditions the selected voice and section through a simple synthesizer,
with an optional **Metronome**. Use it to check rhythm and pitch. It is a
sketching aid, not a preview of YuE2's output — YuE2 adds the instruments,
production and vocals when you run the workflow.

When the score reads the way you want, **Export ABC**.

### 6. Render in YuE2

This editor stops at notation. To turn the `.abc` into audio, take it to
ComfyUI-FL-YuE2: connect the score to the Compose node and set the **style
prompt** there. Style is where instrumentation and production live — the
editor's chords describe the harmony, and the style prompt describes everything
else.

### Iterating

Small edits are safe and reversible: the editor re-validates on every change and
keeps 50 steps of undo.

Two habits worth having:

- **Keep the melody and the harmony as separate steps.** If a chord change
  breaks something, you want to know the notes were already right.
- **Watch the status line.** It reports bars, BPM and duration, and it turns
  **red only on a real error** — a green message like the meter warning is
  information, not a failure.

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

Two further limits worth knowing before you plan around them:

- **One meter for the whole song.** Mid-song `M:` or `K:` changes disable the
  piano roll. See [Scores with meter or key changes](#scores-with-meter-or-key-changes).
- **Two monophonic voices, no bass or chord voice.** Harmony is expressed
  through chord symbols and slash chords. See [Harmony: chords and bass](#harmony-chords-and-bass).

---

## Credits

The piano roll, score model and ABC dialect come from
[ComfyUI-FL-YuE2](https://github.com/facok/ComfyUI-FL-YuE2). This project is a
repackaging of that editor as a standalone local tool; the original license
terms apply to the ported files.
