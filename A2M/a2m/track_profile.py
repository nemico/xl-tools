"""HITL トラックプロファイル抽出・加点（CARD_P1-19 第2部）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from a2m.evaluate import EvalNote
from a2m.explain_away import CandidateNote

GRID_SUBDIVISIONS = ("quarter", "eighth", "sixteenth", "triplet_eighth")
GRID_TICKS = {
    "quarter": 480,
    "eighth": 240,
    "sixteenth": 120,
    "triplet_eighth": 160,
}


@dataclass(frozen=True)
class TrackProfile:
    pitch_min: int  # 最低音 - 2
    pitch_max: int  # 最高音 + 2
    pitch_classes: frozenset[int]  # 使われている音名集合 (0..11)
    max_polyphony: int  # 同時発音数の最大値
    dur_median: float  # 音の長さ：中央値
    dur_p10: float  # 音の長さ：10%
    dur_p90: float  # 音の長さ：90%
    used_grids: frozenset[str]  # 使われている刻みの集合
    grid_error_medians: dict[str, float]  # 各刻みの誤差中央値（秒）
    pitch_intervals: tuple[int, ...]  # 隣り合うノートの音程分布 (pitch[i+1] - pitch[i])


def build_track_profile(
    first_half_notes: Sequence[EvalNote],
    grid_points: dict[str, np.ndarray],
    grid_tol_s: float = 0.030,
    min_grid_ratio: float = 0.05,
) -> TrackProfile:
    """前半の正解ノートだけからトラックプロファイルを抽出する。"""
    if not first_half_notes:
        raise ValueError("first_half_notes must not be empty")

    pitches = [n.pitch for n in first_half_notes]
    p_min = min(pitches) - 2
    p_max = max(pitches) + 2
    pitch_classes = frozenset(p % 12 for p in pitches)

    durations = [max(0.0, float(n.end_s - n.start_s)) for n in first_half_notes]
    dur_med = float(np.median(durations))
    dur_p10 = float(np.percentile(durations, 10))
    dur_p90 = float(np.percentile(durations, 90))

    # 同時発音数の最大値
    max_poly = 1
    for n in first_half_notes:
        poly = sum(1 for o in first_half_notes if o.start_s <= n.start_s < o.end_s)
        if poly > max_poly:
            max_poly = poly

    # グリッドの使い方
    used_grids: set[str] = set()
    grid_error_medians: dict[str, float] = {}
    for gname in GRID_SUBDIVISIONS:
        pts = grid_points.get(gname)
        if pts is None or len(pts) == 0:
            grid_error_medians[gname] = float("inf")
            continue
        errs = []
        for n in first_half_notes:
            idx = np.searchsorted(pts, n.start_s)
            candidates = []
            if idx < len(pts):
                candidates.append(abs(n.start_s - pts[idx]))
            if idx > 0:
                candidates.append(abs(n.start_s - pts[idx - 1]))
            errs.append(min(candidates) if candidates else float("inf"))
        med_err = float(np.median(errs))
        grid_error_medians[gname] = med_err
        # 5%以上のノートが許容誤差内に乗っていれば使用されていると判定
        match_count = sum(1 for e in errs if e <= grid_tol_s)
        if match_count >= max(1, int(np.ceil(min_grid_ratio * len(first_half_notes)))):
            used_grids.add(gname)

    # 隣り合うノートの音程分布
    sorted_by_start = sorted(first_half_notes, key=lambda n: n.start_s)
    intervals = tuple(
        int(sorted_by_start[i + 1].pitch - sorted_by_start[i].pitch)
        for i in range(len(sorted_by_start) - 1)
    )

    return TrackProfile(
        pitch_min=p_min,
        pitch_max=p_max,
        pitch_classes=pitch_classes,
        max_polyphony=max_poly,
        dur_median=dur_med,
        dur_p10=dur_p10,
        dur_p90=dur_p90,
        used_grids=frozenset(used_grids),
        grid_error_medians=grid_error_medians,
        pitch_intervals=intervals,
    )


def compute_candidate_bonuses(
    candidates: Sequence[CandidateNote],
    profile: TrackProfile,
    grid_points: dict[str, np.ndarray],
    grid_tol_s: float = 0.030,
) -> dict[str, np.ndarray]:
    """後半の候補群に対して 5 つの加点ベクトルを計算する。

    加点:
    - pitch: 音域に入っている (+1.0)
    - pc: 音名が集合に入っている (+1.0)
    - dur: 長さが 10〜90% の範囲に入る (+0.5)
    - grid: 開始が、使われている刻みのどれかに ±30ms で乗る (+1.5)
    - poly: 同時発音数が最大値を超えない (+0.5)
    """
    n_c = len(candidates)
    if n_c == 0:
        return {
            "pitch": np.zeros(0, dtype=float),
            "pc": np.zeros(0, dtype=float),
            "dur": np.zeros(0, dtype=float),
            "grid": np.zeros(0, dtype=float),
            "poly": np.zeros(0, dtype=float),
            "total": np.zeros(0, dtype=float),
        }

    c_starts = np.array([c.start_s for c in candidates], dtype=float)
    c_ends = np.array([c.end_s for c in candidates], dtype=float)
    c_pitches = np.array([c.pitch for c in candidates], dtype=int)
    c_durs = c_ends - c_starts

    # 1. 音域 (+1.0)
    b_pitch = np.where(
        (c_pitches >= profile.pitch_min) & (c_pitches <= profile.pitch_max),
        1.0,
        0.0,
    )

    # 2. 音名集合 (+1.0)
    pcs = set(profile.pitch_classes)
    b_pc = np.array([1.0 if (p % 12) in pcs else 0.0 for p in c_pitches], dtype=float)

    # 3. 長さ (+0.5)
    b_dur = np.where(
        (c_durs >= profile.dur_p10) & (c_durs <= profile.dur_p90),
        0.5,
        0.0,
    )

    # 4. グリッド (+1.5)
    b_grid = np.zeros(n_c, dtype=float)
    for gname in profile.used_grids:
        pts = grid_points.get(gname)
        if pts is None or len(pts) == 0:
            continue
        idx = np.searchsorted(pts, c_starts)
        idx = np.clip(idx, 0, len(pts) - 1)
        idx_prev = np.clip(idx - 1, 0, len(pts) - 1)
        err = np.minimum(np.abs(c_starts - pts[idx]), np.abs(c_starts - pts[idx_prev]))
        b_grid = np.maximum(b_grid, np.where(err <= grid_tol_s, 1.5, 0.0))

    # 5. 同時発音数 (+0.5)
    # 同一音域 [pitch_min, pitch_max] 内で開始が ±50ms 以内にある候補数を数える
    in_range = (c_pitches >= profile.pitch_min) & (c_pitches <= profile.pitch_max)
    b_poly = np.zeros(n_c, dtype=float)
    for i in range(n_c):
        t = c_starts[i]
        idx_lo = int(np.searchsorted(c_starts, t - 0.050, side="left"))
        idx_hi = int(np.searchsorted(c_starts, t + 0.050, side="right"))
        concurrent = int(np.sum(in_range[idx_lo:idx_hi]))
        if concurrent <= profile.max_polyphony:
            b_poly[i] = 0.5

    b_total = b_pitch + b_pc + b_dur + b_grid + b_poly

    return {
        "pitch": b_pitch,
        "pc": b_pc,
        "dur": b_dur,
        "grid": b_grid,
        "poly": b_poly,
        "total": b_total,
    }
