"""Build a chord sequence for YuE2 by combining both reference mixes.

The situation this solves
-------------------------
Two renders of the same song exist, each with *some* sections whose harmony the
user likes:

    set1 = alejandra_es_solo_amor_right_chords.wav    (intro / verse / pre-chorus)
    set2 = alejandra_es_solo_amor_right_chords_2.wav  (both choruses)

They were rendered at **different tempos** (123.4 vs 125.65 BPM), so bars do not
line up between them and a straight comparison is meaningless. Each set is
therefore analysed against its own grid, and its own offset is recovered by
maximising the self-similarity of sections that must repeat (verse1 vs verse3).

Method per set
--------------
* Bass root: from the isolated bass stem, the strongest note per bar. This
  proved far more reliable than any full-mix chroma estimate.
* Quality: chord templates scored against piano+other chroma, then a **vote
  across the repeated instances** of the same section. A section that appears
  three times gets three votes, which cancels most one-off errors.
"""

from __future__ import annotations

import sys
from collections import Counter

import numpy as np

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from tools.chord_from_stems import bass_profile, chroma, load, score_chords  # noqa: E402

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


class SetAnalyser:
    def __init__(self, n, bpm, offset):
        self.n, self.bpm, self.offset = n, bpm, offset
        self.spb = 60.0 / bpm * 4
        self.notes, self.bp, self.bt = bass_profile(load(f"/tmp/sbass{n}.raw"))
        self.hp, self.ht = chroma(load(f"/tmp/piano{n}.raw"))
        self.ho, _ = chroma(load(f"/tmp/other{n}.raw"))

    def bass(self, bar):
        t0 = self.offset + bar * self.spb
        sel = (self.bt >= t0) & (self.bt < t0 + self.spb)
        if not sel.any():
            return None
        prof = np.median(self.bp[:, sel], axis=1)
        return int(self.notes[int(np.argmax(prof))])

    def chroma_at(self, bar):
        t0 = self.offset + bar * self.spb
        sel = (self.ht >= t0) & (self.ht < t0 + self.spb)
        if not sel.any():
            return None
        u = 0.6 * np.median(self.hp[:, sel], axis=1) + 0.4 * np.median(self.ho[:, sel], axis=1)
        n = np.linalg.norm(u)
        return u / n if n > 0 else None

    def ranked(self, bar):
        u = self.chroma_at(bar)
        return score_chords(u) if u is not None else []


def root_pc_of(label):
    """Pitch class of a chord label like 'C#m7' or 'A'."""
    if label in ("", "?"):
        return None
    if len(label) > 1 and label[1] in "#b":
        root = label[:2]
    else:
        root = label[:1]
    norm = root.replace("Db", "C#").replace("Eb", "D#").replace("Gb", "F#") \
                .replace("Ab", "G#").replace("Bb", "A#")
    return NAMES.index(norm) if norm in NAMES else None


def vote_chord(analysers_bars, pc):
    """Pick the chord whose template best matches, restricted to a known root.

    `analysers_bars` is a list of (analyser, bar) for the repeated instances.
    Only candidates on the agreed root are considered, because the root came
    from the bass stem and is more trustworthy than the chroma match.
    """
    totals = Counter()
    for an, bar in analysers_bars:
        for score, label in an.ranked(bar):
            if pc is None or root_pc_of(label) == pc:
                totals[label] = max(totals[label], score)
    if not totals:
        return None, 0.0
    best, sc = totals.most_common(1)[0]
    return best, sc


def main():
    # Offsets recovered by maximising section self-similarity.
    s1 = SetAnalyser(1, 123.4, 1.00)
    s2 = SetAnalyser(2, 125.65, 0.30)

    # Repeated instances, per the ABC section map.
    verses = [10, 43, 75]
    pres = [18, 51, 83]
    choruses = [27, 35, 59, 67, 92, 100]

    print("=" * 78)
    print("BASS ROOT per bar  (bass stem = most reliable signal)")
    print("=" * 78)
    for label, base, an in (("verse", verses, s1), ("pre", pres, s1),
                            ("chorus", choruses, s2), ("intro", [0], s1)):
        if label == "intro":
            roots = [an.bass(b) for b in range(0, 10)]
            print(f"  intro    : {' '.join(NAMES[r % 12] if r else '?' for r in roots)}")
            continue
        print(f"  {label}:")
        for pos in range(8):
            rs = [an.bass(b + pos) for b in base]
            names = [NAMES[r % 12] if r is not None else "?" for r in rs]
            agree = len(set(x for x in rs if x is not None)) == 1
            print(f"    c{pos+1}: {names} {'AGREE' if agree else ''}")

    print()
    print("=" * 78)
    print("CHORD VOTE per position  (quality by majority across instances)")
    print("=" * 78)
    for label, base, an in (("verse", verses, s1), ("pre-chorus", pres, s1),
                            ("chorus", choruses, s2)):
        print(f"  {label}:")
        for pos in range(8):
            pairs = [(an, b + pos) for b in base]
            roots = [an.bass(b) for _, b in pairs]
            rc = Counter(r % 12 for r in roots if r is not None)
            root_pc = rc.most_common(1)[0][0] if rc else None
            label_best, sc = vote_chord(pairs, root_pc)
            cons = rc.most_common(2)
            print(f"    c{pos+1}: root={NAMES[root_pc] if root_pc is not None else '?':>3s} "
                  f"(votes {[NAMES[r]+'x'+str(c) for r,c in cons]})  chord={label_best}  {sc:.2f}")


if __name__ == "__main__":
    main()
