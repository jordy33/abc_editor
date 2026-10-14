"""Estimate the bass note per bar, which is the strongest harmony cue.

In a dense mix the bass line carries the chord root far more reliably than the
full spectrum does: vocals and cymbals smear the upper chroma, but the lowest
strong partial in a bar is almost always the root (or an inversion's bass note,
which is equally useful).

Approach: low-pass the mix, then track the fundamental per bar.
"""

from __future__ import annotations

import sys

import numpy as np

SR = 22050
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def load(path):
    return np.fromfile(path, dtype="<f4")


def midi_to_name(m: int) -> str:
    return NAMES[m % 12] + str(m // 12 - 1)


def note_energy(x, sr=SR, n_fft=16384, hop=4096):
    """Per-frame energy at each MIDI note in the bass region (E1..E4 / 28..64)."""
    win = np.hanning(n_fft).astype(np.float32)
    frames = 1 + (len(x) - n_fft) // hop
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    notes = np.arange(28, 65)
    # Bin index nearest each note's frequency.
    bins = np.array([int(np.argmin(np.abs(freqs - 440 * 2 ** ((n - 69) / 12)))) for n in notes])
    out = np.zeros((len(notes), frames))
    for f in range(frames):
        s = f * hop
        mag = np.abs(np.fft.rfft(x[s:s + n_fft] * win))
        # Sum a small neighbourhood so slight tuning drift does not split energy.
        for i, b in enumerate(bins):
            out[i, f] = mag[max(0, b - 2):b + 3].sum()
    times = np.arange(frames) * hop / sr
    return notes, out, times


def per_bar(notes, energy, times, bars=110, bpm=123.0, beats=4):
    spb = 60.0 / bpm * beats
    rows = []
    for bar in range(bars):
        t0, t1 = bar * spb, (bar + 1) * spb
        sel = (times >= t0) & (times < t1)
        if not sel.any():
            rows.append((bar, None, 0.0, []))
            continue
        # Median across the bar: the sustained bass note, not a passing tone.
        prof = np.median(energy[:, sel], axis=1)
        order = np.argsort(prof)[::-1]
        top = [(int(notes[i]), float(prof[i])) for i in order[:4]]
        best = top[0][0]
        # Confidence: how much the winner beats the best note a semitone away,
        # which is the hardest distinction to make.
        rest = [p for n, p in top[1:] if abs(n - best) > 1]
        margin = (top[0][1] - (max(rest) if rest else 0)) / (top[0][1] or 1)
        rows.append((bar, best, margin, top))
    return rows


if __name__ == "__main__":
    raw = sys.argv[1] if len(sys.argv) > 1 else "/tmp/bass1.raw"
    x = load(raw)
    notes, energy, times = note_energy(x)
    print(f"{'bar':>4} {'bass':>6} {'midi':>5} {'conf':>6}   top-4 notes")
    for bar, best, margin, top in per_bar(notes, energy, times):
        if best is None:
            print(f"{bar+1:>4} {'?':>6}")
            continue
        names = " ".join(midi_to_name(n) for n, _ in top)
        print(f"{bar+1:>4} {midi_to_name(best):>6} {best:>5} {margin:>6.2f}   {names}")
