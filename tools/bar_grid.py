"""Find the bar grid (tempo + offset) of a rendered mix, from its drum stem.

The ABC says 123 BPM in 4/4, but a rendered mix can be a little off, and the
first downbeat does not have to sit at t=0. Getting this grid right matters:
every later conclusion depends on slicing bars correctly.

Method: onset strength envelope -> autocorrelation over plausible bar periods
-> pick the period with the strongest self-similarity -> then find the phase
(offset) that puts the strongest onsets on downbeats.
"""

from __future__ import annotations

import sys

import numpy as np

SR = 22050


def load(p):
    return np.fromfile(p, dtype="<f4")


def onset_envelope(x, sr=SR, hop=256):
    """Spectral-flux onset strength, one value per hop."""
    n_fft = 1024
    win = np.hanning(n_fft).astype(np.float32)
    frames = 1 + (len(x) - n_fft) // hop
    S = np.empty((n_fft // 2 + 1, frames), dtype=np.float32)
    for f in range(frames):
        s = f * hop
        S[:, f] = np.abs(np.fft.rfft(x[s:s + n_fft] * win))
    # Half-wave rectified difference = energy that just appeared.
    flux = np.maximum(0, np.diff(S, axis=1)).sum(axis=0)
    return flux, hop / sr


def best_grid(flux, hop_s, bpm_hint=123.0, beats=4):
    """Return (bar_period_s, offset_s, confidence)."""
    bar_hint = 60.0 / bpm_hint * beats
    # Search +/- 3% around the hinted bar length.
    candidates = np.linspace(bar_hint * 0.97, bar_hint * 1.03, 400)
    f = flux - flux.mean()
    best = (-1e18, None)
    for period in candidates:
        lag = int(round(period / hop_s))
        if lag < 2 or lag >= len(f):
            continue
        # Normalised autocorrelation: rewards periodic onset structure.
        a, b = f[:-lag], f[lag:]
        denom = np.sqrt((a * a).sum() * (b * b).sum()) or 1
        score = float((a * b).sum() / denom)
        if score > best[0]:
            best = (score, period)
    period = best[1]
    # Phase: choose the offset that lines the phase comb up with onset peaks.
    lag = int(round(period / hop_s))
    comb = np.zeros(lag)
    for i in range(0, len(flux) - lag, lag):
        comb += flux[i:i + lag]
    offset_frames = int(np.argmax(comb))
    return period, offset_frames * hop_s, best[0]


if __name__ == "__main__":
    for n in (1, 2):
        path = sys.argv[1] if len(sys.argv) > 1 else f"/tmp/drums{n}.raw"
        if len(sys.argv) == 1:
            path = f"/tmp/drums{n}.raw"
        x = load(path)
        flux, hop_s = onset_envelope(x)
        period, offset, conf = best_grid(flux, hop_s)
        print(f"set{n}: bar period {period:.4f}s  = {60/period*4:.2f} BPM  "
              f"offset {offset:.3f}s  (self-similarity {conf:.3f})")
