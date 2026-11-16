"""M1 のつなぎ：拍の推定結果から、設定どおりにグリッドを作る。"""

from __future__ import annotations

from typing import Any

import numpy as np

from a2m.beat_grid import BeatGrid, fit_beat_grid
from a2m.tempo_dp import grid_residuals, refine_grid, window_medians
from a2m.tempo_fit import smooth_beat_times


def make_coarse_grid(beats: np.ndarray, downbeats: np.ndarray, m1: dict[str, Any], rules: dict[str, Any]) -> BeatGrid:
    """拍の推定だけから作る粗いグリッド（ずれは数十ms残る）。"""
    beats = smooth_beat_times(beats, m1["smooth_window_beats"], m1["smooth_max_dev_ratio"])
    return fit_beat_grid(
        beats,
        downbeats,
        tol_s=m1["drift_tol_s"],
        bpm_min=rules["tempo_bpm_min"],
        bpm_max=rules["tempo_bpm_max"],
        search_bpm=m1["search_bpm"],
        constant_share=m1["constant_share"],
        patience_beats=m1["patience_beats"],
        local_span_beats=m1["local_span_beats"],
        bar_min_run=m1["bar_min_run"],
        constant_tol_s=m1["constant_tol_s"],
        main_ratio=m1["main_ratio"],
    )


def make_grid(
    beats: np.ndarray,
    downbeats: np.ndarray,
    m1: dict[str, Any],
    rules: dict[str, Any],
    onsets: tuple[np.ndarray, np.ndarray] | None = None,
) -> BeatGrid:
    """グリッドを作る。onsets（ドラムの立ち上がりの時刻と強さ）があれば、それに合わせ直す。"""
    coarse = make_coarse_grid(beats, downbeats, m1, rules)
    if onsets is None:
        return coarse
    r = m1["refine"]
    return refine_grid(
        coarse, onsets[0], onsets[1], windows_s=r["windows_s"], window_ratio=r["window_ratio"],
        strength_quantile=r["strength_quantile"], bpm_margin=r["bpm_margin"], bpm_min=rules["tempo_bpm_min"],
        bpm_max=rules["tempo_bpm_max"], free_s=r["free_s"], cost_bar_s=r["cost_bar_s"], cost_beat_s=r["cost_beat_s"],
        drift_max_s=r["drift_max_s"], bin_s=r["bin_s"],
    )


def grid_fit_stats(grid_times: np.ndarray, onsets: tuple[np.ndarray, np.ndarray], m1: dict[str, Any]) -> dict[str, float]:
    """グリッドがドラムの立ち上がりにどれだけ合っているか。

    offset_ms: ずれ全体の中央値。 block_max_ms: 区切りごとの中央値の、全体の中央値からの最大の離れ。
    """
    m = m1["metric"]
    t, r = grid_residuals(grid_times, onsets[0], onsets[1], m["window_s"], m["strength_quantile"])
    if len(r) < m["block_min_count"]:
        return {"n": float(len(r)), "offset_ms": 0.0, "abs_med_ms": 0.0, "block_max_ms": float("nan")}
    med = float(np.median(r))
    blocks = window_medians(t, r, m["block_s"], m["block_min_count"])
    return {
        "n": float(len(r)),
        "offset_ms": med * 1000,
        "abs_med_ms": float(np.median(np.abs(r - med))) * 1000,
        "block_max_ms": float(np.max(np.abs(blocks - med))) * 1000 if len(blocks) else float("nan"),
    }
