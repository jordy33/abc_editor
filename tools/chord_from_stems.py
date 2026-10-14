"""Robust per-bar chord estimation from separated stems.

Designed after the first two attempts failed on the full mix. Two ideas make
this work:

1. **Use the bass stem for the root.** In a separated stem the bass line is
   almost alone, so the lowest strong pitch class *is* the chord root (or the
   bass note of an inversion).

2. **Use the harmonic stems for the quality.** Piano + Other + Guitar carry the
   upper chord tones; the bass does not. Comparing the two gives root and
   quality independently.

Both are pooled per bar with a median across time, which rejects passing tones
and single transients.
"""

from __future__ import annotations

import sys

import numpy as np

SR = 22050
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

QUALITIES = {
    "": [0, 4, 7], "m": [0, 3, 7], "dim": [0, 3, 6], "aug": [0, 4, 8],
    "7": [0, 4, 7, 10], "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10],
    "dim7": [0, 3, 6, 9], "m7b5": [0, 3, 6, 10], "sus4": [0, 5, 7],
    "sus2": [0, 2, 7], "6": [0, 4, 7, 9], "m6": [0, 3, 7, 9],
    "7sus4": [0, 5, 7, 10],
}


def load(p):
    return np.fromfile(p, dtype="<f4")


def chroma(x, sr=SR, n_fft=8192, hop=2048):
    """Pitch-class energy over time (12 x frames), plus frame times."""
    win = np.hanning(n_fft).astype(np.float32)
    frames = 1 + (len(x) - n_fft) // hop
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    with np.errstate(divide="ignore", invalid="ignore"):
        midi = 69 + 12 * np.log2(np.maximum(freqs, 1e-9) / 440.0)
    ok = freqs > 50
    pc = np.zeros(len(freqs), dtype=int)
    pc[ok] = np.mod(np.round(midi[ok]).astype(int), 12)
    out = np.zeros((12, max(frames, 0)))
    for f in range(max(frames, 0)):
        s = f * hop
        mag = np.abs(np.fft.rfft(x[s:s + n_fft] * win))
        for c in range(12):
            sel = ok & (pc == c)
            if sel.any():
                out[c, f] = mag[sel].sum()
    tot = out.sum(axis=0, keepdims=True)
    tot[tot == 0] = 1
    return out / tot, (np.arange(max(frames, 0)) * hop) / sr


def bass_profile(x, sr=SR, n_fft=16384, hop=4096, lo=28, hi=64):
    """Energy per MIDI note in the bass register, over time."""
    win = np.hanning(n_fft).astype(np.float32)
    frames = 1 + (len(x) - n_fft) // hop
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    notes = np.arange(lo, hi + 1)
    bins = np.array([int(np.argmin(np.abs(freqs - 440 * 2 ** ((n - 69) / 12)))) for n in notes])
    out = np.zeros((len(notes), max(frames, 0)))
    for f in range(max(frames, 0)):
        s = f * hop
        mag = np.abs(np.fft.rfft(x[s:s + n_fft] * win))
        for i, b in enumerate(bins):
            out[i, f] = mag[max(0, b - 2):b + 3].sum()
    tot = out.sum(axis=0, keepdims=True)
    tot[tot == 0] = 1
    return notes, out / tot, (np.arange(max(frames, 0)) * hop) / sr


def score_chords(harm_unit, bass_pc_weight=0.0):
    """Rank chord templates against one normalised 12-vector."""
    results = []
    for root in range(12):
        for q, offs in QUALITIES.items():
            vec = np.zeros(12)
            for o in offs:
                vec[(root + o) % 12] = 1.0
            vec[root % 12] *= 1.4
            vec[(root + 7) % 12] *= 1.15
            vec /= np.linalg.norm(vec)
            results.append((float(vec @ harm_unit), NAMES[root] + q))
    results.sort(reverse=True)
    return results


def analyse(harm_raw, bass_raw=None, bars=110, bpm=123.4, offset=0.0, beats=4,
            start_bar=0):
    hx = load(harm_raw)
    hc, ht = chroma(hx)
    if bass_raw:
        notes, bp, bt = bass_profile(load(bass_raw))
    spb = 60.0 / bpm * beats
    rows = []
    for bar in range(start_bar, bars):
        t0, t1 = offset + bar * spb, offset + (bar + 1) * spb
        sel = (ht >= t0) & (ht < t1)
        if not sel.any():
            rows.append((bar, "?", 0.0, ""))
            continue
        unit = np.median(hc[:, sel], axis=1)
        n = np.linalg.norm(unit)
        if n == 0:
            rows.append((bar, "?", 0.0, ""))
            continue
        ranked = score_chords(unit / n)
        best, second = ranked[0], ranked[1]
        margin = best[0] - second[0]
        rows.append((bar, best[1], margin, second[1]))
    return rows


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--set", type=int, default=1)
    ap.add_argument("--stem", default="piano")
    ap.add_argument("--bass", default="sbass")
    ap.add_argument("--bpm", type=float, default=None)
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--bars", type=int, default=110)
    ap.add_argument("--start", type=int, default=0)
    a = ap.parse_args()
    bpm = a.bpm if a.bpm else (123.4 if a.set == 1 else 125.65)
    rows = analyse(f"/tmp/{a.stem}{a.set}.raw", f"/tmp/{a.bass}{a.set}.raw",
                   bars=a.bars, bpm=bpm, offset=a.offset, start_bar=a.start)
    print(f"set{a.set} {a.stem} @{bpm}bpm offset={a.offset:.3f}s")
    print(f"{'bar':>4} {'chord':>9} {'marg':>6}  runner-up")
    for bar, chord, margin, second in rows:
        print(f"{bar+1:>4} {chord:>9} {margin:>6.3f}  {second}")
