"""Detect the chord progression of a rendered YuE2 mix, bar by bar.

Strategy
--------
1. Load the mono float mix.
2. Slice it into bars using the timing map implied by the ABC
   (110 bars, 123 BPM, 4/4 => 1.9512 s per bar).
3. For each bar, compute a **chromagram**: 12 pitch-class energies, using an
   STFT and folding every bin onto its pitch class. Harmonic content (bass,
   chords, vocals) survives; percussion largely averages out.
4. Match each chroma against chord templates built from the 14 qualities the
   YuE2 dialect supports, in all 12 roots, and pick the best correlation.
5. Report the winning chord plus the runner-up, so low-confidence bars are
   visible instead of silently wrong.

This is deliberately a *sketch*, not a transcription. It is good at telling
`C#m` from `A` from `E` from `B`, which is exactly the decision this task needs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SR = 22050

# Pitch-class templates as semitone offsets from the root.
TEMPLATES = {
    "":      [0, 4, 7],            # major
    "m":     [0, 3, 7],            # minor
    "dim":   [0, 3, 6],
    "aug":   [0, 4, 8],
    "7":     [0, 4, 7, 10],
    "maj7":  [0, 4, 7, 11],
    "m7":    [0, 3, 7, 10],
    "dim7":  [0, 3, 6, 9],
    "m7b5":  [0, 3, 6, 10],
    "sus4":  [0, 5, 7],
    "sus2":  [0, 2, 7],
    "6":     [0, 4, 7, 9],
    "m6":    [0, 3, 7, 9],
    "7sus4": [0, 5, 7, 10],
    "m(maj7)": [0, 3, 7, 11],
}

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Spelling that reads naturally in C#m.
SPELL = {"C#": "C#", "D#": "D#", "F#": "F#", "G#": "G#", "A#": "A#",
         "C": "C", "D": "D", "E": "E", "F": "F", "G": "G", "A": "A", "B": "B"}


def load(path: str) -> np.ndarray:
    return np.fromfile(path, dtype="<f4")


def chromagram(x: np.ndarray, sr: int = SR, n_fft: int = 8192, hop: int = 2048):
    """12 pitch-class energies over time, plus frame times in seconds."""
    win = np.hanning(n_fft).astype(np.float32)
    frames = 1 + (len(x) - n_fft) // hop
    if frames <= 0:
        raise ValueError("signal shorter than one FFT window")
    # Frequencies of each FFT bin.
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    # MIDI note number for each bin; ignore DC and anything below ~55 Hz.
    with np.errstate(divide="ignore", invalid="ignore"):
        midi = 69 + 12 * np.log2(freqs / 440.0)
    valid = freqs > 55
    pitch_class = np.zeros(len(freqs), dtype=np.int32)
    pc = np.round(midi).astype(np.int32) % 12
    pitch_class[valid] = pc[valid]
    # A mild spectral whitening: normalise each bin's magnitude by a running
    # average so loud low partials do not dominate the chroma.
    chroma = np.zeros((12, frames), dtype=np.float64)
    idx = np.arange(frames) * hop
    for f, start in enumerate(idx):
        seg = x[start:start + n_fft]
        mag = np.abs(np.fft.rfft(seg * win))
        for c in range(12):
            sel = valid & (pitch_class == c)
            if sel.any():
                chroma[c, f] = mag[sel].sum()
    times = idx / sr
    # Normalise each frame to unit sum so loud/quiet bars compare fairly.
    total = chroma.sum(axis=0, keepdims=True)
    total[total == 0] = 1
    return chroma / total, times


def template_matrix() -> tuple[list[str], np.ndarray]:
    labels, rows = [], []
    for root in range(12):
        for quality, offsets in TEMPLATES.items():
            vec = np.zeros(12)
            for o in offsets:
                vec[(root + o) % 12] = 1.0
            # Weight the root and fifth a little higher: they carry the
            # perceptual identity of the chord in a dense mix.
            vec[root % 12] *= 1.35
            vec[(root + 7) % 12] *= 1.15
            vec /= np.linalg.norm(vec)
            labels.append(NAMES[root] + quality)
            rows.append(vec)
    return labels, np.vstack(rows)


def analyse(raw: str, bars: int = 110, bpm: float = 123.0, beats: int = 4,
            start_bar: int = 0, end_bar: int | None = None):
    x = load(raw)
    end_bar = bars if end_bar is None else end_bar
    chroma, times = chromagram(x)
    labels, tpl = template_matrix()

    sec_per_bar = 60.0 / bpm * beats
    results = []
    for bar in range(start_bar, end_bar):
        t0, t1 = bar * sec_per_bar, (bar + 1) * sec_per_bar
        sel = (times >= t0) & (times < t1)
        if not sel.any():
            results.append((bar, "?", 0.0, "?"))
            continue
        # Median across the bar's frames: robust to a single loud transient.
        bar_chroma = np.median(chroma[:, sel], axis=1)
        n = np.linalg.norm(bar_chroma)
        if n == 0:
            results.append((bar, "?", 0.0, "?"))
            continue
        unit = bar_chroma / n
        scores = tpl @ unit
        order = np.argsort(scores)[::-1]
        best, second = labels[order[0]], labels[order[1]]
        margin = float(scores[order[0]] - scores[order[1]])
        results.append((bar, best, margin, second))
    return results


def krumhansl_key(chroma: np.ndarray) -> str:
    """Rough global key estimate from the average chroma."""
    major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    best = (-1e9, "")
    for r in range(12):
        for prof, mode in ((major, ""), (minor, "m")):
            rotated = np.roll(prof, r)
            corr = float(np.corrcoef(chroma, rotated)[0, 1])
            if corr > best[0]:
                best = (corr, NAMES[r] + mode)
    return best[1]


if __name__ == "__main__":
    raw = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mono1.raw"
    res = analyse(raw)
    print(f"{'bar':>4} {'chord':>8} {'margin':>7}   runner-up")
    for bar, chord, margin, second in res:
        flag = "" if margin > 0.05 else "   <-- weak"
        print(f"{bar+1:>4} {chord:>8} {margin:>7.3f}   {second}{flag}")
