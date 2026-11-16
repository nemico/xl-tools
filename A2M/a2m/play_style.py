"""演奏の型分類および編集コスト測定（仕様 CARD_P1-16）。"""

from __future__ import annotations

import bisect
from collections import defaultdict
from typing import Sequence

import numpy as np

from a2m.evaluate import EvalNote
from a2m.explain_away import RefTrackNote
from a2m.karaoke_midi import KaraokeMidiData
from a2m.midi_time import measure_length_ticks, time_signature_at_tick, tick_to_second
from a2m.warp import WarpResult

PLAY_STYLES: tuple[str, ...] = ("block", "arpeggio", "single", "sustain", "mixed")
MISS_CATEGORIES: tuple[str, ...] = (
    "見つかった",
    "時間だけずれ",
    "オクターブ違い",
    "音名違い",
    "何も無い",
)
EDIT_OPERATIONS: tuple[str, ...] = (
    "一括削除",
    "個別削除",
    "範囲量子化",
    "個別編集",
    "追加",
)

# 標準和音テンプレート（Major, Minor, Dim, Aug, Sus4, Sus2, 7th系, 6th/add9, 9th系）
_CHORD_TEMPLATES: list[set[int]] = [
    {0, 4, 7},
    {0, 3, 7},
    {0, 3, 6},
    {0, 4, 8},
    {0, 5, 7},
    {0, 2, 7},
    {0, 4, 7, 11},
    {0, 3, 7, 10},
    {0, 4, 7, 10},
    {0, 3, 6, 10},
    {0, 3, 6, 9},
    {0, 3, 7, 11},
    {0, 4, 7, 9},
    {0, 3, 7, 9},
    {0, 2, 4, 7},
    {0, 2, 3, 7},
    {0, 2, 4, 7, 10},
    {0, 2, 4, 7, 11},
    {0, 2, 3, 7, 10},
]

ALL_TRANSPOSED_CHORDS: list[frozenset[int]] = []
for _tmpl in _CHORD_TEMPLATES:
    for _root in range(12):
        ALL_TRANSPOSED_CHORDS.append(frozenset((p + _root) % 12 for p in _tmpl))


def is_chord_pitch_classes(pitch_classes: set[int] | frozenset[int]) -> bool:
    """3音以上の音名集合がいずれかの和音テンプレートの部分集合になっているか判定。"""
    if len(pitch_classes) < 3:
        return False
    return any(pitch_classes.issubset(c) for c in ALL_TRANSPOSED_CHORDS)


def cluster_simultaneous_notes(
    notes: Sequence[EvalNote],
    window_s: float = 0.030,
) -> list[list[EvalNote]]:
    """開始時刻の差が window_s (30ms) 以内のノートを同時発音組にクラスタリング。"""
    if not notes:
        return []
    sorted_n = sorted(notes, key=lambda n: n.start_s)
    clusters: list[list[EvalNote]] = []
    for n in sorted_n:
        if not clusters or (n.start_s - clusters[-1][0].start_s > window_s):
            clusters.append([n])
        else:
            clusters[-1].append(n)
    return clusters


def classify_bar(notes: Sequence[EvalNote], beat_dur_s: float) -> str:
    """小節内のノート群を 5 種類の演奏の型のいずれかに分類する。

    判定基準:
    - sustain: ノート数 <= 2 かつ すべての長さ >= 1拍（マージン 5% 許容）
    - block: 3つ以上の音が同時に（30ms以内）鳴る組が2組以上
    - arpeggio: 同時発音が常に1〜2音、>=3クラスタ、音高集合が和音を構成（>=3音名）
    - single: 同時発音が常に1音、音名種類 <= 2 または 順次進行が主（>=50%）
    - mixed: 上のどれにも決まらない／小節内で切り替わる
    """
    if not notes:
        return "mixed"

    sorted_notes = sorted(notes, key=lambda n: n.start_s)

    # 1. sustain: 小節内のノートが2つ以下で、長さが1拍以上
    if len(sorted_notes) <= 2:
        if all((n.end_s - n.start_s) >= beat_dur_s * 0.95 for n in sorted_notes):
            return "sustain"

    clusters = cluster_simultaneous_notes(sorted_notes, window_s=0.030)

    # 2. block: 3つ以上の音が同時に（開始の差 30ms 以内）鳴る組が2組以上
    block_clusters = sum(1 for c in clusters if len(c) >= 3)
    if block_clusters >= 2:
        return "block"

    # 3. single: 同時発音が常に1音で、音名の種類が2つ以下、または順次進行が主
    if all(len(c) == 1 for c in clusters):
        pcs = {n.pitch % 12 for n in sorted_notes}
        if len(pcs) <= 2:
            return "single"
        if len(sorted_notes) >= 2:
            stepwise_count = sum(
                1
                for i in range(len(sorted_notes) - 1)
                if abs(sorted_notes[i + 1].pitch - sorted_notes[i].pitch) <= 2
            )
            if stepwise_count / (len(sorted_notes) - 1) >= 0.5:
                return "single"

    # 4. arpeggio: 同時発音が常に1〜2音、小節内の音高の集合が和音を構成（3音以上の異なる音名）、音が順に並ぶ
    if all(len(c) <= 2 for c in clusters) and len(clusters) >= 3:
        pcs = {n.pitch % 12 for n in sorted_notes}
        if is_chord_pitch_classes(pcs):
            return "arpeggio"

    # 5. mixed: 上のどれにも決まらない／迷う場合
    return "mixed"


