"""粗いグリッドを、音の立ち上がりに合わせて細かく合わせ直す（M1）。

人のやり方：整数BPMのまま進め、原曲からずれてきたらBPMを変えて戻す。
ここでは「立ち上がりからのずれ」と「テンポ変更の回数」の合計が一番小さくなる
整数BPMの並びを、動的計画法で求める。
"""

from __future__ import annotations

import numpy as np

from a2m.beat_grid import BeatGrid
from a2m.tempo_fit import TempoSegment


def pick_onsets(y: np.ndarray, sr: int, *, hop: int, n_fft: int, delta: float, min_gap_s: float) -> tuple[np.ndarray, np.ndarray]:
    """音の立ち上がりの (時刻, 強さ) を返す。"""
    import librosa

    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, n_fft=n_fft, aggregate=np.median)
    frames = librosa.onset.onset_detect(
        onset_envelope=env, sr=sr, hop_length=hop, units="frames", backtrack=False, delta=delta,
        wait=max(1, int(min_gap_s * sr / hop)),
    )
    return frames * hop / sr, env[frames]


def snap_offsets(grid: np.ndarray, on_t: np.ndarray, on_s: np.ndarray, window_s: float, min_strength: float) -> tuple[np.ndarray, np.ndarray]:
    """グリッドの各点について、近くの一番強い立ち上がりとの差（立ち上がり − グリッド）と、見つかったかどうか。"""
    offsets = np.zeros(len(grid))
    found = np.zeros(len(grid), dtype=bool)
    lo = np.searchsorted(on_t, grid - window_s)
    hi = np.searchsorted(on_t, grid + window_s)
    for k in range(len(grid)):
        if hi[k] > lo[k]:
            j = lo[k] + int(np.argmax(on_s[lo[k] : hi[k]]))
            if on_s[j] >= min_strength:
                offsets[k] = on_t[j] - grid[k]
                found[k] = True
    return offsets, found


def fit_tempo_dp(
    ref: np.ndarray,
    offsets: np.ndarray,
    weights: np.ndarray,
    bar_head: np.ndarray,
    bpms: list[int],
    *,
    free_s: float,
    cost_bar_s: float,
    cost_beat_s: float,
    drift_max_s: float,
    bin_s: float,
) -> tuple[np.ndarray, float]:
    """整数BPMの並びを決める。

    ref: 基準のグリッド（4分音符ごとの秒）。offsets: 各点の目標 − ref。weights: 目標の重み（0 なら無視）。
    bar_head: その点が小節頭か。小節頭以外でのテンポ変更は cost_beat_s、小節頭では cost_bar_s。
    返り値: (各区間のBPM（長さ N-1）, 最初の点の時刻)
    """
    n = len(ref)
    nb = len(bpms)
    half = int(round(drift_max_s / bin_s))
    nd = 2 * half + 1
    drift = (np.arange(nd) - half) * bin_s  # 新しいグリッド − ref

    def node(k: int) -> np.ndarray:
        return weights[k] * np.maximum(0.0, np.abs(drift - offsets[k]) - free_s)

    periods = 60.0 / np.asarray(bpms, dtype=float)
    idx = np.arange(n)
    u = np.round((idx[None, :] * periods[:, None] - (ref - ref[0])[None, :]) / bin_s).astype(np.int64)
    shift = np.diff(u, axis=1)  # (nb, n-1)
    cost = np.tile(node(0), (nb, 1))
    src = np.zeros((n - 1, nb, nd), dtype=np.int16)
    own = np.arange(nb)[:, None]
    for k in range(n - 1):
        lam = 0.0 if k == 0 else (cost_bar_s if bar_head[k] else cost_beat_s)
        best_b = np.argmin(cost, axis=0)
        switch = cost[best_b, np.arange(nd)] + lam
        stay = cost <= switch[None, :]
        best = np.where(stay, cost, switch[None, :])
        src[k] = np.where(stay, own, best_b[None, :])
        nxt = np.full((nb, nd), np.inf)
        nk = node(k + 1)
        for b in range(nb):
            s = int(shift[b, k])
            if s >= nd or s <= -nd:
                continue
            if s >= 0:
                nxt[b, s:] = best[b, : nd - s]
            else:
                nxt[b, : nd + s] = best[b, -s:]
        cost = nxt + nk[None, :]
    b, d = np.unravel_index(int(np.argmin(cost)), cost.shape)
    out = np.zeros(n - 1, dtype=int)
    for k in range(n - 2, -1, -1):
        out[k] = bpms[b]
        d = d - int(shift[b, k])
        b = int(src[k, b, d])
    return out, float(ref[0] + (d - half) * bin_s)


def refine_grid(
    coarse: BeatGrid,
    on_t: np.ndarray,
    on_s: np.ndarray,
    *,
    windows_s: list[float],
    window_ratio: float,
    strength_quantile: float,
    bpm_margin: int,
    bpm_min: int,
    bpm_max: int,
    free_s: float,
    cost_bar_s: float,
    cost_beat_s: float,
    drift_max_s: float,
    bin_s: float,
) -> BeatGrid:
    """粗いグリッドを、立ち上がりに合わせ直す。拍の数と小節頭の位置は変えない。"""
    ref = np.asarray(coarse.grid_times, dtype=float)
    if len(ref) < 3 or len(on_t) == 0:
        return coarse
    used = [s.bpm for s in coarse.segments]
    bpms = list(range(max(bpm_min, min(used) - bpm_margin), min(bpm_max, max(used) + bpm_margin) + 1))
    bar_head = np.zeros(len(ref), dtype=bool)
    bar_head[np.asarray(coarse.bar_idx, dtype=int)] = True
    min_strength = float(np.quantile(on_s, strength_quantile))
    period = float(np.median(np.diff(ref)))
    seq = None
    for window in windows_s:
        offsets, found = snap_offsets(ref, on_t, on_s, min(window, window_ratio * period), min_strength)
        seq, t0 = fit_tempo_dp(
            ref, offsets, found.astype(float), bar_head, bpms, free_s=free_s, cost_bar_s=cost_bar_s,
            cost_beat_s=cost_beat_s, drift_max_s=drift_max_s, bin_s=bin_s,
        )
        ref = np.concatenate([[t0], t0 + np.cumsum(60.0 / seq)])
    segments = [TempoSegment(0, int(seq[0]))]
    for k in range(1, len(seq)):
        if seq[k] != seq[k - 1]:
            segments.append(TempoSegment(k, int(seq[k])))
    return BeatGrid(ref, segments, coarse.beats_per_bar, coarse.bar_idx, len(segments) == 1)


def grid_residuals(grid: np.ndarray, on_t: np.ndarray, on_s: np.ndarray, window_s: float, strength_quantile: float) -> tuple[np.ndarray, np.ndarray]:
    """グリッドの近くにある強い立ち上がりの (時刻, 立ち上がり − グリッド)。"""
    offsets, found = snap_offsets(np.asarray(grid), on_t, on_s, window_s, float(np.quantile(on_s, strength_quantile)))
    return np.asarray(grid)[found] + offsets[found], offsets[found]


def window_medians(t: np.ndarray, r: np.ndarray, window_s: float, min_count: int) -> np.ndarray:
    """ずれの、時間窓ごとの中央値。"""
    if len(t) == 0:
        return np.zeros(0)
    out = []
    for a in np.arange(t.min(), t.max(), window_s):
        sel = (t >= a) & (t < a + window_s)
        if sel.sum() >= min_count:
            out.append(float(np.median(r[sel])))
    return np.asarray(out)
