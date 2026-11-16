"""docs/EDIT_COST_RULES.md に基づく現行編集コストの計算モジュール。"""

from __future__ import annotations

from typing import Sequence

from a2m.class_run import split_time_runs
from a2m.cost_guard import guard_cost
from a2m.evaluate import EvalNote
from a2m.near_miss import NearMiss, classify_near_miss
from a2m.region_edit import nearest_gap_s


def _matched_ids(near: NearMiss) -> set[int]:
    return {
        id(d)
        for pair in (near.exact_pairs, near.timing_pairs, near.octave_pairs, near.pitch_pairs)
        for _g, d in pair
    }


def _extras(draft: Sequence[EvalNote], near: NearMiss) -> list[EvalNote]:
    used = _matched_ids(near)
    return [n for n in draft if id(n) not in used]


def _new_bulk(extras: Sequence[EvalNote], gt_times: Sequence[float], gap_s: float) -> tuple[int, int]:
    """納品から gap_s 以上離れた余分な音の個数と、区間数を返す。"""
    if not extras or not gt_times:
        return len(extras), (1 if extras else 0)
    far = [n for n in extras if nearest_gap_s(n.start_s, list(gt_times)) >= gap_s]
    runs = split_time_runs([n.start_s for n in far], gap_s)
    return len(far), len(runs)


def compute_track_edit_cost(
    role: str,
    gt_notes: Sequence[EvalNote],
    draft_notes: Sequence[EvalNote],
    windows_s: list[float] | float = 0.050,
    *,
    onset_tol_s: float = 0.050,
    run_gap_s: float = 2.0,
    region_gap_s: float = 2.0,
    track_name: str = "",
) -> int:
    """1つのトラックに対する現行編集コストを計算する。

    - 歌メロ・ベース・ドラム: 前後 2.0秒以上納品に音が無い区間を一括削除 (1区間1回)
    - その他: 一括削除を認めず1件ずつ削除
    """
    gts = list(gt_notes)
    draft = list(draft_notes)
    if not gts and not draft:
        return 0
    if not gts:
        # 納品が無いトラックに下書きを出した場合は全削除
        if role != "other":
            runs = split_time_runs([n.start_s for n in draft], region_gap_s)
            return len(runs)
        return len(draft)

    near = classify_near_miss(
        gts,
        draft,
        windows_s,
        onset_tol_s=onset_tol_s,
        run_gap_s=run_gap_s,
        drums=(role == "drums"),
    )

    cut = 0
    if role != "other":
        extras = _extras(draft, near)
        gt_times = [n.start_s for n in gts]
        n_far_notes, n_runs = _new_bulk(extras, gt_times, region_gap_s)
        cut = n_far_notes - n_runs

    cost = int(near.cost - cut)
    return guard_cost(cost, len(gts), where=f"track {track_name} ({role})")


def resolve_monophonic_overlaps(
    candidates_with_score: Sequence[tuple[EvalNote, float]],
    target_count: int,
) -> list[EvalNote]:
    """単音トラック用に、スコア順に採択しながら重なりを解消する。"""
    # スコア降順、開始昇順
    sorted_cands = sorted(candidates_with_score, key=lambda x: (-x[1], x[0].start_s))
    accepted: list[EvalNote] = []

    for note, _score in sorted_cands:
        if len(accepted) >= target_count:
            break
        # 既存ノートとの区間重複判定: max(s1, s2) < min(e1, e2)
        overlaps = False
        for ex in accepted:
            if max(note.start_s, ex.start_s) < min(note.end_s, ex.end_s):
                overlaps = True
                break
        if not overlaps:
            accepted.append(note)

    # 最終的な下書きは時間順に並べる
    accepted.sort(key=lambda n: n.start_s)
    return accepted


def calculate_reduction_rate(n_gt: int, cost: int) -> float:
    """削減率 = (打点数 - コスト) / 打点数"""
    if n_gt <= 0:
        return 0.0
    return (n_gt - cost) / n_gt


def calculate_overall_reduction_rate(total_n_gt: int, total_cost: int) -> float:
    """全体削減率 = 1 - コスト合計 / 打点合計"""
    if total_n_gt <= 0:
        return 0.0
    return 1.0 - (total_cost / total_n_gt)