def compute_bar_boundaries(
    meta: KaraokeMidiData,
    warp: WarpResult,
) -> list[tuple[int, float, float, float]]:
    """各小節の (小節番号, audio開始秒, audio終了秒, 1拍のaudio秒) を算出する。"""
    tpb = meta.ticks_per_beat
    tempos = meta.tempos
    sigs = meta.time_signatures

    cur_tick = 0
    boundaries: list[tuple[int, float, float, float]] = []
    dur_midi = meta.duration_s
    m = 1
    while True:
        t_start_midi = tick_to_second(cur_tick, tempos, tpb)
        ts = time_signature_at_tick(cur_tick, sigs)
        span = measure_length_ticks(ts, tpb)
        next_tick = cur_tick + span
        t_end_midi = tick_to_second(next_tick, tempos, tpb)

        t_start_audio = warp.audio_at_midi(t_start_midi)
        t_end_audio = warp.audio_at_midi(t_end_midi)
        beat_len_audio = (t_end_audio - t_start_audio) / ts.numerator

        boundaries.append((m, t_start_audio, t_end_audio, beat_len_audio))
        if t_start_midi > dur_midi + 5.0:
            break
        cur_tick = next_tick
        m += 1
    return boundaries


def classify_track_measure_notes(
    refs: Sequence[RefTrackNote],
    bar_bounds: list[tuple[int, float, float, float]],
) -> dict[int, str]:
    """正解ノートを (track_index, measure_num) ごとに分類し、各ノートの id -> 型の辞書を返す。"""
    m_starts = [b[1] for b in bar_bounds]
    beat_lens = {b[0]: b[3] for b in bar_bounds}

    notes_by_track_m: dict[tuple[int, int], list[EvalNote]] = defaultdict(list)
    for rn in refs:
        idx = bisect.bisect_right(m_starts, rn.note.start_s)
        m_num = max(1, idx)
        notes_by_track_m[(rn.track_index, m_num)].append(rn.note)

    note_to_style: dict[int, str] = {}
    for (ti, m_num), nlist in notes_by_track_m.items():
        st = classify_bar(nlist, beat_lens.get(m_num, 0.5))
        for n in nlist:
            note_to_style[id(n)] = st
    return note_to_style


def categorize_miss(
    ref_note: EvalNote,
    candidate_sub: Sequence[tuple[float, int]],
) -> str:
    """正解ノートを候補群（±300ms以内）に対して 5 つの外し方に分類する。

    複数該当の場合は上から優先:
    1. 見つかった: ±50ms・同音高
    2. 時間だけずれ: 同音高で 50〜300ms
    3. オクターブ違い: ±50ms で音高差が ±12 の倍数
    4. 音名違い: ±50ms で音高差が上記以外
    5. 何も無い: ±300ms 以内に候補なし
    """
    t_ref = ref_note.start_s
    p_ref = ref_note.pitch

    # 1. 見つかった
    if any(p == p_ref and abs(t - t_ref) <= 0.050 for t, p in candidate_sub):
        return "見つかった"

    # 2. 時間だけずれ
    if any(p == p_ref and 0.050 < abs(t - t_ref) <= 0.300 for t, p in candidate_sub):
        return "時間だけずれ"

    # 3. オクターブ違い
    if any(
        abs(t - t_ref) <= 0.050 and p != p_ref and (p - p_ref) % 12 == 0
        for t, p in candidate_sub
    ):
        return "オクターブ違い"

    # 4. 音名違い
    if any(
        abs(t - t_ref) <= 0.050 and (p - p_ref) % 12 != 0
        for t, p in candidate_sub
    ):
        return "音名違い"

    # 5. 何も無い
    return "何も無い"


