"""Locate section boundaries in a rendered mix, using the drum stem.

Rather than trusting the ABC's bar count, this finds where the arrangement
actually changes: energy drops at the end of a section, and the drum pattern
often shifts. It prints an energy profile per bar so the structure is visible.
"""

from __future__ import annotations

import sys

import numpy as np

SR = 22050


def load(p):
    return np.fromfile(p, dtype="<f4")


def rms_per_bar(x, bar_period, offset, bars):
    out = []
    for b in range(bars):
        t0 = offset + b * bar_period
        t1 = t0 + bar_period
        a, z = int(t0 * SR), int(t1 * SR)
        if a >= len(x) or z <= a:
            out.append(0.0)
            continue
        seg = x[max(0, a):z]
        out.append(float(np.sqrt((seg ** 2).mean())))
    return np.array(out)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    bpm = float(sys.argv[2]) if len(sys.argv) > 2 else 123.0
    offset = float(sys.argv[3]) if len(sys.argv) > 3 else 0.87
    stem = sys.argv[4] if len(sys.argv) > 4 else "drums"
    bars = int(sys.argv[5]) if len(sys.argv) > 5 else 115

    x = load(f"/tmp/{stem}{n}.raw")
    bar_period = 60.0 / bpm * 4
    rms = rms_per_bar(x, bar_period, offset, bars)
    peak = rms.max() or 1
    print(f"set{n} {stem}: bar={bar_period:.4f}s offset={offset:.3f}s")
    print(f"{'bar':>4}  {'level':>6}  bar-graph")
    for b, v in enumerate(rms):
        rel = v / peak
        print(f"{b+1:>4}  {rel:>6.3f}  {'#' * int(rel * 50)}")


if __name__ == "__main__":
    main()
