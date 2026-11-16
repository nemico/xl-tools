"""選別モデル用の候補特徴量テーブルと正解ラベルの作成。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from a2m.evaluate import EvalNote
from a2m.explain_away import CandidateNote
from a2m.scoring_features import (
    compute_f1_explained,
    compute_f2_pitch_range,
    compute_f3_pitch_classes,
    compute_f4_duration,
    compute_f5_used_grid,
    compute_f6_polyphony,
    compute_f7_onset_strength,
    compute_f8_grid_distance,
    compute_f9_panning,
)
from a2m.track_profile import TrackProfile

ALL_METHODS = (
    "M1",
    "M2",
    "M4",
    "M6",
    "M8",
    "M10",
    "M11",
    "M12",
    "M13",
    "M14",
    "M15",
    "M16",
    "M17",
    "M18",
    "M19",
    "M20",
    "M1_delim",
    "M2_delim",
    "M6_delim",
    "M_dereverb",
    "drumsep_hihat",
)

MATCH_TYPES = ("none", "exact", "shifted", "octave", "pitch_diff")


def classify_candidate_match(
    cand_start: float,
    cand_pitch: int,
    cand_drum_class: str | None,
    cand_role: str,
    gt_notes: Sequence[EvalNote],
    onset_tol_s: float = 0.050,
    quantize_tol_s: float = 0.300,
) -> tuple[int, str]:
    """1つの候補に対し、最も優先度が高い正解との対応関係を返す。

    戻り値: (label: 0 or 1, match_type: str)
    exact (合ってる): label=1
    shifted (ずれているだけ): label=0
    octave (オクターブ違い): label=0
    pitch_diff (音名違い): label=0
    none (なし): label=0
    """
    is_drums = cand_role == "drums"
    best_priority = 0
    best_dt = float("inf")
    best_type = "none"

    for gt in gt_notes:
        dt = abs(cand_start - gt.start_s)
        if dt > quantize_tol_s and dt > onset_tol_s:
            continue

        if is_drums:
            same = (cand_drum_class or "") == (gt.drum_class or "") or (cand_pitch == gt.pitch)
            if same and dt <= onset_tol_s:
                if 4 > best_priority or (best_priority == 4 and dt < best_dt):
                    best_priority = 4
                    best_dt = dt
                    best_type = "exact"
            elif same and dt <= quantize_tol_s:
                if 3 > best_priority or (best_priority == 3 and dt < best_dt):
                    best_priority = 3
                    best_dt = dt
                    best_type = "shifted"
            elif dt <= onset_tol_s:
                if 1 > best_priority or (best_priority == 1 and dt < best_dt):
                    best_priority = 1
                    best_dt = dt
                    best_type = "pitch_diff"
        else:
            same_pitch = cand_pitch == gt.pitch
            if same_pitch and dt <= onset_tol_s:
                if 4 > best_priority or (best_priority == 4 and dt < best_dt):
                    best_priority = 4
                    best_dt = dt
                    best_type = "exact"
            elif same_pitch and dt <= quantize_tol_s:
                if 3 > best_priority or (best_priority == 3 and dt < best_dt):
                    best_priority = 3
                    best_dt = dt
                    best_type = "shifted"
            elif dt <= onset_tol_s:
                dp = cand_pitch - gt.pitch
                if dp != 0 and dp % 12 == 0:
                    if 2 > best_priority or (best_priority == 2 and dt < best_dt):
                        best_priority = 2
                        best_dt = dt
                        best_type = "octave"
                else:
                    if 1 > best_priority or (best_priority == 1 and dt < best_dt):
                        best_priority = 1
                        best_dt = dt
                        best_type = "pitch_diff"

    label = 1 if best_type == "exact" else 0
    return label, best_type


def build_candidate_row(
    cand: CandidateNote,
    profile: TrackProfile,
    confirmed_notes: Sequence[EvalNote],
    grid_points: dict[str, np.ndarray],
    fine_grid_points: np.ndarray,
    onset_env: np.ndarray,
    sr: int,
    hop: int,
    song_info: dict[str, Any],
    gt_notes: Sequence[EvalNote] | None = None,
    onset_tol_s: float = 0.050,
    quantize_tol_s: float = 0.300,
) -> dict[str, Any]:
    """1つの候補に対して全特徴量とラベルを構築する。"""
    c_list = [cand]
    f1 = float(compute_f1_explained(c_list, confirmed_notes)[0]) if confirmed_notes else 0.0
    f2 = float(compute_f2_pitch_range(c_list, profile)[0])
    f3 = float(compute_f3_pitch_classes(c_list, profile)[0])
    f4 = float(compute_f4_duration(c_list, profile)[0])
    f5 = float(compute_f5_used_grid(c_list, profile, grid_points)[0]) if grid_points else 0.0
    f6 = float(compute_f6_polyphony(c_list, profile)[0])
    f7 = float(compute_f7_onset_strength(c_list, onset_env, sr=sr, hop=hop)[0]) if len(onset_env) else 0.0
    f8 = float(compute_f8_grid_distance(c_list, fine_grid_points)[0]) if len(fine_grid_points) else 0.0
    f9 = float(compute_f9_panning(c_list, cand.pan)[0]) if cand.pan is not None else 0.0

    methods_set = set(cand.methods) if cand.methods else set()
    n_methods = len(methods_set)

    row: dict[str, Any] = {
        # 基本情報
        "role": cand.role,
        "pitch": int(cand.pitch),
        "start_s": float(cand.start_s),
        "end_s": float(cand.end_s),
        "duration": float(cand.end_s - cand.start_s),
        "drum_class": cand.drum_class or "none",
        "n_methods": n_methods,
        # scoring_features (w1..w9)
        "w1_explained": f1,
        "w2_pitch_range": f2,
        "w3_pitch_classes": f3,
        "w4_duration": f4,
        "w5_used_grid": f5,
        "w6_polyphony": f6,
        "w7_onset_strength": f7,
        "w8_grid_distance": f8,
        "w9_panning": f9,
        # トラック情報
        "track_polyphonic": 1 if profile.max_polyphony > 1 else 0,
        "track_pitch_min": profile.pitch_min,
        "track_pitch_max": profile.pitch_max,
        "track_used_grids_count": len(profile.used_grids),
        # 曲情報
        "bpm": float(song_info.get("bpm", 120.0)),
        "time_sig_num": int(song_info.get("time_sig_num", 4)),
        "time_sig_den": int(song_info.get("time_sig_den", 4)),
        "song_duration_s": float(song_info.get("duration_s", 0.0)),
    }

    # 各方式のフラグ (0/1)
    for m in ALL_METHODS:
        row[f"method_{m}"] = 1 if m in methods_set else 0

    if gt_notes is not None:
        label, mtype = classify_candidate_match(
            cand.start_s, cand.pitch, cand.drum_class, cand.role, gt_notes, onset_tol_s, quantize_tol_s
        )
        row["label"] = label
        row["match_type"] = mtype
    else:
        row["label"] = 0
        row["match_type"] = "none"

    return row
