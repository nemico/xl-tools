"""拍の推定から、整数BPMのグリッド（4分音符）を作る（M1）。

拍の列をそのまま信じない。推定器は拍を抜かしたり倍で取ったりするので、
「整数BPMのグリッドに拍がどれだけ乗るか」で決める。8分音符の位置の拍も「乗った」と数える。

1. 曲全体が1つの整数BPMに乗るかを先に試す。
2. 乗らなければ、乗らなくなった所（なるべく小節頭）でBPMを変えていく。
3. 小節頭は、推定された小節頭の多数決で決める。途中で位置がずれて、ずれたまま続くなら、
   そこに半端な小節が入ったとみなす（2/4 の挿入など）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from a2m.tempo_fit import TempoSegment

SUBDIV = 2  # 8分音符の位置まで「グリッドに乗った」とみなす


@dataclass(frozen=True)
class BeatGrid:
    grid_times: np.ndarray  # 4分音符ごとのグリッド（秒）
    segments: list[TempoSegment]  # bar_index は「グリッドの番号」（4分音符）で数える
    beats_per_bar: int
    bar_idx: np.ndarray  # 小節頭のグリッド番号
    constant: bool  # 曲全体が1つの整数BPMに乗ったか

    @property
    def bar_times(self) -> np.ndarray:
        return self.grid_times[self.bar_idx]


def _residuals(beats: np.ndarray, t0: float, period: float) -> np.ndarray:
    n = np.round((beats - t0) / period)
    return beats - (t0 + n * period)


def best_constant_grid(beats: np.ndarray, bpm_candidates: list[int], tol_s: float) -> tuple[int, float, float]:
    """1つの整数BPMのグリッドに、拍が一番よく乗る (BPM, 基準時刻, 乗った割合) を返す。"""
    best = (bpm_candidates[0], float(beats[0]), -1.0, np.inf)
    for bpm in bpm_candidates:
        period = 60.0 / bpm
        sub = period / SUBDIV
        ang = 2 * np.pi * (beats % sub) / sub
        t0 = float(np.angle(np.mean(np.exp(1j * ang))) / (2 * np.pi) * sub)
        t0 += float(np.median(_residuals(beats, t0, sub)))
        res = np.abs(_residuals(beats, t0, sub))
        share, mean = float(np.mean(res <= tol_s)), float(np.mean(res))
        # 4分音符の位置は、乗った拍が多いほう
        hits = [int(np.sum(np.abs(_residuals(beats, t0 + j * sub, period)) <= tol_s)) for j in range(SUBDIV)]
        t0 += int(np.argmax(hits)) * sub
        if (share, -mean) > (best[2], -best[3]):
            best = (bpm, t0, share, mean)
    return best[0], best[1], best[2]


def global_bpm(beats: np.ndarray) -> float:
    """曲全体のBPM。拍の間隔の、時間で重みを付けた中央値から出す。

    一部の区間だけ倍で取られていても、曲の時間の大半を占めるテンポを選ぶ。
    """
    ibi = np.sort(np.diff(beats))
    cum = np.cumsum(ibi)
    return 60.0 / float(ibi[int(np.searchsorted(cum, cum[-1] / 2.0))])


def fold_bpm(bpm: float, bpm_min: int, bpm_max: int) -> float:
    """BPM が範囲の外なら、半分か倍にして範囲に入れる。"""
    while bpm > bpm_max:
        bpm /= 2.0
    while bpm < bpm_min:
        bpm *= 2.0
    return bpm


def local_bpm(beats: np.ndarray, k: int, span: int) -> float:
    """k 番目の拍から span 拍ぶんの、拍の間隔の中央値から出したBPM。"""
    ibi = np.diff(beats[k : k + span + 1])
    if len(ibi) == 0:
        ibi = np.diff(beats[-span - 1 :])
    return 60.0 / float(np.median(ibi))


def run_on_grid(
    beats: np.ndarray, k0: int, t0: float, bpm: int, tol_s: float, patience: int, subdiv: int = 1
) -> tuple[int, int, float]:
    """t0 から bpm のグリッドに、k0 以降の拍がどこまで乗るか。

    外れた拍が patience 個続いたら、その手前で止まる。
    返り値: (乗っていた最後の拍の次の番号, その拍のグリッド番号（4分音符）, 乗っていた拍のずれの平均)
    """
    sub = 60.0 / bpm / subdiv
    good_k, good_n, streak, total, count = k0, 0, 0, 0.0, 0
    for k in range(k0, len(beats)):
        n = int(round((beats[k] - t0) / sub))
        res = abs(beats[k] - (t0 + n * sub))
        if res > tol_s:
            streak += 1
            if streak >= patience:
                break
        else:
            streak = 0
            good_k, good_n = k + 1, n // subdiv
            total += res
            count += 1
    return good_k, good_n, total / max(count, 1)


def _piecewise(
    beats: np.ndarray,
    *,
    tol_s: float,
    bpm_min: int,
    bpm_max: int,
    search_bpm: int,
    patience: int,
    local_span: int,
    bar_idx: np.ndarray | None,
    main_bpm: int,
    main_ratio: float,
) -> tuple[np.ndarray, list[TempoSegment]]:
    t = float(beats[0])
    k, n_total, prev = 0, 0, None
    grid = [t]
    segments: list[TempoSegment] = []
    while k < len(beats):
        center = int(round(fold_bpm(local_bpm(beats, k, local_span), bpm_min, bpm_max)))
        cands = {int(np.clip(b, bpm_min, bpm_max)) for b in range(center - search_bpm, center + search_bpm + 1)}
        cands.add(main_bpm)
        if prev is not None:
            cands.add(prev)
        runs = {b: run_on_grid(beats, k, t, b, tol_s, patience) for b in sorted(cands)}
        bpm = max(runs, key=lambda b: (runs[b][0], -runs[b][2], b == prev))
        # 曲の主なBPMでも十分進めるなら、そちらを使う（余計なテンポ変更を入れない）
        if runs[main_bpm][0] > k and runs[main_bpm][0] - k >= main_ratio * (runs[bpm][0] - k):
            bpm = main_bpm
        end_k, end_n, _ = runs[bpm]
        n_seg = end_n
        if end_k >= len(beats):
            n_seg = max(end_n, 1)
        elif n_seg > 0 and bar_idx is not None and len(bar_idx):
            # 小節頭まで戻して、そこでBPMを変える
            j = int(np.searchsorted(bar_idx, n_total + n_seg, side="right")) - 1
            if j >= 0 and bar_idx[j] > n_total:
                n_seg = int(bar_idx[j] - n_total)
        if n_seg <= 0:
            # どのBPMでも乗らない：その場のテンポで1拍だけ進む
            bpm = int(np.clip(center, bpm_min, bpm_max))
            n_seg = 1
        if bpm != prev:
            segments.append(TempoSegment(bar_index=n_total, bpm=bpm))
            prev = bpm
        period = 60.0 / bpm
        grid.extend(t + (i + 1) * period for i in range(n_seg))
        t += n_seg * period
        n_total += n_seg
        k = int(np.searchsorted(beats, t + period / 2))
    return np.asarray(grid), segments


def _merge_phase_runs(runs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """同じ位置が続く区間をまとめる。A→B→A と元に戻るものは、B を推定の誤りとみなして捨てる。"""
    merged: list[tuple[int, int]] = []
    for r in runs:
        if not merged or r[1] != merged[-1][1]:
            merged.append(r)
    changed = True
    while changed:
        changed = False
        for i in range(1, len(merged) - 1):
            if merged[i - 1][1] == merged[i + 1][1]:
                merged = merged[:i] + merged[i + 2 :]
                changed = True
                break
    return merged


def bar_heads(grid: np.ndarray, downbeats: np.ndarray, min_run: int) -> tuple[int, np.ndarray]:
    """推定された小節頭から、(1小節の拍数, 小節頭のグリッド番号の列) を決める。

    小節頭の位置（拍数で割った余り）が同じまま min_run 個以上続く区間を「確かな区間」とし、
    区間が切り替わる所には半端な小節を1つ入れる。短い揺れは無視する。
    """
    n = len(grid)
    idx = np.clip(np.searchsorted(grid, downbeats), 1, n - 1)
    idx = np.where(np.abs(grid[idx - 1] - downbeats) < np.abs(grid[idx] - downbeats), idx - 1, idx)
    idx = np.unique(idx)
    diffs = np.diff(idx)
    diffs = diffs[(diffs >= 2) & (diffs <= 12)]
    bpb = int(np.bincount(diffs).argmax()) if len(diffs) else 4
    while bpb > 5 and bpb % 2 == 0:
        bpb //= 2
    if len(idx) == 0:
        return bpb, np.arange(0, n, bpb)
    phases = idx % bpb
    runs: list[tuple[int, int]] = []  # (最初の小節頭のグリッド番号, 余り)
    start = 0
    for i in range(1, len(idx) + 1):
        if i == len(idx) or phases[i] != phases[start]:
            if i - start >= min_run:
                runs.append((int(idx[start]), int(phases[start])))
            start = i
    if not runs:
        runs = [(int(idx[0]), int(np.bincount(phases).argmax()))]
    merged = _merge_phase_runs(runs)
    heads: list[int] = []
    for k, (start_k, phase) in enumerate(merged):
        lo = phase if k == 0 else start_k
        hi = merged[k + 1][0] if k + 1 < len(merged) else n
        heads.extend(range(lo, hi, bpb))
    return bpb, np.asarray(sorted(set(h for h in heads if 0 <= h < n)), dtype=int)


def fit_beat_grid(
    beats: np.ndarray,
    downbeats: np.ndarray,
    *,
    tol_s: float,
    bpm_min: int,
    bpm_max: int,
    search_bpm: int,
    constant_share: float,
    patience_beats: int,
    local_span_beats: int,
    bar_min_run: int,
    constant_tol_s: float,
    main_ratio: float,
) -> BeatGrid:
    """拍と小節頭の推定から、整数BPMのグリッドを作る。"""
    beats = np.asarray(beats, dtype=float)
    downbeats = np.asarray(downbeats, dtype=float)
    center = int(round(fold_bpm(global_bpm(beats), bpm_min, bpm_max)))
    cands = [int(np.clip(b, bpm_min, bpm_max)) for b in range(center - search_bpm, center + search_bpm + 1)]
    bpm, t0, share = best_constant_grid(beats, cands, constant_tol_s)
    if share >= constant_share:
        period = 60.0 / bpm
        n0 = int(np.round((beats[0] - t0) / period))
        n1 = int(np.round((beats[-1] - t0) / period))
        grid = t0 + np.arange(n0, n1 + 1) * period
        bpb, bar_idx = bar_heads(grid, downbeats, bar_min_run)
        return BeatGrid(grid, [TempoSegment(0, bpm)], bpb, bar_idx, True)
    kw = dict(tol_s=tol_s, bpm_min=bpm_min, bpm_max=bpm_max, search_bpm=search_bpm,
              patience=patience_beats, local_span=local_span_beats, main_bpm=bpm, main_ratio=main_ratio)
    grid, _ = _piecewise(beats, bar_idx=None, **kw)
    _, bar_idx = bar_heads(grid, downbeats, bar_min_run)
    grid, segments = _piecewise(beats, bar_idx=bar_idx, **kw)
    bpb, bar_idx = bar_heads(grid, downbeats, bar_min_run)
    return BeatGrid(grid, segments, bpb, bar_idx, False)
