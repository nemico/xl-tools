"""二つの打点列で、一致数が最大になる時刻のずれ。"""

from __future__ import annotations

import numpy as np


def _mean_abs(ref_s: np.ndarray, hyp_s: np.ndarray, tol_s: float) -> float:
    ref = np.sort(ref_s)
    total = 0.0
    n = 0
    for t in hyp_s:
        i = int(np.searchsorted(ref, t))
        best = tol_s + 1
        for j in (i - 1, i):
            if 0 <= j < ref.size:
                best = min(best, abs(float(ref[j]) - float(t)))
        if best <= tol_s:
            total += best
            n += 1
    return total / n if n else 1e9


def count_matches(ref_s: np.ndarray, hyp_s: np.ndarray, tol_s: float) -> int:
    if ref_s.size == 0 or hyp_s.size == 0:
        return 0
    ref = np.sort(ref_s)
    used = np.zeros(ref.size, dtype=bool)
    n = 0
    for t in np.sort(hyp_s):
        i = int(np.searchsorted(ref, t))
        best = None
        best_dt = tol_s + 1
        for j in (i - 1, i):
            if 0 <= j < ref.size and not used[j]:
                dt = abs(float(ref[j]) - float(t))
                if dt <= tol_s and dt < best_dt:
                    best_dt = dt
                    best = j
        if best is not None:
            used[best] = True
            n += 1
    return n


def best_onset_shift_s(
    ref_s: list[float],
    hyp_s: list[float],
    *,
    tol_s: float = 0.05,
    span_s: float = 8.0,
    coarse_s: float = 0.02,
) -> float:
    """hyp に足すと ref との一致が最大になる秒。一致0なら 0。"""
    ref = np.asarray(ref_s, dtype=np.float64)
    hyp = np.asarray(hyp_s, dtype=np.float64)
    if ref.size == 0 or hyp.size == 0:
        return 0.0
    shifts = np.arange(-span_s, span_s + coarse_s * 0.5, coarse_s)
    best_shift = 0.0
    best_n = -1
    best_err = 1e9
    for shift in shifts:
        moved = hyp + float(shift)
        n = count_matches(ref, moved, tol_s)
        err = _mean_abs(ref, moved, tol_s)
        if n > best_n or (n == best_n and err < best_err):
            best_n = n
            best_err = err
            best_shift = float(shift)
    fine = np.arange(best_shift - coarse_s, best_shift + coarse_s + 0.0005, 0.001)
    for shift in fine:
        moved = hyp + float(shift)
        n = count_matches(ref, moved, tol_s)
        err = _mean_abs(ref, moved, tol_s)
        if n > best_n or (n == best_n and err < best_err):
            best_n = n
            best_err = err
            best_shift = float(shift)
    return best_shift


def third_shifts_s(ref_s: list[float], hyp_s: list[float], **kwargs) -> list[float]:
    """全体のずれを戻したあと、前半・中盤・後半の残りのずれ。"""
    if not ref_s:
        return [0.0, 0.0, 0.0]
    global_shift = best_onset_shift_s(ref_s, hyp_s, **kwargs)
    moved = [t + global_shift for t in hyp_s]
    lo, hi = min(ref_s), max(ref_s)
    edges = [lo + (hi - lo) * i / 3 for i in range(4)]
    out = []
    for a, b in zip(edges, edges[1:]):
        ref_part = [t for t in ref_s if a <= t < b or (b == edges[-1] and t == b)]
        hyp_part = [t for t in moved if a <= t < b or (b == edges[-1] and t == b)]
        out.append(best_onset_shift_s(ref_part, hyp_part, span_s=0.08, coarse_s=0.005))
    return out