def measure_track_edit_costs(
    gt_notes: list[EvalNote],
    draft_notes: list[EvalNote],
) -> dict[str, int]:
    """トラックごとの正解ノート群と下書きノート群から 5 種類の編集コストを算出する。"""
    gt_sorted = sorted(gt_notes, key=lambda n: (n.start_s, n.pitch))
    draft_sorted = sorted(draft_notes, key=lambda n: (n.start_s, n.pitch))

    # 1対1照合（同音高、開始差 <= 300ms、差の小さい順）
    pairs: list[tuple[float, int, int]] = []
    for gi, gn in enumerate(gt_sorted):
        for di, dn in enumerate(draft_sorted):
            if gn.pitch == dn.pitch:
                dt = abs(gn.start_s - dn.start_s)
                if dt <= 0.300:
                    pairs.append((dt, gi, di))
    pairs.sort(key=lambda x: x[0])

    used_g: set[int] = set()
    used_d: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    for dt, gi, di in pairs:
        if gi in used_g or di in used_d:
            continue
        used_g.add(gi)
        used_d.add(di)
        offset = draft_sorted[di].start_s - gt_sorted[gi].start_s
        matches.append((gi, di, offset))

    # 追加: 正解にあって下書きに無いノート (1ノートにつき1)
    cost_add = len(gt_sorted) - len(matches)

    # 範囲量子化 & 個別編集
    matches.sort(key=lambda m: draft_sorted[m[1]].start_s)
    in_run: set[int] = set()
    current_run: list[int] = []
    run_dir = 0
    cost_quant = 0

    for idx, (_gi, _di, off) in enumerate(matches):
        if 0.010 <= off <= 0.100:
            d = 1
        elif -0.100 <= off <= -0.010:
            d = -1
        else:
            d = 0

        if d != 0:
            if run_dir == d:
                current_run.append(idx)
            else:
                if len(current_run) >= 4:
                    cost_quant += 1
                    in_run.update(current_run)
                current_run = [idx]
                run_dir = d
        else:
            if len(current_run) >= 4:
                cost_quant += 1
                in_run.update(current_run)
            current_run = []
            run_dir = 0
    if len(current_run) >= 4:
        cost_quant += 1
        in_run.update(current_run)

    # 個別編集: 範囲量子化に含まれず、位置または長さのずれがあるノート (1ノートにつき1)
    cost_edit = 0
    for idx, (gi, di, off) in enumerate(matches):
        if idx not in in_run:
            gn = gt_sorted[gi]
            dn = draft_sorted[di]
            len_diff = abs((dn.end_s - dn.start_s) - (gn.end_s - gn.start_s))
            if abs(off) >= 0.010 or len_diff >= 0.100:
                cost_edit += 1

    # 一括削除 vs 個別削除
    unmatched_d = [dn for di, dn in enumerate(draft_sorted) if di not in used_d]
    gt_pitches = [n.pitch for n in gt_sorted]
    min_p = min(gt_pitches) if gt_pitches else 0
    max_p = max(gt_pitches) if gt_pitches else 127
    gt_starts = np.array([n.start_s for n in gt_sorted], dtype=float)

    bulk_candidates: list[EvalNote] = []
    cost_del_ind = 0

    for dn in unmatched_d:
        out_of_pitch = dn.pitch < min_p or dn.pitch > max_p
        near_gt = False
        if len(gt_starts) > 0:
            idx = int(np.searchsorted(gt_starts, dn.start_s))
            for j in (idx - 1, idx, idx + 1):
                if 0 <= j < len(gt_starts):
                    if abs(gt_starts[j] - dn.start_s) <= 0.500:  # 前後約1拍 (0.5秒)
                        near_gt = True
                        break
        if out_of_pitch or not near_gt:
            bulk_candidates.append(dn)
        else:
            cost_del_ind += 1

    # 一括削除クラスタ: 連続したかたまり（時間差 <= 1.0秒）ごとに 1
    cost_del_bulk = 0
    if bulk_candidates:
        bulk_candidates.sort(key=lambda n: n.start_s)
        cost_del_bulk = 1
        for k in range(len(bulk_candidates) - 1):
            if bulk_candidates[k + 1].start_s - bulk_candidates[k].start_s > 1.0:
                cost_del_bulk += 1

    return {
        "一括削除": cost_del_bulk,
        "個別削除": cost_del_ind,
        "範囲量子化": cost_quant,
        "個別編集": cost_edit,
        "追加": cost_add,
    }
