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
| Hear a chord | Click a chord name in the **`Chords…`** modal |
| Edit chords | **`Chords…`** on the toolbar opens the chord modal |
| Hear chords in playback | **Harmony** checkbox on the toolbar |
| Expand | **Expand / close** for a near-fullscreen editor; <kbd>Esc</kbd> exits |

**Also available:** tempo, key transposition, snap values, the
**Instrumental / Sung melody** switch, per-section arrangement
(add / duplicate / reorder / remove, add bars), the **`Chords…`** editor
(see [Seeing and editing the harmony](#seeing-and-editing-the-harmony)),
**Center notes**, **Instrumental only**, **Clear melody**, **New blank score**,
and **Advanced ABC** for raw text editing.

**Import ABC / Export ABC** read and write `.abc` files. **Export MIDI /
Import MIDI** move notes to and from a DAW as one file per section — see
[MIDI round-trip: one file per section](#midi-round-trip-one-file-per-section).
Scores are also saved to your browser automatically, so a refresh does not lose
work.

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

To edit in Logic Pro or MuseScore instead, use **Export MIDI**, edit the
per-section files, and **Import MIDI**; the ABC is updated in place. See
[MIDI round-trip: one file per section](#midi-round-trip-one-file-per-section).

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

Chords live in a **strip above the timeline** — one button per bar, each as wide
as the bar it belongs to, so the progression lines up with the grid below:

```
 Section: [ 1. intro ▼ ]   Melody: [ Instrumental ▼ ]    ...    [ Chords… ]

 ♫ │ 1  │ 2  │ 3    │ 4        │ 5    │ 6  │ 7  │ 8  │ 9  │ 10     │
   │ —  │ —  │ C#m  │ C#m F#m  │ F#m  │ B  │ E  │ A  │ E  │ F#m B  │
 └───────────────────────────────────────────────────────────────────────
   Bar 1                    Bar 3      Bar 4                     Bar 10
```

The button shows the bar number and the chord, or `—` when the bar has none. A
bar that changes chord mid-way shows both (`C#m F#m`). Hover for the full
detail.

**Click a bar button** to edit just that bar:

```
 Bar 7        Section intro · 4/4 · 4 beats     [◀] 7 / 10 [▶]  [Whole section]  [Close]
 ─────────────────────────────────────────────────────────────────────────────────────
 Bar 7   ┌ beat 1  ┐ ┌ beat 2  ┐ ┌ beat 3  ┐ ┌ beat 4  ┐
         │   E     │ │   +     │ │   +     │ │   +     │
         │[E][ ][→] │ │[—][ ][→]│ │[—][ ][→]│ │[—][ ][→]│
         └─────────┘ └─────────┘ └─────────┘ └─────────┘
 —————————————————————————————————————————————————————————————————————————————————————
 Click a chord name to hear it. Edits save as you make them.      [Clear bar 7]
```

**`Chords…` on the toolbar** opens the same editor for the **whole section** at
once, when you want to see the progression as a whole.

Each beat cell holds three pickers and a clear button:

- **Root** — `C C# Db D Eb E F F# G Ab A Bb B`. Choosing `—` removes the chord.
- **Type** — `(blank)` = major, `m`, `dim`, `aug`, `7`, `maj7`, `m7`, `dim7`,
  `m7b5`, `sus4`, `sus2`, `6`, `m6`, `7sus4`, `m(maj7)`.
- **`→ n`** — move this chord to another beat of the same bar.
- **`−`** — clear just this beat.

**Adding a chord:** click the dashed `+` on any empty beat. It places a `C` there,
which you then change with the pickers.

**Hearing a chord:** click its name in the cell.

**Moving between bars:** `◀` / `▶` step one bar at a time (or the arrow keys),
so you can walk a progression without closing and reopening. **Whole section**
switches back to the full list and **One bar** returns.

**The strip updates as you edit**, so you can watch the progression build up.

Close with **Close**, <kbd>Esc</kbd>, or by clicking outside the modal.

#### Why a modal, and not a panel in the page

Every chord edit re-validates the score and rebuilds the piano roll. An inline
panel was part of that rebuilt tree, so it was destroyed and recreated on each
change — and because it sat at the bottom of a long page, the browser reset the
scroll and threw you back to the top, losing your place mid-edit.

The modal lives outside that tree. It keeps its own scroll position, survives
re-renders, and updates only its own numbers. Verified behaviour: editing a
chord with the modal scrolled to 300px leaves it at 300px.

**Change `Type` before `Root`.** If you want a completely different chord, set
the quality first and the root second. The two dropdowns are wired so that each
one re-reads the other's current value, and setting the type first makes the
final symbol unambiguous.

To confirm an edit landed, look at the **strip above the timeline** — it
refreshes immediately — or switch to **Advanced ABC** and read the `Vocal` line,
where the chord symbol in quotes is authoritative.

### Hearing the chords

You can check a progression by ear without leaving the editor.

**Click a chord name to hear it.** The chord shown in each beat cell is a
button: clicking it plays that chord's voicing on its own, about a second long,
so you can compare two candidates on the spot. This is the fastest way to decide
between, say, `E` and `Emaj7`.

**Use the `Harmony` checkbox for playback.** On the toolbar next to `Metronome`:

| `Harmony` | What `Play` sounds |
| --- | --- |
| **on** (default) | The selected melody **and** the chords |
| off | The melody alone |

Toggling it while playing restarts playback with the new setting.

Two things to know about how the chords sound:

- **They play whichever voice you are viewing.** The accompaniment belongs to
  the song, not to one lane: switch `Melody` between `Instrumental` and `Sung
  melody` and the chords keep playing either way. This matters most in an
  instrumental intro, where the melodic content sits in `Instrumental` while the
  chord symbols live in `Vocal` — hearing both at once is the useful default.
- **Each chord rings until the next one**, filling the bar (or several bars, if
  the chord repeats). A chord written mid-bar sounds from its own position.

**The voicing is an interpretation, not data in the file.** A chord symbol is
just a name — `C#m7` says nothing about which octave or how many notes. To make
it audible the editor expands the symbol into a sensible voicing:

```
C#m   -> C#2 G#2 C#3 E3        root in the bass, chord tones stacked above
B7    -> B1 F#2 A2 B2 D#3      the seventh included
F#m/C#-> C#2 F#2 A2 C#3        slash chords put the written bass note on the bottom
```

They are voiced **low** (roughly C2–A3) while melodies sit around B4–A5, so the
harmony supports the melody instead of masking it, and they play more quietly
and with a different tone. Slash chords are respected: `C#m/G#` really does put
G# underneath.

This preview is a sketching aid. It is a plain synthesizer, not a rendering of
YuE2's arrangement — YuE2 will choose its own voicings, instruments and register
when you run the workflow. Use it to check that the *progression* is right, not
how it will finally sound.

### Things to know about the chord modal

- **It follows the selected section.** Open it on `intro` and you see that
  section's bars; switch `Section` and reopen for another part. The modal header
  names the section, meter and bar range it is showing.
- **Beats per bar come from the meter**, not a fixed four: `4/4` → 4 cells,
  `3/4` → 3, `5/4` → 5, `6/8` → 3, `12/8` → 6.
- **Add, move and remove chords freely.** Click a dashed `+` to place one on a
  free beat, use `→ n` to move it to another beat of the same bar, and `−` to
  clear a single beat.
- **The footer button clears only what you are looking at.** Its label names the
  scope, so there is no guessing:

  | Modal is showing | Button reads | It removes |
  | --- | --- | --- |
  | One bar | `Clear bar 7` | Every chord in bar 7 |
  | Whole section | `Clear section "intro"` | Every chord in the intro section |

  It is disabled when there is nothing to clear, and it **never wipes the whole
  score** — to empty the song, clear each section in turn.
- **Edits apply as you make them**, so there is nothing to save or confirm.
- **Playback follows beats, not bar lines** — what you hear matches what the
  cells say.
- **You can keep editing notes with the modal open**, though closing it first
  gives the roll more room.

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

## MIDI round-trip: one file per section

**Export MIDI** writes one Standard MIDI File per ABC section — `01_intro.mid`,
`02_verse.mid`, `03_chorus.mid`, ... — so Logic Pro, MuseScore, or another DAW
shows each part as its own region or track. Edit the notes there, then **Import
MIDI** with the edited files. The ABC stays open in the editor and remains the
source of truth; only the notes of the sections you import are replaced.

This is the intended loop:

```
YuE2 cover ──▶ .abc ──▶ this editor ──▶ Export MIDI ──▶ edit in Logic/MuseScore
                ▲                                             │
                └──────────── Export ABC ◀── Import MIDI ◀────┘
```

### What is inside each file

Every section file contains three tracks:

| Track | Contents |
| --- | --- |
| Conductor | Section marker, tempo, time signature, key signature |
| `Vocal` | The section's vocal notes (MIDI channel 1) |
| `Ins` | The section's instrumental notes (MIDI channel 2) |

Both voice tracks are always present, even when a voice is empty in that
section. Tick 0 is the first bar of the section. Track names and internal
markers carry the section number:

```
YUE2-SECTION 02 verse bars=16 voices=Vocal,Ins
```

The file name is the primary, human-readable key. The embedded marker is the
safety net: if a DAW renames the file but preserves the marker, the import still
knows which section it belongs to. A descriptive suffix such as
`02_chorus copy.mid` is fine when the marker agrees. A disagreement about the
section **number** is rejected rather than guessed.

Section numbers are **positions**, not permanent IDs. After adding, removing, or
reordering sections in the ABC, export the MIDI files again before importing any
older files. The marker's bar count catches many stale arrangements, but not
all of them.

### Import rules

The loaded ABC defines the song. MIDI files supply notes only:

- **Imported:** note start, duration, and pitch for the matching section.
- **Not imported:** section order and bar counts, chords, tempo, meter, key,
  and every other section.
- **Voices:** files exported here replace both voices in their section. A file
  with no voice declaration only replaces the voices it actually contains.
- **Empty tracks:** re-importing an exported file whose voice was emptied in the
  DAW clears that voice's notes in that section. That is how you delete a part.
- **Note ownership:** a note belongs to the section where it *starts*. A note
  that starts in one section and sustains into the next keeps its tail; import
  the section where it starts to edit it.

### Rejections and warnings

The import is deliberately strict; a failed import leaves the score untouched.

| Situation | Result |
| --- | --- |
| File has no section number and cannot be matched | **Rejected** |
| Two files target the same section | **Rejected** |
| File name says one section, marker says another | **Rejected** |
| Markers or track names inside one file disagree | **Rejected** |
| Drum channel 10, or an unnamed track on channel 3+ | **Rejected** |
| Two notes overlap in one voice | **Rejected**, with section/bar and pitches |
| A note starts after the section's last bar | **Rejected** |
| MIDI time signature differs from the ABC | **Rejected** (bar alignment would break) |
| Tempo differs | Imported, with a warning; the ABC keeps its BPM |
| Key signature differs | Imported, with a warning; notes are taken as written |
| Note is off the 1/32 grid | Imported, quantized, with a warning |

To fix a rejection, keep the exported names, or open the file in the DAW and
correct the named bar/track. Running **Export MIDI** again is the safest reset.

### What MIDI cannot carry

MIDI is a note transport, not a score document. These stay in the ABC/editor:

- chord symbols (edit them with **`Chords…`**);
- section names and order;
- tempo, meter, and key-signature maps (one of each per song).

These are not stored by the editor at all and are dropped on import: velocity,
articulation, lyrics, and instrument choice. The exported program changes
(Voice Oohs / piano) are only a DAW preview convention.

Timing is quantized to the editor's grid (1/32 notes, or finer if the score
already uses a finer `L:`). Triplets and swing are approximated. Both YuE2
voices are monophonic in each direction, so polyphonic DAW tracks are rejected
instead of being silently reduced.

### Suggested workflow

1. Open the `.abc` from YuE2 and click **Export MIDI**.
2. Choose a folder (Chrome/Edge) or allow the individual downloads.
3. In Logic/MuseScore, keep each file's name and edit its `Vocal`/`Ins` tracks.
4. Back in the editor, click **Import MIDI** and select the files you changed
   (one or all of them). Sections you do not select are untouched.
5. Fix any reported overlap or meter problem, then **Import MIDI** again.
6. **Export ABC** and feed that file to YuE2.

For `al_otro_lado_del_mar.abc` the export is:

```
01_intro.mid  02_verse.mid  03_chorus.mid  04_verse.mid
05_interlude.mid  06_chorus.mid  07_outro.mid
```

The repeated `verse`/`chorus` names are safe: the number is the section key, and
the slug is only for human readability.

### DAW notes

- **Logic Pro:** import each file as a separate project, or use *File > Import*
  and keep the track names. The conductor track carries the section markers.
  The exported track names end in `- Vocal` and `- Ins`, which survives a rename
  that keeps those words.
- **MuseScore:** *File > Import* the MIDI, edit the two staves, then *File >
  Export > MIDI*. Keep the original file name. MuseScore may rename the tracks
  to instrument names; before exporting, name them back to **Vocal** and **Ins**
  (or keep them on MIDI channels 1 and 2). Track names, `YUE2-VOICE` text, and
  the original channels are the three ways the import recognizes the voices.
- If a DAW strips the file name, the embedded `YUE2-SECTION` marker still
  identifies the section. If it strips the marker too, the import cannot
  identify the section and deliberately refuses to guess.

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
│   ├── score.py          inspect_score / build_score (ComfyUI removed)
│   └── midi.py           per-section Standard MIDI File bridge
├── web/
│   ├── index.html        page shell
│   ├── style.css         editor styles
│   ├── piano_roll.js     byte-identical to ComfyUI-FL-YuE2/web/
│   └── score_editor.js   standalone shell (node plumbing removed)
├── tests/
│   ├── test_score.py     score logic (no browser needed)
│   ├── test_midi.py      MIDI round-trip and rejection cases
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
| `POST` | `/fl_yue2/score/midi/export` | `{"score_abc": "..."}` | `{"files": [...], "summary": ...}` |
| `POST` | `/fl_yue2/score/midi/import` | `{"score_abc": "...", "files": [...]}` | rebuilt ABC + `import` summary |
| `GET` | `/fl_yue2/score/default` | – | `{"score_abc": "..."}` |

MIDI file `data` is base64-encoded in both directions; each file is one section
with its own `Vocal` and `Ins` tracks. Errors return HTTP 400 with
`{"error": "..."}`, matching the original routes.

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

Once the melody holds up on its own, open **`Chords…`** and set the progression
beat by beat, or use slash chords for bass movement. A progression that follows
the form:

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

### 7. Edit a section in a DAW (optional)

If a section needs a different phrase or voicing, click **Export MIDI**, fix
that section in Logic Pro or MuseScore, then **Import MIDI** with the edited
file. Only that section's notes change; chords, form, tempo, meter, and key stay
as they are. Export the ABC and render again. See
[MIDI round-trip: one file per section](#midi-round-trip-one-file-per-section).

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
python3 tests/test_score.py                 # 15 checks, no browser
python3 tests/test_midi.py                  # 30 checks, no browser
```

`test_midi.py` covers full export/import round-trips, a 3/4 score with duplicate
section names, empty voices and an empty section, notes crossing a section
boundary, partial imports, filename/marker recovery, overlap and out-of-range
rejections, and a running-status MIDI reader case.

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
invalid ABC surfaces a readable error. It also exports one MIDI file per section
through the real UI, imports it back (including a renamed file recovered from
its marker), checks that a non-MIDI file is rejected without changing the score,
and writes `docs/screenshot.png`.

---

## Scope and limitations

This editor **only** reads and writes ABC notation. It does not generate audio,
does not load YuE2 models, and does not render your score into music — that
still happens in ComfyUI (or wherever you run YuE2). The piano roll's Play
button is a simple synthesizer preview of the melody and chords, not a preview
of YuE2's finished production.

Not included, matching the upstream node: audio transcription and
instrument/sound selection (those live in YuE2's style prompt). MIDI
import/export **is** included, but only as a note bridge: one file per section,
with chords, section layout, tempo, meter, and key staying in the ABC. See
[MIDI round-trip: one file per section](#midi-round-trip-one-file-per-section).

Further limits worth knowing before you plan around them:

- **One meter for the whole song.** Mid-song `M:` or `K:` changes disable the
  piano roll. See [Scores with meter or key changes](#scores-with-meter-or-key-changes).
- **Two monophonic voices, no bass or chord voice.** Harmony is expressed
  through chord symbols and slash chords. See [Harmony: chords and bass](#harmony-chords-and-bass).
- **MIDI is notes-only and grid-quantized.** Timing is snapped to 1/32 notes
  (or the score's finer `L:`), so triplets and swing are approximated; velocity,
  articulation, lyrics, and instrument choice are not preserved. The ABC
  remains the source of truth, so re-importing a file only replaces notes in its
  section.
- **MIDI import is batched.** One import accepts up to 256 files, 8 MB per file
  and 16 MB in total. A song with more than 256 sections can be imported in
  batches; every batch leaves the sections it does not name untouched.

---

## Credits

The piano roll, score model and ABC dialect come from
[ComfyUI-FL-YuE2](https://github.com/facok/ComfyUI-FL-YuE2). This project is a
repackaging of that editor as a standalone local tool; the original license
terms apply to the ported files.
