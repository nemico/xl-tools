"""P1-16b: DAW（Reaper）実作業に即した編集コスト測定モジュール。

仕様: docs/archive/CARD_P1-16b_MESSAGE.md
- オクターブ違い: 範囲選択して一括移調 = 1回（連続して同じ方向にずれている音は何個あっても1回）
- 音域外のまとめ削除: 高すぎ/低すぎの音をまとめて削除 = 1回
- グリッドのずれ: 範囲選択してクオンタイズ = 1回（ずれ方向に関係なく範囲で1回）
- 長さの調整: 0回（カラオケ打ち込み評価では不問）
- 音が無い: 1音につき1回（打ち足し）
- 余分な音: 1音につき1回（空小節等の連続塊は一括削除=1回）
- 音高違い: 1音につき1回（ドラッグ修正）
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from a2m.evaluate import EvalNote


@dataclass(frozen=True)
class EditCostConfig:
    onset_tol_s: float = 0.050
    quantize_tol_s: float = 0.300
    run_gap_s: float = 2.0
    bulk_del_min_notes: int = 3
    bulk_del_gap_s: float = 1.0
    empty_space_dist_s: float = 0.5

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> EditCostConfig:
        if not d:
            return cls()
        return cls(
            onset_tol_s=float(d.get("onset_tol_s", 0.050)),
            quantize_tol_s=float(d.get("quantize_tol_s", 0.300)),
            run_gap_s=float(d.get("run_gap_s", 2.0)),
            bulk_del_min_notes=int(d.get("bulk_del_min_notes", 3)),
            bulk_del_gap_s=float(d.get("bulk_del_gap_s", 1.0)),
            empty_space_dist_s=float(d.get("empty_space_dist_s", 0.5)),
        )


@dataclass
class TrackEditResult:
    song: str
    track_index: int
    role: str
    n_gt: int
    n_draft: int
    cost_add: int              # 音が無い（打ち足し）
    cost_del_ind: int          # 余分な音（個別削除）
    cost_del_bulk: int         # 余分な音（まとめ削除）
    cost_del_range: int        # 音域外（まとめ削除）
    cost_pitch_fix: int        # 音高がずれている（1音ずつ直す）
    cost_octave_trans: int     # オクターブ違い（一括移調）
    cost_quantize: int         # グリッドのずれ（一括クオンタイズ）

    # 個数カウント
    n_exact_match: int
    n_octave_notes: int
    n_quantize_notes: int
    n_pitch_error_notes: int
    n_spurious_notes: int
    n_out_of_range_notes: int

    @property
    def total_cost(self) -> int:
        return (
            self.cost_add
            + self.cost_del_ind
            + self.cost_del_bulk
            + self.cost_del_range
            + self.cost_pitch_fix
            + self.cost_octave_trans
            + self.cost_quantize
        )


def evaluate_track_edit_cost(
    gt_notes: Sequence[EvalNote],
    draft_notes: Sequence[EvalNote],
    role: str,
    cfg: EditCostConfig = EditCostConfig(),
    song: str = "",
    track_index: int = 0,
) -> TrackEditResult:
    """1つのトラックに対する正解ノート群と下書きノート群から編集操作回数を算出する。"""
    n_gt = len(gt_notes)
    n_draft = len(draft_notes)
    if n_gt == 0:
        return TrackEditResult(
            song=song,
            track_index=track_index,
            role=role,
            n_gt=0,
            n_draft=n_draft,
            cost_add=0,
            cost_del_ind=n_draft,
            cost_del_bulk=0,
            cost_del_range=0,
            cost_pitch_fix=0,
            cost_octave_trans=0,
            cost_quantize=0,
            n_exact_match=0,
            n_octave_notes=0,
            n_quantize_notes=0,
            n_pitch_error_notes=0,
            n_spurious_notes=n_draft,
            n_out_of_range_notes=0,
        )

    gt_sorted = sorted(gt_notes, key=lambda n: (n.start_s, n.pitch))
    draft_sorted = sorted(draft_notes, key=lambda n: (n.start_s, n.pitch))

    # 1. 照合 (Greedy by priority tiers with bisect windowing)
    used_g: set[int] = set()
    used_d: set[int] = set()
    d_starts = [d.start_s for d in draft_sorted]

    # Tier 1: 完全一致 (|dt| <= onset_tol_s, same pitch / drum_class)
    exact_pairs: list[tuple[float, int, int]] = []
    for gi, gn in enumerate(gt_sorted):
        i0 = bisect.bisect_left(d_starts, gn.start_s - cfg.onset_tol_s)
        i1 = bisect.bisect_right(d_starts, gn.start_s + cfg.onset_tol_s)
        for di in range(i0, i1):
            dn = draft_sorted[di]
            is_match = False
            if role == "drums":
                is_match = (
                    (dn.drum_class is not None and dn.drum_class == gn.drum_class)
                    or dn.pitch == gn.pitch
                )
            else:
                is_match = (dn.pitch == gn.pitch)

            if is_match:
                dt = abs(dn.start_s - gn.start_s)
                exact_pairs.append((dt, gi, di))

    exact_pairs.sort(key=lambda x: x[0])
    for dt, gi, di in exact_pairs:
        if gi in used_g or di in used_d:
            continue
        used_g.add(gi)
        used_d.add(di)

    n_exact_match = len(used_g)

    # Tier 2: オクターブ違い (|dt| <= onset_tol_s, delta_p in {+-12, +-24, +-36}) - pitched only
    octave_pairs: list[tuple[float, int, int, int]] = []
    if role != "drums":
        for gi, gn in enumerate(gt_sorted):
            if gi in used_g:
                continue
            i0 = bisect.bisect_left(d_starts, gn.start_s - cfg.onset_tol_s)
            i1 = bisect.bisect_right(d_starts, gn.start_s + cfg.onset_tol_s)
            for di in range(i0, i1):
                if di in used_d:
                    continue
                dn = draft_sorted[di]
                dp = dn.pitch - gn.pitch
                if dp in (-36, -24, -12, 12, 24, 36):
                    dt = abs(dn.start_s - gn.start_s)
                    octave_pairs.append((dt, gi, di, dp))

        octave_pairs.sort(key=lambda x: x[0])
        matched_octaves: list[tuple[int, int, int, float]] = []  # (gi, di, dp, start_s)
        for dt, gi, di, dp in octave_pairs:
            if gi in used_g or di in used_d:
                continue
            used_g.add(gi)
            used_d.add(di)
            matched_octaves.append((gi, di, dp, draft_sorted[di].start_s))
    else:
        matched_octaves = []

    n_octave_notes = len(matched_octaves)

    # 一括移調操作カウント（同じ移動量かつ連続するノートを1回と数える）
    cost_octave_trans = 0
    if matched_octaves:
        matched_octaves.sort(key=lambda x: x[3])
        cur_dp = matched_octaves[0][2]
        last_t = matched_octaves[0][3]
        cost_octave_trans = 1
        for gi, di, dp, t in matched_octaves[1:]:
            if dp == cur_dp and (t - last_t) <= cfg.run_gap_s:
                pass
            else:
                cost_octave_trans += 1
                cur_dp = dp
            last_t = t

    # Tier 3: グリッドのずれ / クオンタイズ (onset_tol_s < |dt| <= quantize_tol_s, same pitch / drum_class)
    quant_pairs: list[tuple[float, int, int]] = []
    for gi, gn in enumerate(gt_sorted):
        if gi in used_g:
            continue
        i0 = bisect.bisect_left(d_starts, gn.start_s - cfg.quantize_tol_s)
        i1 = bisect.bisect_right(d_starts, gn.start_s + cfg.quantize_tol_s)
        for di in range(i0, i1):
            if di in used_d:
                continue
            dn = draft_sorted[di]
            dt = abs(dn.start_s - gn.start_s)
            if dt <= cfg.onset_tol_s:
                continue
            is_match = False
            if role == "drums":
                is_match = (
                    (dn.drum_class is not None and dn.drum_class == gn.drum_class)
                    or dn.pitch == gn.pitch
                )
            else:
                is_match = (dn.pitch == gn.pitch)

            if is_match:
                quant_pairs.append((dt, gi, di))

    quant_pairs.sort(key=lambda x: x[0])
    matched_quants: list[tuple[int, int, float]] = []  # (gi, di, start_s)
    for dt, gi, di in quant_pairs:
        if gi in used_g or di in used_d:
            continue
        used_g.add(gi)
        used_d.add(di)
        matched_quants.append((gi, di, draft_sorted[di].start_s))

    n_quantize_notes = len(matched_quants)

    # 一括クオンタイズ操作カウント（連続するノート群を1回と数える）
    cost_quantize = 0
    if matched_quants:
        matched_quants.sort(key=lambda x: x[2])
        last_t = matched_quants[0][2]
        cost_quantize = 1
        for gi, di, t in matched_quants[1:]:
            if (t - last_t) <= cfg.run_gap_s:
                pass
            else:
                cost_quantize += 1
            last_t = t

    # Tier 4: 音高がずれている (|dt| <= onset_tol_s, wrong pitch / drum_class)
    pitch_pairs: list[tuple[float, int, int]] = []
    for gi, gn in enumerate(gt_sorted):
        if gi in used_g:
            continue
        i0 = bisect.bisect_left(d_starts, gn.start_s - cfg.onset_tol_s)
        i1 = bisect.bisect_right(d_starts, gn.start_s + cfg.onset_tol_s)
        for di in range(i0, i1):
            if di in used_d:
                continue
            dn = draft_sorted[di]
            dt = abs(dn.start_s - gn.start_s)
            pitch_pairs.append((dt, gi, di))

    pitch_pairs.sort(key=lambda x: x[0])
    matched_pitches: list[tuple[int, int]] = []
    for dt, gi, di in pitch_pairs:
        if gi in used_g or di in used_d:
            continue
        used_g.add(gi)
        used_d.add(di)
        matched_pitches.append((gi, di))

    n_pitch_error_notes = len(matched_pitches)
    cost_pitch_fix = n_pitch_error_notes  # 1音ずつ直す = 1 each

    # 2. 未照合の正解ノート -> 音が無い（打ち足し）
    cost_add = len(gt_sorted) - len(used_g)

    # 3. 未照合の下書きノート -> 音域外まとめ削除 / 余分な音（削除）
    unmatched_d = [draft_sorted[di] for di in range(len(draft_sorted)) if di not in used_d]

    cost_del_range = 0
    n_out_of_range_notes = 0
    remaining_unmatched_d: list[EvalNote] = []

    if role != "drums" and unmatched_d:
        gt_pitches = [n.pitch for n in gt_sorted]
        min_p = min(gt_pitches)
        max_p = max(gt_pitches)

        too_high = [n for n in unmatched_d if n.pitch > max_p]
        too_low = [n for n in unmatched_d if n.pitch < min_p]

        if too_high:
            cost_del_range += 1
            n_out_of_range_notes += len(too_high)
        if too_low:
            cost_del_range += 1
            n_out_of_range_notes += len(too_low)

        remaining_unmatched_d = [n for n in unmatched_d if min_p <= n.pitch <= max_p]
    else:
        remaining_unmatched_d = list(unmatched_d)

    n_spurious_notes = len(remaining_unmatched_d)

    # 一括削除 vs 個別削除の判別
    cost_del_bulk = 0
    cost_del_ind = 0

    if remaining_unmatched_d:
        gt_starts = np.array([g.start_s for g in gt_sorted], dtype=float)
        remaining_unmatched_d.sort(key=lambda n: n.start_s)

        in_empty_space = []
        for dn in remaining_unmatched_d:
            idx = int(np.searchsorted(gt_starts, dn.start_s))
            near = False
            for j in (idx - 1, idx, idx + 1):
                if 0 <= j < len(gt_starts):
                    if abs(gt_starts[j] - dn.start_s) <= cfg.empty_space_dist_s:
                        near = True
                        break
            in_empty_space.append(not near)

        cur_cluster: list[int] = []
        in_bulk: set[int] = set()
        for i, dn in enumerate(remaining_unmatched_d):
            if in_empty_space[i]:
                if not cur_cluster:
                    cur_cluster = [i]
                else:
                    if dn.start_s - remaining_unmatched_d[cur_cluster[-1]].start_s <= cfg.bulk_del_gap_s:
                        cur_cluster.append(i)
                    else:
                        if len(cur_cluster) >= cfg.bulk_del_min_notes:
                            cost_del_bulk += 1
                            in_bulk.update(cur_cluster)
                        cur_cluster = [i]
            else:
                if len(cur_cluster) >= cfg.bulk_del_min_notes:
                    cost_del_bulk += 1
                    in_bulk.update(cur_cluster)
                cur_cluster = []

        if len(cur_cluster) >= cfg.bulk_del_min_notes:
            cost_del_bulk += 1
            in_bulk.update(cur_cluster)

        cost_del_ind = len(remaining_unmatched_d) - len(in_bulk)


    return TrackEditResult(
        song=song,
        track_index=track_index,
        role=role,
        n_gt=n_gt,
        n_draft=n_draft,
        cost_add=cost_add,
        cost_del_ind=cost_del_ind,
        cost_del_bulk=cost_del_bulk,
        cost_del_range=cost_del_range,
        cost_pitch_fix=cost_pitch_fix,
        cost_octave_trans=cost_octave_trans,
        cost_quantize=cost_quantize,
        n_exact_match=n_exact_match,
        n_octave_notes=n_octave_notes,
        n_quantize_notes=n_quantize_notes,
        n_pitch_error_notes=n_pitch_error_notes,
        n_spurious_notes=n_spurious_notes,
        n_out_of_range_notes=n_out_of_range_notes,
    )


def compute_perfect_selector_costs(
    gt_notes: Sequence[EvalNote],
    cand_pool: Sequence[dict],
    role: str,
    cfg: EditCostConfig = EditCostConfig(),
) -> tuple[int, int]:
    """選別が完璧だった場合の上限コストを算出する（True Positives のみ選別、FP 0件）。"""
    n_gt = len(gt_notes)
    if n_gt == 0:
        return 0, 0

    gt_sorted = sorted(gt_notes, key=lambda n: (n.start_s, n.pitch))
    pool_sorted = sorted(cand_pool, key=lambda c: (c["start_s"], c["pitch"]))
    c_starts = [c["start_s"] for c in pool_sorted]

    matched_exact = set()
    matched_octave = []  # (gi, dp, start_s)
    matched_quant = []   # (gi, start_s)

    used_c = set()
    # Tier 1: exact matches
    for gi, gn in enumerate(gt_sorted):
        i0 = bisect.bisect_left(c_starts, gn.start_s - cfg.onset_tol_s)
        i1 = bisect.bisect_right(c_starts, gn.start_s + cfg.onset_tol_s)
        for ci in range(i0, i1):
            if ci in used_c:
                continue
            c = pool_sorted[ci]
            is_match = False
            if role == "drums":
                is_match = (
                    (c.get("drum_class") is not None and c["drum_class"] == gn.drum_class)
                    or c["pitch"] == gn.pitch
                )
            else:
                is_match = (c["pitch"] == gn.pitch)

            if is_match:
                used_c.add(ci)
                matched_exact.add(gi)
                break

    # Tier 2: octave matches (pitched only)
    if role != "drums":
        for gi, gn in enumerate(gt_sorted):
            if gi in matched_exact:
                continue
            i0 = bisect.bisect_left(c_starts, gn.start_s - cfg.onset_tol_s)
            i1 = bisect.bisect_right(c_starts, gn.start_s + cfg.onset_tol_s)
            for ci in range(i0, i1):
                if ci in used_c:
                    continue
                c = pool_sorted[ci]
                dp = c["pitch"] - gn.pitch
                if dp in (-36, -24, -12, 12, 24, 36):
                    used_c.add(ci)
                    matched_octave.append((gi, dp, c["start_s"]))
                    break

    # Tier 3: timing jitter
    for gi, gn in enumerate(gt_sorted):
        if gi in matched_exact or any(x[0] == gi for x in matched_octave):
            continue
        i0 = bisect.bisect_left(c_starts, gn.start_s - cfg.quantize_tol_s)
        i1 = bisect.bisect_right(c_starts, gn.start_s + cfg.quantize_tol_s)
        for ci in range(i0, i1):
            if ci in used_c:
                continue
            c = pool_sorted[ci]
            if abs(c["start_s"] - gn.start_s) <= cfg.onset_tol_s:
                continue
            is_match = False
            if role == "drums":
                is_match = (
                    (c.get("drum_class") is not None and c["drum_class"] == gn.drum_class)
                    or c["pitch"] == gn.pitch
                )
            else:
                is_match = (c["pitch"] == gn.pitch)

            if is_match:
                used_c.add(ci)
                matched_quant.append((gi, c["start_s"]))
                break

    n_tp = len(matched_exact) + len(matched_octave) + len(matched_quant)
    cost_add = n_gt - n_tp

    cost_oct = 0
    if matched_octave:
        matched_octave.sort(key=lambda x: x[2])
        cur_dp = matched_octave[0][1]
        last_t = matched_octave[0][2]
        cost_oct = 1
        for gi, dp, t in matched_octave[1:]:
            if dp == cur_dp and (t - last_t) <= cfg.run_gap_s:
                pass
            else:
                cost_oct += 1
                cur_dp = dp
            last_t = t

    cost_quant = 0
    if matched_quant:
        matched_quant.sort(key=lambda x: x[1])
        last_t = matched_quant[0][1]
        cost_quant = 1
        for gi, t in matched_quant[1:]:
            if (t - last_t) <= cfg.run_gap_s:
                pass
            else:
                cost_quant += 1
            last_t = t

    total_cost_perfect = cost_add + cost_oct + cost_quant
    return n_gt, total_cost_perfect
