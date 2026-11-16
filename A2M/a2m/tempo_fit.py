"""小節頭の時刻（秒）から、整数BPMのテンポマップを作る（M1）。

人のやり方と同じ：テンポは整数BPMだけを使い、グリッドが原曲からずれてきたら
小節の頭でBPMを変えて追いかける。区間はなるべく長くする。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TempoSegment:
    bar_index: int  # この小節の頭からこのBPM
    bpm: int


@dataclass(frozen=True)
class TempoFit:
    segments: list[TempoSegment]
    grid_bar_times: np.ndarray  # 当てはめたグリッドの小節頭（秒）。長さは小節数+1
    drift_s: np.ndarray  # グリッド − 原曲（秒）。長さは小節数+1

    @property
    def max_abs_drift_s(self) -> float:
        return float(np.max(np.abs(self.drift_s)))


def smooth_beat_times(beats: np.ndarray, window: int, max_dev_ratio: float) -> np.ndarray:
    """拍の時刻の細かい揺れ（推定器の時間刻み）をならす。

    拍の間隔がそろっている区間ごとに、前後 window 拍の直線当てはめで置き換える。
    間隔が中央値から max_dev_ratio 以上外れた所（拍の抜け・テンポの切り替わり）はまたがない。
    """
    beats = np.asarray(beats, dtype=float)
    out = beats.copy()
    if len(beats) < 3:
        return out
    ibi = np.diff(beats)
    med = float(np.median(ibi))
    breaks = [0, *(np.where(np.abs(ibi - med) > max_dev_ratio * med)[0] + 1).tolist(), len(beats)]
    half = window // 2
    for a, b in zip(breaks[:-1], breaks[1:]):
        n = b - a
        if n < 3:
            continue
        idx = np.arange(n, dtype=float)
        seg = beats[a:b]
        for k in range(n):
            lo, hi = max(0, k - half), min(n, k + half + 1)
            slope, intercept = np.polyfit(idx[lo:hi], seg[lo:hi], 1)
            out[a + k] = slope * k + intercept
    return out


def bars_from_beats(beats: np.ndarray, downbeats: np.ndarray, match_tol_s: float) -> tuple[np.ndarray, np.ndarray]:
    """拍と小節頭の時刻から、(小節頭の時刻, 各小節の拍数) を返す。

    小節頭の時刻は、対応する拍の時刻（ならした後の beats）を使う。長さは 小節数+1。
    """
    beats = np.asarray(beats, dtype=float)
    downbeats = np.asarray(downbeats, dtype=float)
    if len(downbeats) < 2:
        return downbeats.copy(), np.zeros(0, dtype=int)
    idx = np.clip(np.searchsorted(beats, downbeats - match_tol_s), 0, len(beats) - 1)
    keep = np.concatenate([[True], np.diff(idx) > 0])
    idx = idx[keep]
    return beats[idx], np.diff(idx).astype(int)


def _run_length(
    bar_dur: np.ndarray, bar_beats: np.ndarray, start: int, bpm: int, drift: float, tol_s: float, patience: int
) -> tuple[int, float]:
    """bpm のまま何小節進めるか。

    ずれが tol_s を超えた小節が patience 個続いたら、超え始める手前で止まる。
    1小節だけの外れ（拍の推定の誤差）では止まらない。
    """
    good_j, good_drift = start, drift
    streak = 0
    j = start
    while j < len(bar_dur):
        drift = drift + bar_beats[j] * 60.0 / bpm - bar_dur[j]
        j += 1
        if abs(drift) > tol_s:
            streak += 1
            if streak >= patience:
                break
        else:
            streak = 0
            good_j, good_drift = j, drift
    return good_j - start, good_drift


def fit_integer_tempo(
    bar_times: np.ndarray,
    bar_beats: np.ndarray,
    *,
    tol_s: float,
    bpm_min: int,
    bpm_max: int,
    search_bpm: int,
    patience: int = 1,
) -> TempoFit:
    """小節頭の時刻と各小節の拍数（4分音符）から、整数BPMの区間を決める。

    最後に、ずれの中央値が0になるようにグリッド全体を平行移動する
    （最初の小節頭の推定誤差を全体に引きずらないため）。
    """
    bar_times = np.asarray(bar_times, dtype=float)
    bar_beats = np.asarray(bar_beats, dtype=float)
    n = len(bar_beats)
    if len(bar_times) != n + 1:
        raise ValueError("bar_times は小節数+1 個必要")
    bar_dur = np.diff(bar_times)
    segments: list[TempoSegment] = []
    drift = 0.0
    drifts = [0.0]
    i = 0
    prev: int | None = None
    while i < n:
        center = int(round(bar_beats[i] * 60.0 / bar_dur[i]))
        cands = {int(np.clip(b, bpm_min, bpm_max)) for b in range(center - search_bpm, center + search_bpm + 1)}
        if prev is not None:
            cands.add(prev)
        best: tuple[int, float, int, int, float] | None = None
        for b in sorted(cands):
            run, end = _run_length(bar_dur, bar_beats, i, b, drift, tol_s, patience)
            key = (run, -abs(end), 1 if b == prev else 0)
            if best is None or key > best[:3]:
                best = (*key, b, end)
        run, bpm, end = best[0], best[3], best[4]
        if run == 0:
            # どのBPMでも許容に収まらない小節：ずれが一番小さくなるBPMで1小節だけ進む
            bpm = min(sorted(cands), key=lambda b: abs(drift + bar_beats[i] * 60.0 / b - bar_dur[i]))
            run = 1
        if bpm != prev:
            segments.append(TempoSegment(bar_index=i, bpm=bpm))
            prev = bpm
        for j in range(i, i + run):
            drift = drift + bar_beats[j] * 60.0 / bpm - bar_dur[j]
            drifts.append(drift)
        i += run
    drift_arr = np.asarray(drifts)
    drift_arr = drift_arr - np.median(drift_arr)
    return TempoFit(segments=segments, grid_bar_times=bar_times + drift_arr, drift_s=drift_arr)


def master_bpm(segments: list[TempoSegment], grid_bar_times: np.ndarray) -> int:
    """一番長い時間演奏されるBPM（仕様：M1 に入れる Master Tempo）。"""
    total: dict[int, float] = {}
    for k, seg in enumerate(segments):
        end_bar = segments[k + 1].bar_index if k + 1 < len(segments) else len(grid_bar_times) - 1
        total[seg.bpm] = total.get(seg.bpm, 0.0) + float(grid_bar_times[end_bar] - grid_bar_times[seg.bar_index])
    return max(total, key=lambda b: (total[b], -b))
