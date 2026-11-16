"""採譜結果と正解の比較（仕様 第5節）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import mir_eval
import numpy as np

from a2m.units import midi_to_hz

GT_EXCLUDED_ROLES = frozenset(
    {"melody_unpitched", "chorus", "se", "exclusive", "unknown", "countin"}
)
# ショートディレイ複製トラックのノートは gt_aligned 書き出し前に除外（align.json delay_copy）。
REF_VOCAL_ROLES = frozenset({"melody", "melody2"})
PITCHED_EVAL_ROLES = frozenset({"melody", "bass", "other"})
EST_VOCAL_ROLE = "melody"


def ref_notes_for_eval_role(notes: list[EvalNote], role: str) -> list[EvalNote]:
    if role == EST_VOCAL_ROLE:
        return [n for n in notes if n.role in REF_VOCAL_ROLES]
    return [n for n in notes if n.role == role]


def est_notes_for_eval_role(notes: list[EvalNote], role: str) -> list[EvalNote]:
    if role == EST_VOCAL_ROLE:
        return [n for n in notes if n.role == EST_VOCAL_ROLE]
    return [n for n in notes if n.role == role]


@dataclass(frozen=True)
class EvalNote:
    role: str
    pitch: int
    start_s: float
    end_s: float
    drum_class: str | None = None


@dataclass(frozen=True)
class DrumStackedHit:
    drum_class: str
    start_s: float
    stacked_n: int


@dataclass(frozen=True)
class EvalConfig:
    onset_tol_s: float
    offset_ratio: float
    offset_min_tol_s: float
    drum_stack_window_s: float = 0.02

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvalConfig:
        return cls(
            onset_tol_s=float(d["onset_tol_s"]),
            offset_ratio=float(d["offset_ratio"]),
            offset_min_tol_s=float(d["offset_min_tol_s"]),
            drum_stack_window_s=float(d.get("drum_stack_window_s", 0.02)),
        )


def eval_window_bounds(
    ref_notes: list[EvalNote],
    audio_duration_s: float,
) -> tuple[float, float] | None:
    """正解の評価対象ノートから [t0, t1]（開始のみ）を求める。"""
    usable = [
        n
        for n in ref_notes
        if n.role not in GT_EXCLUDED_ROLES and 0.0 <= n.start_s <= audio_duration_s
    ]
    if not usable:
        return None
    t0 = min(n.start_s for n in usable)
    t1 = max(n.start_s for n in usable)
    return t0, t1


def filter_notes_in_window(
    notes: list[EvalNote],
    t0: float,
    t1: float,
    audio_duration_s: float,
) -> list[EvalNote]:
    return [
        n
        for n in notes
        if t0 <= n.start_s <= t1 and 0.0 <= n.start_s <= audio_duration_s
    ]


def filter_notes_excluding_intervals(
    notes: list[EvalNote],
    exclude_intervals: list[dict[str, Any]],
    audio_duration_s: float,
) -> list[EvalNote]:
    if not exclude_intervals:
        return [n for n in notes if 0.0 <= n.start_s <= audio_duration_s]

    def excluded(t: float) -> bool:
        return any(
            float(iv["start_s"]) <= t < float(iv["end_s"]) for iv in exclude_intervals
        )

    return [
        n
        for n in notes
        if 0.0 <= n.start_s <= audio_duration_s and not excluded(n.start_s)
    ]


def _valid_duration_notes(notes: list[EvalNote]) -> list[EvalNote]:
    return [n for n in notes if n.end_s > n.start_s]


def _to_intervals_pitches(
    notes: list[EvalNote],
) -> tuple[np.ndarray, np.ndarray]:
    notes = _valid_duration_notes(notes)
    if not notes:
        return np.zeros((0, 2), dtype=float), np.zeros((0,), dtype=float)
    intervals = np.array([[n.start_s, n.end_s] for n in notes], dtype=float)
    pitches = np.array([midi_to_hz(float(n.pitch)) for n in notes], dtype=float)
    return intervals, pitches


def _pitch_class_hz(midi: int) -> float:
    """オクターブ無視比較用：音高クラスだけ残す。"""
    pc = int(midi) % 12
    return midi_to_hz(48.0 + pc)


def _mir_f1(
    ref_intervals: np.ndarray,
    ref_pitches: np.ndarray,
    est_intervals: np.ndarray,
    est_pitches: np.ndarray,
    cfg: EvalConfig,
    *,
    offset_ratio: float | None,
) -> float:
    if ref_intervals.size == 0 and est_intervals.size == 0:
        return 1.0
    if ref_intervals.size == 0 or est_intervals.size == 0:
        return 0.0
    scores = mir_eval.transcription.precision_recall_f1_overlap(
        ref_intervals,
        ref_pitches,
        est_intervals,
        est_pitches,
        onset_tolerance=cfg.onset_tol_s,
        offset_ratio=offset_ratio,
        offset_min_tolerance=cfg.offset_min_tol_s,
    )
    f1 = scores[2]
    return float(f1)


def evaluate_pitched_onset_prf(
    ref_notes: list[EvalNote],
    est_notes: list[EvalNote],
    cfg: EvalConfig,
) -> dict[str, float]:
    """開始のみの precision / recall / F1（mir_eval）。"""
    ref_v = _valid_duration_notes(ref_notes)
    est_v = _valid_duration_notes(est_notes)
    ref_i, ref_p = _to_intervals_pitches(ref_v)
    est_i, est_p = _to_intervals_pitches(est_v)
    if ref_i.size == 0 and est_i.size == 0:
        return {"precision": 1.0, "recall": 1.0, "onset_f1": 1.0}
    if ref_i.size == 0:
        return {"precision": 0.0, "recall": 0.0, "onset_f1": 0.0}
    if est_i.size == 0:
        return {"precision": 0.0, "recall": 0.0, "onset_f1": 0.0}
    scores = mir_eval.transcription.precision_recall_f1_overlap(
        ref_i,
        ref_p,
        est_i,
        est_p,
        onset_tolerance=cfg.onset_tol_s,
        offset_ratio=None,
        offset_min_tolerance=cfg.offset_min_tol_s,
    )
    return {
        "precision": float(scores[0]),
        "recall": float(scores[1]),
        "onset_f1": float(scores[2]),
    }


def evaluate_pitched_role(
    ref_notes: list[EvalNote],
    est_notes: list[EvalNote],
    cfg: EvalConfig,
) -> dict[str, float]:
    prf = evaluate_pitched_onset_prf(ref_notes, est_notes, cfg)
    ref_v = _valid_duration_notes(ref_notes)
    est_v = _valid_duration_notes(est_notes)
    ref_i, ref_p = _to_intervals_pitches(ref_v)
    est_i, est_p = _to_intervals_pitches(est_v)
    onset_f1 = prf["onset_f1"]
    both_f1 = _mir_f1(
        ref_i,
        ref_p,
        est_i,
        est_p,
        cfg,
        offset_ratio=cfg.offset_ratio,
    )
    ref_pc = np.array([_pitch_class_hz(n.pitch) for n in ref_v], dtype=float)
    est_pc = np.array([_pitch_class_hz(n.pitch) for n in est_v], dtype=float)
    octave_f1 = _mir_f1(ref_i, ref_pc, est_i, est_pc, cfg, offset_ratio=None)
    return {
        "onset_f1": onset_f1,
        "onset_offset_f1": both_f1,
        "octave_f1": octave_f1,
    }


def stack_drum_hits(
    notes: list[EvalNote],
    window_s: float,
) -> list[DrumStackedHit]:
    """同クラスで開始が window_s 以内のノートを1打点に（開始は最も早い）。"""
    by_cls: dict[str, list[float]] = {}
    for n in notes:
        cls = n.drum_class or "other"
        by_cls.setdefault(cls, []).append(float(n.start_s))
    hits: list[DrumStackedHit] = []
    for cls, times in by_cls.items():
        times.sort()
        i = 0
        while i < len(times):
            group_start = times[i]
            j = i + 1
            while j < len(times) and times[j] - group_start <= window_s:
                j += 1
            hits.append(DrumStackedHit(cls, group_start, j - i))
            i = j
    return hits


def _match_onsets_smallest_first(
    ref_times: np.ndarray,
    est_times: np.ndarray,
    tol: float,
) -> tuple[int, int, int]:
    """差の小さい順に1対1照合（±tol）。"""
    if ref_times.size == 0 and est_times.size == 0:
        return 0, 0, 0
    if ref_times.size == 0:
        return 0, int(est_times.size), 0
    if est_times.size == 0:
        return 0, 0, int(ref_times.size)

    sort_idx = np.argsort(est_times)
    est_sorted = est_times[sort_idx]

    pairs: list[tuple[float, int, int]] = []
    for i, rt in enumerate(ref_times):
        val = float(rt)
        l = int(np.searchsorted(est_sorted, val - tol))
        r = int(np.searchsorted(est_sorted, val + tol, side="right"))
        for k in range(l, r):
            d = abs(val - float(est_sorted[k]))
            pairs.append((d, i, int(sort_idx[k])))
    pairs.sort(key=lambda x: (x[0], x[1], x[2]))
    used_r: set[int] = set()
    used_e: set[int] = set()
    tp = 0
    for _d, i, j in pairs:
        if i in used_r or j in used_e:
            continue
        used_r.add(i)
        used_e.add(j)
        tp += 1
    fn = int(ref_times.size) - tp
    fp = int(est_times.size) - tp
    return tp, fp, fn


def _f1_from_counts(tp: int, fp: int, fn: int) -> float:
    if tp == 0 and fp == 0 and fn == 0:
        return 1.0
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    if p + r == 0.0:
        return 0.0
    return 2.0 * p * r / (p + r)


def _evaluate_drums_by_pitch(
    ref_notes: list[EvalNote],
    est_notes: list[EvalNote],
    cfg: EvalConfig,
) -> dict[str, float]:
    """参考：音高ごと（まとめなし）の F1。"""
    pitches = sorted({n.pitch for n in ref_notes} | {n.pitch for n in est_notes})
    per_pitch: dict[str, float] = {}
    ref_classes = [n.drum_class or "other" for n in ref_notes]
    for pitch in pitches:
        ref_t = np.array(
            [n.start_s for n in ref_notes if n.pitch == pitch],
            dtype=float,
        )
        est_t = np.array(
            [n.start_s for n in est_notes if n.pitch == pitch],
            dtype=float,
        )
        tp, fp, fn = _match_onsets_smallest_first(ref_t, est_t, cfg.onset_tol_s)
        per_pitch[str(pitch)] = _f1_from_counts(tp, fp, fn)
    per_class: dict[str, list[tuple[float, int]]] = {}
    for n, cls in zip(ref_notes, ref_classes):
        per_class.setdefault(cls, []).append((float(n.start_s), n.pitch))
    per_class_f1: dict[str, float] = {}
    for cls in sorted(set(ref_classes) | {n.drum_class or "other" for n in est_notes}):
        ref_t = np.array(
            [n.start_s for n, c in zip(ref_notes, ref_classes) if c == cls],
            dtype=float,
        )
        est_t = np.array(
            [
                n.start_s
                for n in est_notes
                if (n.drum_class or "other") == cls
            ],
            dtype=float,
        )
        tp, fp, fn = _match_onsets_smallest_first(ref_t, est_t, cfg.onset_tol_s)
        per_class_f1[cls] = _f1_from_counts(tp, fp, fn)
    return per_class_f1


def evaluate_drums(
    ref_notes: list[EvalNote],
    est_notes: list[EvalNote],
    cfg: EvalConfig,
) -> dict[str, Any]:
    ref_stacks = stack_drum_hits(ref_notes, cfg.drum_stack_window_s)
    est_stacks = stack_drum_hits(est_notes, cfg.drum_stack_window_s)
    all_classes = sorted({h.drum_class for h in ref_stacks} | {h.drum_class for h in est_stacks})
    per_class: dict[str, dict[str, float | int]] = {}
    for cls in all_classes:
        ref_t = np.array(
            [h.start_s for h in ref_stacks if h.drum_class == cls],
            dtype=float,
        )
        est_t = np.array(
            [h.start_s for h in est_stacks if h.drum_class == cls],
            dtype=float,
        )
        tp, fp, fn = _match_onsets_smallest_first(ref_t, est_t, cfg.onset_tol_s)
        ref_notes_n = sum(1 for n in ref_notes if (n.drum_class or "other") == cls)
        per_class[cls] = {
            "f1": _f1_from_counts(tp, fp, fn),
            "p": tp / (tp + fp) if tp + fp else 0.0,
            "r": tp / (tp + fn) if tp + fn else 0.0,
            "ref_notes": ref_notes_n,
            "ref_stacks": int(ref_t.size),
            "est_stacks": int(est_t.size),
        }
    pitch_f1_by_class = _evaluate_drums_by_pitch(ref_notes, est_notes, cfg)

    def _weighted(d: dict[str, float]) -> float:
        num = 0.0
        den = 0.0
        for cls, row in per_class.items():
            w = int(row["ref_stacks"])
            if w <= 0:
                continue
            f1 = d.get(cls, float("nan"))
            if f1 == f1:
                num += f1 * w
                den += w
        return num / den if den else float("nan")

    stack_f1 = {
        cls: float(row["f1"]) for cls, row in per_class.items()
    }
    weighted_stack_f1 = _weighted(stack_f1)
    weighted_pitch_f1 = _weighted(pitch_f1_by_class)

    overlap: dict[str, float] = {}
    for cls in ("kick", "snare"):
        cls_stacks = [h for h in ref_stacks if h.drum_class == cls]
        if not cls_stacks:
            overlap[cls] = float("nan")
        else:
            overlap[cls] = sum(1 for h in cls_stacks if h.stacked_n >= 2) / len(
                cls_stacks
            )

    return {
        "per_class": stack_f1,
        "per_class_detail": per_class,
        "per_class_pitch_f1": pitch_f1_by_class,
        "weighted_stack_f1": weighted_stack_f1,
        "weighted_pitch_f1": weighted_pitch_f1,
        "stack_overlap_ratio": overlap,
        "ref_notes_total": len(ref_notes),
        "ref_stacks_total": len(ref_stacks),
        "est_notes_total": len(est_notes),
        "est_stacks_total": len(est_stacks),
    }


def evaluate_all_pitched(
    ref_notes: list[EvalNote],
    est_notes: list[EvalNote],
    cfg: EvalConfig,
) -> float:
    ref = [
        n
        for n in ref_notes
        if n.role in REF_VOCAL_ROLES or n.role in ("bass", "other")
    ]
    est = [n for n in est_notes if n.role in PITCHED_EVAL_ROLES]
    metrics = evaluate_pitched_role(ref, est, cfg)
    return metrics["onset_f1"]


def evaluate_song_method(
    ref_notes: list[EvalNote],
    est_notes: list[EvalNote],
    audio_duration_s: float,
    cfg: EvalConfig,
    *,
    exclude_intervals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    bounds = eval_window_bounds(ref_notes, audio_duration_s)
    if bounds is None:
        return {"window": None, "roles": {}, "drums": {}, "all_pitched_onset_f1": float("nan")}
    t0, t1 = bounds
    ref_base = filter_notes_in_window(
        [n for n in ref_notes if n.role not in GT_EXCLUDED_ROLES],
        t0,
        t1,
        audio_duration_s,
    )
    est_base = filter_notes_in_window(est_notes, t0, t1, audio_duration_s)
    ref_w = filter_notes_excluding_intervals(
        ref_base, exclude_intervals or [], audio_duration_s
    )
    est_w = filter_notes_excluding_intervals(
        est_base, exclude_intervals or [], audio_duration_s
    )

    roles_out: dict[str, dict[str, float]] = {}
    for role in sorted(PITCHED_EVAL_ROLES):
        ref_r = ref_notes_for_eval_role(ref_w, role)
        est_r = est_notes_for_eval_role(est_w, role)
        if not ref_r and not est_r:
            continue
        roles_out[role] = evaluate_pitched_role(ref_r, est_r, cfg)

    ref_d = [n for n in ref_w if n.role == "drums"]
    est_d = [n for n in est_w if n.role == "drums"]
    drums_out = evaluate_drums(ref_d, est_d, cfg) if ref_d or est_d else {}

    all_p = evaluate_all_pitched(ref_w, est_w, cfg)
    return {
        "window": {"t0": t0, "t1": t1},
        "roles": roles_out,
        "drums": drums_out,
        "all_pitched_onset_f1": all_p,
    }
