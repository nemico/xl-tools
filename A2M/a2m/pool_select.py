"""方式の足し引き。票のまとまりと、コストが下がる間だけの選択。"""

from __future__ import annotations

import numpy as np


def nearest_anchor_indexes(anchors: np.ndarray, starts: np.ndarray, tol: float) -> np.ndarray:
    """各時刻を、許容以内で一番近いアンカーに付ける。無ければ -1。アンカーは昇順。"""
    out = np.full(len(starts), -1, dtype=np.int32)
    if len(anchors) == 0 or len(starts) == 0:
        return out
    pos = np.searchsorted(anchors, starts)
    best = np.full(len(starts), np.inf)
    for delta in (-1, 0):
        j = pos + delta
        ok = (j >= 0) & (j < len(anchors))
        dist = np.full(len(starts), np.inf)
        clipped = np.clip(j, 0, len(anchors) - 1)
        dist[ok] = np.abs(anchors[clipped[ok]] - starts[ok])
        take = ok & (dist <= tol) & (dist < best)
        out[take] = j[take].astype(np.int32)
        best[take] = dist[take]
    return out


def top_indexes(starts: np.ndarray, pitches: np.ndarray, scores: np.ndarray, need: int) -> np.ndarray:
    """スコアが高い順、同点は時刻、その次は音高。スコア 0 は入れない。"""
    active = np.flatnonzero(scores > 0)
    if need <= 0 or active.size == 0:
        return active[:0]
    if active.size <= need:
        order = np.lexsort((pitches[active], starts[active], -scores[active]))
        return active[order]
    sc = scores[active]
    thr = np.partition(sc, -need)[-need]
    above = active[sc > thr]
    border = active[sc == thr]
    above = above[np.lexsort((pitches[above], starts[above], -scores[above]))]
    take = need - int(above.size)
    if take <= 0:
        return above[:need]
    if border.size > take:
        part = np.argpartition(starts[border], take)[:take]
        border = border[part]
    border = border[np.lexsort((pitches[border], starts[border]))]
    return np.concatenate([above, border])[:need]


def greedy_pick(methods: list[str], cost_of, *, backward: bool, start: list[str] | None = None) -> tuple[list[str], int]:
    """コストが厳密に下がる間だけ足す、または外す。"""
    if start is not None:
        selected = list(start)
    else:
        selected = list(methods) if backward else []
    current = int(cost_of(selected))
    while True:
        trial = [m for m in methods if (m in selected) == backward]
        best_m = None
        best_c = current
        for m in trial:
            nxt = [x for x in selected if x != m] if backward else selected + [m]
            c = int(cost_of(nxt))
            if c < best_c:
                best_c = c
                best_m = m
        if best_m is None:
            break
        if backward:
            selected = [x for x in selected if x != best_m]
        else:
            selected.append(best_m)
        current = best_c
    return selected, current
