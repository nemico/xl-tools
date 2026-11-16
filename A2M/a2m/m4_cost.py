"""下書きを納品のノートに直すまでの手数（M4）。グリッドにそろえた後の位置（tick）で数える。

数え方（1音ずつ。まとめ操作は数えないので、P1 の編集コストより厳しめ）:
  合っている      : 同じ位置（±tol tick）で同じ音高（ドラムは同じクラス） → 0
  音高だけ直す    : 同じ位置に別の音高の下書きがある                     → 1
  打ち足す        : 納品にあって、下書きに無い                           → 1
  消す            : 下書きにあって、納品に無い                           → 1
削減率 = 1 − 手数 / 納品のノート数（ゼロから打つと、納品のノート数だけ手数がかかる）
"""

from __future__ import annotations

from a2m.m4_tokens import match_onsets


def note_edit_cost(ref: list[tuple[int, int]], est: list[tuple[int, int]], tol: int) -> dict[str, int]:
    """ref・est は (tick, 音高) の一覧。"""
    ref = sorted(set(ref))
    est = sorted(set(est))
    # 位置と音高が合うもの
    used_r: set[int] = set()
    used_e: set[int] = set()
    cands = sorted((abs(a[0] - b[0]), i, j) for i, a in enumerate(ref) for j, b in _near(est, a[0], tol) if a[1] == b[1])
    for _, i, j in cands:
        if i not in used_r and j not in used_e:
            used_r.add(i)
            used_e.add(j)
    hit = len(used_r)
    # 残りのうち、位置だけ合うもの（音高を直す）
    rest_r = [(t, 0) for i, (t, _) in enumerate(ref) if i not in used_r]
    rest_e = [(t, 0) for j, (t, _) in enumerate(est) if j not in used_e]
    fix, _, _ = match_onsets(rest_r, rest_e, tol)
    missing = len(rest_r) - fix
    extra = len(rest_e) - fix
    return {"ref": len(ref), "est": len(est), "hit": hit, "fix": fix, "missing": missing, "extra": extra, "cost": fix + missing + extra}


def _near(est: list[tuple[int, int]], tick: int, tol: int):
    """est（tick の順）のうち、tick の前後 tol にあるものを (番号, 要素) で返す。"""
    import bisect

    lo = bisect.bisect_left(est, (tick - tol, -1))
    hi = bisect.bisect_right(est, (tick + tol, 10**9))
    return [(j, est[j]) for j in range(lo, hi)]


def reduction(cost: int, n_ref: int) -> float:
    return 1.0 - cost / n_ref if n_ref else 0.0
