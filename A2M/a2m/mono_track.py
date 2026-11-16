"""単音トラックの判定と、重なった候補を1つに残す選別。長さは変えない。"""

from __future__ import annotations


def overlap_s(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _clip_ends(starts: list[float], ends: list[float], ignore_s: float) -> list[float]:
    """50ms 以下の重なりは、先の音を次の音の頭で止めたものとして数えない。"""
    clipped = list(ends)
    order = sorted(range(len(starts)), key=lambda i: starts[i])
    for p, i in enumerate(order):
        for j in order[p + 1:]:
            if starts[j] >= clipped[i]:
                break
            if clipped[i] - starts[j] <= ignore_s:
                clipped[i] = starts[j]
            else:
                break
    return clipped


def mono_stats(starts: list[float], ends: list[float], ignore_s: float) -> tuple[int, float, int]:
    """同時発音の最大、単音の時間の割合、単音にすると消える音の数。"""
    if not starts:
        return 0, 1.0, 0
    clipped = _clip_ends(starts, ends, ignore_s)
    events = []
    for s, e in zip(starts, clipped):
        if e <= s:
            continue
        events.append((s, 1))
        events.append((e, -1))
    events.sort(key=lambda x: (x[0], x[1]))
    level = 0
    max_poly = 0
    mono_t = 0.0
    any_t = 0.0
    prev = None
    for t, delta in events:
        if prev is not None and t > prev:
            dt = t - prev
            if level >= 1:
                any_t += dt
            if level == 1:
                mono_t += dt
        level += delta
        max_poly = max(max_poly, level)
        prev = t
    ratio = (mono_t / any_t) if any_t > 0 else 1.0
    # 重なっている音の塊では、1音を残して残りが消える。
    parent = list(range(len(starts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(len(starts)), key=lambda i: starts[i])
    active: list[int] = []
    for i in order:
        if clipped[i] <= starts[i]:
            continue
        active = [j for j in active if clipped[j] > starts[i]]
        for j in active:
            if overlap_s(starts[i], clipped[i], starts[j], clipped[j]) > 0:
                parent[find(i)] = find(j)
        active.append(i)
    groups: dict[int, int] = {}
    for i in range(len(starts)):
        if clipped[i] <= starts[i]:
            continue
        root = find(i)
        groups[root] = groups.get(root, 0) + 1
    lost = sum(n - 1 for n in groups.values() if n > 1)
    return max_poly, ratio, lost


def keep_highest(scores: list[float], starts: list[float], ends: list[float], ignore_s: float) -> list[int]:
    """重なりが ignore_s を超える候補は、点数の高いほうだけ残す。"""
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], starts[i]))
    kept: list[int] = []
    for i in order:
        sounding = [k for k in kept if ends[k] > starts[i] and ends[i] > starts[k]]
        if any(overlap_s(starts[i], ends[i], starts[k], ends[k]) > ignore_s for k in sounding):
            continue
        kept.append(i)
    return kept
