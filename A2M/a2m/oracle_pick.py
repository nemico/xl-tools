"""正解を知っているときの候補の選び方。プールに正解の音は足さない。"""

from __future__ import annotations

import random

from a2m.evaluate import EvalNote


def _same(gt: EvalNote, pitch: int, drum_class: str | None, drums: bool) -> bool:
    if drums:
        return (drum_class or "") == (gt.drum_class or "") or pitch == gt.pitch
    return pitch == gt.pitch


def _octave(gt: EvalNote, pitch: int) -> bool:
    dp = pitch - gt.pitch
    return dp != 0 and dp % 12 == 0


def oracle_indexes(
    gt: list[EvalNote],
    starts: list[float],
    pitches: list[int],
    classes: list[str | None],
    windows_s: list[float],
    *,
    onset_tol_s: float,
    drums: bool,
) -> list[int]:
    """各正解に、合ってる・ずれ・オクターブの候補を1つずつ割り当てる。"""
    pairs: list[tuple[tuple[int, float], int, int]] = []
    for gi, g in enumerate(gt):
        limit = windows_s[gi] if gi < len(windows_s) else onset_tol_s
        for di, (st, pitch, cls) in enumerate(zip(starts, pitches, classes)):
            dt = abs(st - g.start_s)
            same = _same(g, pitch, cls, drums)
            if same and dt <= onset_tol_s:
                pairs.append(((0, dt), gi, di))
            elif same and onset_tol_s < dt <= limit:
                pairs.append(((1, dt), gi, di))
            elif not drums and dt <= onset_tol_s and _octave(g, pitch):
                pairs.append(((2, dt), gi, di))
    pairs.sort()
    used_g: set[int] = set()
    used_d: set[int] = set()
    chosen: list[int] = []
    for _key, gi, di in pairs:
        if gi in used_g or di in used_d:
            continue
        used_g.add(gi)
        used_d.add(di)
        chosen.append(di)
    return chosen


def mix_indexes(oracle: list[int], ranked: list[int], n: int, p: float, rng: random.Random) -> list[int]:
    """確率 p で正解側の候補を採り、残り枠は順位の高い候補で埋める。p=1 は埋めない。"""
    forced = [i for i in oracle if rng.random() < p]
    if p >= 1.0:
        return forced[:n]
    chosen = list(forced)
    have = set(chosen)
    for i in ranked:
        if len(chosen) >= n:
            break
        if i in have:
            continue
        chosen.append(i)
        have.add(i)
    return chosen
