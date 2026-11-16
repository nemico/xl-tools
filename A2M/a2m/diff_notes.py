"""納品ノートと下書きノートを、合ってる・余分・足りない・音色違いに分ける。"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

from a2m.evaluate import EvalNote


@dataclass
class DiffGroups:
    matched_ai: list[EvalNote]
    matched_gt: list[EvalNote]
    extra: list[EvalNote]
    missing: list[EvalNote]
    kit_ai: list[EvalNote]
    kit_gt: list[EvalNote]


def classify_notes(gt: list[EvalNote], draft: list[EvalNote], *, tol_s: float, drums: bool) -> DiffGroups:
    """1対1。ドラムはキット、それ以外は音高。位置は ±tol_s。"""
    used_g: set[int] = set()
    used_d: set[int] = set()
    matched_ai: list[EvalNote] = []
    matched_gt: list[EvalNote] = []
    kit_ai: list[EvalNote] = []
    kit_gt: list[EvalNote] = []
    gt_sorted = sorted(gt, key=lambda n: n.start_s)
    starts = [n.start_s for n in gt_sorted]
    for di, d in enumerate(draft):
        i = bisect_left(starts, d.start_s)
        best = None
        best_dt = tol_s + 1
        kind = None
        j = i
        while j > 0 and d.start_s - starts[j - 1] <= tol_s:
            j -= 1
        k = j
        while k < len(starts) and starts[k] - d.start_s <= tol_s:
            gi = k
            k += 1
            if gi in used_g:
                continue
            g = gt_sorted[gi]
            dt = abs(g.start_s - d.start_s)
            if dt >= best_dt:
                continue
            same = (g.drum_class or "other") == (d.drum_class or "other") if drums else g.pitch == d.pitch
            if same and (kind != "same" or dt < best_dt):
                best, best_dt, kind = gi, dt, "same"
            elif drums and kind != "same" and dt < best_dt:
                best, best_dt, kind = gi, dt, "kit"
        if best is None:
            continue
        used_g.add(best)
        used_d.add(di)
        if kind == "same":
            matched_ai.append(d)
            matched_gt.append(gt_sorted[best])
        else:
            kit_ai.append(d)
            kit_gt.append(gt_sorted[best])
    extra = [draft[i] for i in range(len(draft)) if i not in used_d]
    missing = [gt_sorted[i] for i in range(len(gt_sorted)) if i not in used_g]
    return DiffGroups(matched_ai, matched_gt, extra, missing, kit_ai, kit_gt)
