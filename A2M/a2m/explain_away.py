"""explain-away 測定（P1-13）：確定トラックで候補を落とす効果。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from a2m.evaluate import (
    EvalConfig,
    EvalNote,
    GT_EXCLUDED_ROLES,
    _f1_from_counts,
    _match_onsets_smallest_first,
    _valid_duration_notes,
    evaluate_drums,
    stack_drum_hits,
)
ExplainMode = Literal["A", "B", "C"]
HARMONIC_UP = frozenset({12, 19, 24, 28, 31})
MERGE_WINDOW_S = 0.02

AGG_ROLES = ("drums", "bass", "melody", "other")


def agg_role_for_track(role: str) -> str | None:
    if role in GT_EXCLUDED_ROLES:
        return None
    if role == "melody2":
        return "melody"
    if role in AGG_ROLES:
        return role
    return None


def pitch_relation(c_pitch: int, g_pitch: int) -> str:
    d = int(c_pitch) - int(g_pitch)
    if d == 0:
        return "same"
    if d in HARMONIC_UP:
        return "harmonic"
    if d == -12:
        return "down_oct"
    return "none"


def explain_drop(
    c_start: float,
    c_pitch: int,
    g_start: float,
    g_pitch: int,
    tol_s: float,
    mode: ExplainMode,
) -> bool:
    if mode == "A":
        return False
    if abs(float(c_start) - float(g_start)) > tol_s:
        return False
    rel = pitch_relation(c_pitch, g_pitch)
    if mode == "B":
        return rel == "same"
    if mode == "C":
        return rel in ("same", "harmonic", "down_oct")
    raise ValueError(mode)


@dataclass(frozen=True)
class ConfirmedNote:
    note: EvalNote
    agg_role: str


def _confirmed_by_start(
    confirmed: list[ConfirmedNote],
) -> tuple[list[ConfirmedNote], np.ndarray]:
    order = sorted(range(len(confirmed)), key=lambda i: confirmed[i].note.start_s)
    sorted_c = [confirmed[i] for i in order]
    starts = np.array([c.note.start_s for c in sorted_c], dtype=float)
    return sorted_c, starts


def filter_candidates(
    candidates: list[EvalNote],
    confirmed: list[ConfirmedNote],
    mode: ExplainMode,
    tol_s: float,
) -> tuple[list[EvalNote], list[EvalNote], list[tuple[EvalNote, ConfirmedNote]]]:
    if mode == "A" or not confirmed:
        return list(candidates), [], []
    sorted_c, starts = _confirmed_by_start(confirmed)
    kept: list[EvalNote] = []
    dropped: list[EvalNote] = []
    reasons: list[tuple[EvalNote, ConfirmedNote]] = []
    for c in candidates:
        drop_g: ConfirmedNote | None = None
        lo = float(c.start_s) - tol_s
        hi = float(c.start_s) + tol_s
        i0 = int(np.searchsorted(starts, lo, side="left"))
        i1 = int(np.searchsorted(starts, hi, side="right"))
        for g in sorted_c[i0:i1]:
            gn = g.note
            if explain_drop(c.start_s, c.pitch, gn.start_s, gn.pitch, tol_s, mode):
                drop_g = g
                break
        if drop_g is not None:
            dropped.append(c)
            reasons.append((c, drop_g))
        else:
            kept.append(c)
    return kept, dropped, reasons


@dataclass
class CandidateNote:
    role: str
    pitch: int
    start_s: float
    end_s: float
    drum_class: str | None
    methods: list[str] = field(default_factory=list)
    pan: float | None = None
    pan_iqr: float | None = None

    def to_eval_note(self) -> EvalNote:
        return EvalNote(
            role=self.role,
            pitch=self.pitch,
            start_s=self.start_s,
            end_s=self.end_s,
            drum_class=self.drum_class,
        )


def merge_method_candidates(
    notes: list[tuple[str, EvalNote]],
    merge_window_s: float = MERGE_WINDOW_S,
) -> list[CandidateNote]:
    """同音高・開始 merge_window_s 以内を1候補に（方式タグを残す）。"""
    if not notes:
        return []
    notes_sorted = sorted(notes, key=lambda x: (x[1].pitch, x[1].start_s))
    out: list[CandidateNote] = []
    i = 0
    while i < len(notes_sorted):
        method0, n0 = notes_sorted[i]
        methods = [method0]
        start = n0.start_s
        end = n0.end_s
        role = n0.role
        drum_class = n0.drum_class
        pitch = n0.pitch
        j = i + 1
        while j < len(notes_sorted):
            mj, nj = notes_sorted[j]
            if nj.pitch != pitch:
                break
            if nj.start_s - start > merge_window_s:
                break
            methods.append(mj)
            end = max(end, nj.end_s)
            j += 1
        out.append(
            CandidateNote(
                role=role,
                pitch=pitch,
                start_s=start,
                end_s=end,
                drum_class=drum_class,
                methods=sorted(set(methods)),
            )
        )
        i = j
    return out


def merge_candidates_anchored(
    existing_cands: list[CandidateNote],
    new_notes: list[tuple[str, EvalNote]],
    merge_window_s: float = MERGE_WINDOW_S,
) -> list[CandidateNote]:
    """既存候補の時刻・役割・音高を一切動かさず、新規ノートを既存候補に吸収または新規追加する。"""
    anchors = [
        CandidateNote(
            role=c.role,
            pitch=c.pitch,
            start_s=c.start_s,
            end_s=c.end_s,
            drum_class=c.drum_class,
            methods=list(c.methods),
            pan=c.pan,
            pan_iqr=c.pan_iqr,
        )
        for c in existing_cands
    ]

    anchors_by_role_pitch = defaultdict(list)
    for idx, a in enumerate(anchors):
        anchors_by_role_pitch[(a.role, a.pitch)].append((idx, a.start_s))

    for k in anchors_by_role_pitch:
        anchors_by_role_pitch[k].sort(key=lambda x: x[1])

    unmatched_new: list[tuple[str, EvalNote]] = []

    for method, note in new_notes:
        k = (note.role, note.pitch)
        matched = False
        if k in anchors_by_role_pitch:
            cand_list = anchors_by_role_pitch[k]
            starts = [x[1] for x in cand_list]
            pos = int(np.searchsorted(starts, note.start_s))

            best_idx = None
            best_dist = 999.0
            for p in (pos - 1, pos, pos + 1):
                if 0 <= p < len(cand_list):
                    dist = abs(cand_list[p][1] - note.start_s)
                    if dist <= merge_window_s and dist < best_dist:
                        best_dist = dist
                        best_idx = cand_list[p][0]

            if best_idx is not None:
                a = anchors[best_idx]
                if method not in a.methods:
                    a.methods.append(method)
                a.end_s = max(a.end_s, note.end_s)
                matched = True

        if not matched:
            unmatched_new.append((method, note))

    new_by_role = defaultdict(list)
    for m, n in unmatched_new:
        new_by_role[n.role].append((m, n))

    merged_new: list[CandidateNote] = []
    for r, notes_for_r in new_by_role.items():
        merged_new.extend(merge_method_candidates(notes_for_r, merge_window_s=merge_window_s))

    result = anchors + merged_new
    result.sort(key=lambda c: (c.start_s, c.pitch))
    return result


def _onset_prf_by_pitch(
    ref_notes: list[EvalNote],
    est_notes: list[EvalNote],
    tol_s: float,
) -> tuple[float, float, float]:
    """開始±tol・同一音高の1対1照合（段階1の開始F1と同系）。"""
    ref_v = _valid_duration_notes(ref_notes)
    est_v = _valid_duration_notes(est_notes)
    if not ref_v and not est_v:
        return 1.0, 1.0, 1.0
    if not ref_v:
        return 0.0, 0.0, 0.0
    if not est_v:
        return 0.0, 0.0, 0.0
    pitches = {n.pitch for n in ref_v} | {n.pitch for n in est_v}
    tp = fp = fn = 0
    for pitch in pitches:
        ref_t = np.array(
            [n.start_s for n in ref_v if n.pitch == pitch],
            dtype=float,
        )
        est_t = np.array(
            [n.start_s for n in est_v if n.pitch == pitch],
            dtype=float,
        )
        tpi, fpi, fni = _match_onsets_smallest_first(ref_t, est_t, tol_s)
        tp += tpi
        fp += fpi
        fn += fni
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = _f1_from_counts(tp, fp, fn)
    return p, r, f1


def track_k_metrics(
    ref_track: list[EvalNote],
    est_pool: list[EvalNote],
    track_role: str,
    cfg: EvalConfig,
) -> dict[str, float | int]:
    if track_role == "drums":
        d = evaluate_drums(ref_track, est_pool, cfg)
        ref_stacks = stack_drum_hits(ref_track, cfg.drum_stack_window_s)
        est_stacks = stack_drum_hits(est_pool, cfg.drum_stack_window_s)
        ref_t = np.array([h.start_s for h in ref_stacks], dtype=float)
        est_t = np.array([h.start_s for h in est_stacks], dtype=float)
        tp, fp, fn = _match_onsets_smallest_first(ref_t, est_t, cfg.onset_tol_s)
        f1 = _f1_from_counts(tp, fp, fn)
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        return {
            "n_candidates": len(est_pool),
            "precision": p,
            "recall": r,
            "f1": f1,
            "ref_notes": len(ref_track),
        }
    p, r, f1 = _onset_prf_by_pitch(ref_track, est_pool, cfg.onset_tol_s)
    return {
        "n_candidates": len(est_pool),
        "precision": p,
        "recall": r,
        "f1": f1,
        "ref_notes": len(ref_track),
    }


def candidate_matches_ref_onset(
    c: EvalNote,
    ref_track: list[EvalNote],
    track_role: str,
    cfg: EvalConfig,
) -> bool:
    """巻き込み判定：候補がトラック k 正解の1打点と一致するか。"""
    if not ref_track:
        return False
    if track_role == "drums":
        cls = c.drum_class or "other"
        ref_cls = [n for n in ref_track if (n.drum_class or "other") == cls]
        if not ref_cls:
            return False
        ref_t = np.array([n.start_s for n in ref_cls], dtype=float)
        est_t = np.array([c.start_s], dtype=float)
        tp, _fp, _fn = _match_onsets_smallest_first(ref_t, est_t, cfg.onset_tol_s)
        return tp > 0
    for r in ref_track:
        if r.pitch != c.pitch:
            continue
        if abs(r.start_s - c.start_s) <= cfg.onset_tol_s and r.end_s > r.start_s:
            return True
    return False


def collateral_stats(
    dropped: list[EvalNote],
    ref_track: list[EvalNote],
    track_role: str,
    cfg: EvalConfig,
) -> tuple[int, float]:
    if not ref_track:
        return 0, float("nan")
    hit = sum(
        1
        for c in dropped
        if candidate_matches_ref_onset(c, ref_track, track_role, cfg)
    )
    ref_n = len(ref_track)
    if track_role == "drums":
        ref_n = len(stack_drum_hits(ref_track, cfg.drum_stack_window_s))
    ratio = hit / ref_n if ref_n else float("nan")
    return hit, ratio


@dataclass(frozen=True)
class RefTrackNote:
    track_index: int
    note: EvalNote


def mean_finite(values: list[float]) -> float:
    vals = [v for v in values if v == v]
    if not vals:
        return float("nan")
    return sum(vals) / len(vals)
