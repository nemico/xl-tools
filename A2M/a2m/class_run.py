"""同じクラス誤りの連続区間。区切りは edit_cost.run_gap_s を使う。"""

from __future__ import annotations


def split_time_runs(times: list[float], gap_s: float) -> list[list[float]]:
    """時刻の昇順で、隣との間隔が gap_s を超えたら新しい区間。"""
    if not times:
        return []
    ordered = sorted(float(t) for t in times)
    runs: list[list[float]] = [[ordered[0]]]
    for t in ordered[1:]:
        if t - runs[-1][-1] <= gap_s:
            runs[-1].append(t)
        else:
            runs.append([t])
    return runs


def contamination_rate(n_wrong: int, n_correct: int) -> float:
    """正しい同クラス ÷（誤り＋正しい）。分母 0 は 0。"""
    total = n_wrong + n_correct
    if total <= 0:
        return 0.0
    return n_correct / total


def class_segment_ops(n_wrong: int, n_correct: int) -> int:
    """混入率 0% は区間1回。混入があれば 1件1回。"""
    if n_wrong <= 0:
        return 0
    if n_correct <= 0:
        return 1
    return n_wrong


def run_stats(times: list[float], gap_s: float) -> tuple[int, int, float]:
    """件数、区間数、1区間あたりの平均件数。"""
    n = len(times)
    if n == 0:
        return 0, 0, 0.0
    runs = split_time_runs(times, gap_s)
    return n, len(runs), n / len(runs)
