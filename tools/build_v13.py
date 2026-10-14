"""Build alejandra_el_gran_amor_v13.abc.

Chord source
------------
The user transcribed the harmony by ear in Logic Pro. That transcription is
authoritative: it replaces the machine-detected chords of v12.

Bar numbering
-------------
The Logic transcription counts from the first bar that *sounds*. The ABC has
two extra silent bars at the start (ABC bars 1-2 hold no events at all; the
first instrumental note is in ABC bar 3). The mapping is therefore

    Logic bar N  ==  ABC bar N + 2

which was verified against the audio structure:

    Logic  1- 8  ->  ABC  3-10   intro       (8 bars, instrumental only)
    Logic  9-16  ->  ABC 11-18   verse       (voice enters at ABC 11)
    Logic 17-25  ->  ABC 19-27   pre-chorus  (9 bars)
    Logic 26-41  ->  ABC 28-43   chorus      (16 bars = two 8-bar blocks)

Every boundary lines up with a real change in the note data, which is what
makes this mapping trustworthy rather than assumed.

Repetition
----------
The song repeats the verse / pre-chorus / chorus cycle three times. The user
asked for the pattern to be applied to every repetition, so each section type
gets its transcribed progression.
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.score import build_score, inspect_score  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "alejandra_el_gran_amor_v12.abc"
OUT = ROOT / "alejandra_el_gran_amor_v13.abc"

# Logic bar 1 == ABC bar 3, so ABC index 2 (0-based).
OFFSET = 2
BEATS_PER_BAR = 4
TICKS_PER_BEAT = 256                       # PPQ used by the score format

# --- the transcription -----------------------------------------------------
# Each entry is (bar offset within the section, beat, symbol). A symbol with no
# beat starts on beat 1. Mid-bar changes are written on their beat.

INTRO = {                                   # 8 bars
    0: [(1, "C#m")],
    1: [(1, "C#m"), (3, "F#m")],            # 2 beats each
    2: [(1, "F#m")],
    3: [(1, "B")],
    4: [(1, "E")],
    5: [(1, "A")],
    6: [(1, "E")],
    7: [(1, "F#m"), (3, "B")],              # 2 beats each
}

VERSE = {                                   # 8 bars
    0: [(1, "C#m")], 1: [(1, "F#m")], 2: [(1, "B")], 3: [(1, "E")],
    4: [(1, "A")],   5: [(1, "C#m")], 6: [(1, "F#m")], 7: [(1, "B")],
}

PRE = {                                     # 9 bars
    0: [(1, "A")], 1: [(1, "E")], 2: [(1, "C#m")], 3: [(1, "E")],
    4: [(1, "A")], 5: [(1, "E")], 6: [(1, "F#m")],
    7: [(1, "B")], 8: [(1, "B")],           # held over two bars
}

CHORUS = {                                  # 16 bars
    0:  [(1, "F#m")],                       # transition into the chorus
    1:  [(1, "C#m")],
    2:  [(1, "B")],
    3:  [(1, "E")],
    4:  [(1, "F#m"), (3, "E")],             # 2 beats each
    5:  [(1, "C#m")],
    6:  [(1, "B")],
    7:  [(1, "F#m")],
    8:  [(1, "C#m")],
    9:  [(1, "B")],
    10: [(1, "E")],
    11: [(1, "F#m"), (3, "E")],             # 2 beats each
    12: [(1, "B")], 13: [(1, "B")], 14: [(1, "B")], 15: [(1, "B")],
}


def expand(pattern, bars, section_bars):
    """Absolute offsets (in bars from section start) -> chord events."""
    events = []
    for offset, entries in pattern.items():
        if offset >= section_bars:
            continue
        for beat, symbol in entries:
            events.append((offset, beat, symbol))
    return events


def main():
    base = inspect_score(SRC.read_text())
    bar_ticks = base["roll"]["bar_ticks"]

    # The transcription's bar 1 is ABC bar 3 (see module docstring), so every
    # pattern is shifted by OFFSET bars inside its own section. Sections that
    # begin later than ABC bar 3 — verse, pre-chorus, chorus — already line up
    # once the section start is applied, so the shift is applied *within* the
    # section only for the intro, which spans the offset itself.
    chords = []
    for section in base["roll"]["sections"]:
        name, start, bars = section["name"], section["start"], section["bars"]
        if name == "intro":
            # Intro music begins at ABC bar 3 (Logic bar 1). Section start is
            # ABC bar 1, so shift by OFFSET bars inside it.
            for offset, beat, symbol in expand(INTRO, bars, bars):
                tick = (start + OFFSET + offset) * bar_ticks + (beat - 1) * TICKS_PER_BEAT
                chords.append({"start": tick, "symbol": symbol})
        elif name == "verse":
            for offset, beat, symbol in expand(VERSE, bars, bars):
                tick = (start + offset) * bar_ticks + (beat - 1) * TICKS_PER_BEAT
                chords.append({"start": tick, "symbol": symbol})
        elif name == "pre-chorus":
            for offset, beat, symbol in expand(PRE, bars, bars):
                tick = (start + offset) * bar_ticks + (beat - 1) * TICKS_PER_BEAT
                chords.append({"start": tick, "symbol": symbol})
        elif name == "chorus":
            pass                      # handled below, as 16-bar pairs
        else:                         # outro: hold the tonic
            chords.append({"start": start * bar_ticks, "symbol": "C#m"})

    # Chorus: the transcription covers 16 bars (two ABC sections of 8). Apply it
    # once per pair, walking the pattern continuously.
    chorus_sections = [s for s in base["roll"]["sections"] if s["name"] == "chorus"]
    for pair in range(0, len(chorus_sections), 2):
        first = chorus_sections[pair]
        for offset, beat, symbol in expand(CHORUS, 16, 16):
            tick = (first["start"] + offset) * bar_ticks + (beat - 1) * TICKS_PER_BEAT
            chords.append({"start": tick, "symbol": symbol})

    chords.sort(key=lambda c: c["start"])

    data = inspect_score(SRC.read_text())
    data["roll"]["chords"] = chords
    result = build_score(data)

    assert result["roll"]["tracks"] == base["roll"]["tracks"], "melody changed!"
    OUT.write_text(result["abc"])

    print(f"wrote {OUT}")
    print(f"  bars   {result['bars']}")
    print(f"  chords {len(result['roll']['chords'])}")
    print(f"  vocal  {len(result['roll']['tracks']['Vocal'])}  "
          f"ins {len(result['roll']['tracks']['Ins'])}")
    print()
    for n, s in enumerate(result["roll"]["sections"], 1):
        st = s["start"] * bar_ticks
        en = st + s["bars"] * bar_ticks
        cs = [(c["start"] - st, c["symbol"]) for c in result["roll"]["chords"]
              if st <= c["start"] < en]
        rendered = []
        for rel, sym in cs:
            bar_in_section = rel // bar_ticks
            beat = (rel % bar_ticks) // TICKS_PER_BEAT + 1
            rendered.append(f"{sym}" if beat == 1 else f"{sym} b{beat}")
        print(f"  {n:2d}. {s['name']:12s} {' '.join(f'{r:>8s}' for r in rendered)}")


if __name__ == "__main__":
    main()
