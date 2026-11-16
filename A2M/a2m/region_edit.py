"""余分な音を、消し方で分ける。区間の空白は gap_s 以上。"""

from __future__ import annotations

import bisect


def nearest_gap_s(t: float, times: list[float]) -> float:
    if not times:
        return float("inf")
    i = bisect.bisect_left(times, t)
    best = float("inf")
    for j in (i - 1, i):
        if 0 <= j < len(times):
            best = min(best, abs(times[j] - t))
    return best


def classify_extra(t: float, pitch: int, gt_times: list[float], gt_pitches: list[int], gap_s: float) -> str:
    """上から順に、区間ごと / 音域 / 1個ずつ。"""
    if nearest_gap_s(t, gt_times) >= gap_s:
        return "region"
    if gt_pitches and (pitch < min(gt_pitches) or pitch > max(gt_pitches)):
        return "register"
    return "single"


def missing_place(t: float, other_gt_times: list[float], gap_s: float) -> str:
    """他の納品音から gap_s 以上離れていれば区間の外。"""
    if nearest_gap_s(t, other_gt_times) >= gap_s:
        return "outside"
    return "inside"
