"""Build alejandra_el_gran_amor_v12.abc: v11 with chords from the two renders.

Chord source, per the user's own assessment of which render sounds right:

    intro / verse / pre-chorus  <- set1 (alejandra_es_solo_amor_right_chords)
    chorus (all 6)              <- set2 (..._right_chords_2)

The two renders are at different tempos (123.4 vs 125.65 BPM), so each was
analysed against its own bar grid. Roots come from the isolated bass stem,
qualities from a vote across the repeated instances of each section.

This script does not modify the melody: it reads v11's note data, attaches the
chord list, and re-serialises through the project's own build_score, so the
result is guaranteed to be valid native YuE2 ABC.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.score import build_score, inspect_score  # noqa: E402
from tools.combine_sets import NAMES, SetAnalyser, root_pc_of  # noqa: E402

SRC = Path(__file__).resolve().parent.parent.parent / "alejandra_el_gran_amor_v11.abc"
OUT = Path(__file__).resolve().parent.parent.parent / "alejandra_el_gran_amor_v12.abc"


def best_chord(analysers, bars, root_pc):
    """Majority chord label on a fixed root across repeated instances."""
    labels = Counter()
    for an, bar in zip(analysers, bars):
        for score, label in an.ranked(bar):
            if root_pc_of(label) == root_pc:
                labels[label] = max(labels[label], score)
                break
    if not labels:
        return NAMES[root_pc]
    return labels.most_common(1)[0][0]


def root_vote(analysers, bars):
    votes = [an.bass(b) for an, b in zip(analysers, bars)]
    c = Counter(r % 12 for r in votes if r is not None)
    if not c:
        return None, []
    return c.most_common(1)[0][0], votes


def main():
    s1 = SetAnalyser(1, 123.4, 1.00)
    s2 = SetAnalyser(2, 125.65, 0.30)

    base = inspect_score(SRC.read_text())
    bar_ticks = base["roll"]["bar_ticks"]

    # Repeated instances, from v11's section map.
    VERSE = [10, 43, 75]
    PRE = [51, 83]          # these two agree; pre@18 is a longer variant
    CHORUS = [27, 35, 59, 67, 92, 100]

    # --- derive one pattern per section type -------------------------------
    def pattern(an_list, starts, length):
        out = []
        for pos in range(length):
            bars = [st + pos for st in starts]
            root_pc, votes = root_vote(an_list, bars)
            if root_pc is None:
                out.append(None)
                continue
            label = best_chord(an_list, bars, root_pc)
            agree = len({v % 12 for v in votes if v is not None})
            out.append((label, root_pc, agree, votes))
        return out

    verse_pat = pattern([s1] * len(VERSE), VERSE, 8)
    pre_pat = pattern([s1] * len(PRE), PRE, 8)
    chorus_pat = pattern([s2] * len(CHORUS), CHORUS, 8)

    # Where a seventh's confidence was barely above the plain triad, prefer the
    # triad: the extra tone is not reliably present in the render, and a simpler
    # symbol asserts less. Keep the richer chord only when every repeated
    # instance agreed on the root (agree == 1).
    def simplify(pat):
        out = []
        for label, pc, agree, votes in pat:
            rich = label.endswith(("7", "6", "sus4", "sus2", "maj7")) or "7" in label
            if rich and agree > 1:
                label = NAMES[pc] + ("m" if label[len(NAMES[pc]):].startswith("m")
                                     and not label.startswith(NAMES[pc] + "maj") else "")
            out.append((label, pc, agree, votes))
        return out

    verse_pat = simplify(verse_pat)
    pre_pat = simplify(pre_pat)
    chorus_pat = simplify(chorus_pat)

    print("verse      : " + " ".join(p[0] for p in verse_pat))
    print("pre-chorus : " + " ".join(p[0] for p in pre_pat))
    print("chorus     : " + " ".join(p[0] for p in chorus_pat))

    # --- intro: set1. The bass line here is clear (G# F# F# E E E F# F# C# F#)
    # but the *quality* is ambiguous: the top-3 candidates sit within ~0.05 of
    # each other because the section is a sparse instrumental pad. When the
    # seventh is not clearly present, a plain triad is the honest answer --
    # it asserts less and YuE2 can still colour it from the style prompt.
    INTRO = ["G#m", "F#m", "F#m", "E", "E", "E", "F#", "F#", "C#m", "F#m"]
    intro_pat = INTRO

    print("intro      : " + " ".join(intro_pat))

    # --- assemble the chord list over the real section map -----------------
    chords = []
    for section in base["roll"]["sections"]:
        name, start, bars = section["name"], section["start"], section["bars"]
        if name == "intro":
            pat = intro_pat
        elif name == "verse":
            pat = [p[0] for p in verse_pat]
        elif name == "pre-chorus":
            pat = [p[0] for p in pre_pat]
        elif name == "chorus":
            pat = [p[0] for p in chorus_pat]
        else:                                    # outro
            pat = ["C#m", "C#m"]
        for k in range(bars):
            symbol = pat[k % len(pat)]
            chords.append({"start": (start + k) * bar_ticks, "symbol": symbol})

    data = inspect_score(SRC.read_text())
    data["roll"]["chords"] = chords
    result = build_score(data)

    # --- integrity checks --------------------------------------------------
    assert result["roll"]["tracks"] == base["roll"]["tracks"], "melody changed!"
    assert len(result["roll"]["chords"]) == len(chords), "chord count changed"
    OUT.write_text(result["abc"])
    print(f"\nwrote {OUT}")
    print(f"  bars {result['bars']}   chords {len(result['roll']['chords'])}")
    print(f"  vocal {len(result['roll']['tracks']['Vocal'])}  "
          f"ins {len(result['roll']['tracks']['Ins'])}  (must match v11)")


if __name__ == "__main__":
    main()
