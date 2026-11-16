"""CARD_P1-20: 統合スコアリング特徴量（w1〜w8）の抽出と重み探索。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from a2m.evaluate import EvalNote
from a2m.explain_away import CandidateNote
from a2m.track_profile import TrackProfile

HARMONIC_RELATIONS = frozenset({0, 12, 19, 24, 28, 31, -12})
FEATURE_NAMES = (
    "w1_explained",
    "w2_pitch_range",
    "w3_pitch_classes",
    "w4_duration",
    "w5_used_grid",
    "w6_polyphony",
    "w7_onset_strength",
    "w8_grid_distance",
)


def compute_f1_explained(
    candidates: Sequence[CandidateNote],
    confirmed_notes: Sequence[EvalNote],
    tol_s: float = 0.050,
) -> np.ndarray:
    """w1: 確定済みトラックのノートで説明がつくか (1.0 or 0.0)。"""
    n_c = len(candidates)
    if n_c == 0 or len(confirmed_notes) == 0:
        return np.zeros(n_c, dtype=float)

    c_starts = np.array([c.start_s for c in candidates], dtype=float)
    c_pitches = np.array([c.pitch for c in candidates], dtype=int)

    sorted_conf = sorted(confirmed_notes, key=lambda n: n.start_s)
    conf_starts = np.array([g.start_s for g in sorted_conf], dtype=float)
    conf_pitches = np.array([g.pitch for g in sorted_conf], dtype=int)

    idx0 = np.searchsorted(conf_starts, c_starts - tol_s, side="left")
    idx1 = np.searchsorted(conf_starts, c_starts + tol_s, side="right")

    f1 = np.zeros(n_c, dtype=float)
    has_cand = np.where(idx0 < idx1)[0]
    for i in has_cand:
        cp = c_pitches[i]
        for gp in conf_pitches[idx0[i]:idx1[i]]:
            if (cp - gp) in HARMONIC_RELATIONS:
                f1[i] = 1.0
                break
    return f1


def compute_f2_pitch_range(
    candidates: Sequence[CandidateNote],
    profile: TrackProfile,
) -> np.ndarray:
    """w2: 前半プロファイルの音域に入るか (1.0 or 0.0)。"""
    c_pitches = np.array([c.pitch for c in candidates], dtype=int)
    return np.where(
        (c_pitches >= profile.pitch_min) & (c_pitches <= profile.pitch_max),
        1.0,
        0.0,
    )


def compute_f3_pitch_classes(
    candidates: Sequence[CandidateNote],
    profile: TrackProfile,
) -> np.ndarray:
    """w3: 前半プロファイルの音名集合に入るか (1.0 or 0.0)。"""
    pcs = set(profile.pitch_classes)
    return np.array([1.0 if (c.pitch % 12) in pcs else 0.0 for c in candidates], dtype=float)


def compute_f4_duration(
    candidates: Sequence[CandidateNote],
    profile: TrackProfile,
) -> np.ndarray:
    """w4: 前半プロファイルの長さの範囲 (10〜90%) に入るか (1.0 or 0.0)。"""
    durs = np.array([c.end_s - c.start_s for c in candidates], dtype=float)
    return np.where(
        (durs >= profile.dur_p10) & (durs <= profile.dur_p90),
        1.0,
        0.0,
    )


def compute_f5_used_grid(
    candidates: Sequence[CandidateNote],
    profile: TrackProfile,
    grid_points: dict[str, np.ndarray],
    grid_tol_s: float = 0.030,
) -> np.ndarray:
    """w5: 前半プロファイルの使われている刻みに乗るか (1.0 or 0.0)。"""
    n_c = len(candidates)
    if n_c == 0 or not profile.used_grids:
        return np.zeros(n_c, dtype=float)

    c_starts = np.array([c.start_s for c in candidates], dtype=float)
    f5 = np.zeros(n_c, dtype=float)

    for gname in profile.used_grids:
        pts = grid_points.get(gname)
        if pts is None or len(pts) == 0:
            continue
        idx = np.searchsorted(pts, c_starts)
        idx = np.clip(idx, 0, len(pts) - 1)
        idx_prev = np.clip(idx - 1, 0, len(pts) - 1)
        err = np.minimum(np.abs(c_starts - pts[idx]), np.abs(c_starts - pts[idx_prev]))
        f5 = np.maximum(f5, np.where(err <= grid_tol_s, 1.0, 0.0))

    return f5


def compute_f6_polyphony(
    candidates: Sequence[CandidateNote],
    profile: TrackProfile,
    onset_window_s: float = 0.050,
) -> np.ndarray:
    """w6: 同時発音数を超えないか (1.0 or 0.0)。"""
    n_c = len(candidates)
    if n_c == 0:
        return np.zeros(0, dtype=float)

    c_starts = np.array([c.start_s for c in candidates], dtype=float)
    c_pitches = np.array([c.pitch for c in candidates], dtype=int)

    in_range = ((c_pitches >= profile.pitch_min) & (c_pitches <= profile.pitch_max)).astype(int)
    # 累積和による区間カウントの高速化 (O(N))
    cum = np.pad(np.cumsum(in_range), (1, 0))
    idx_lo = np.searchsorted(c_starts, c_starts - onset_window_s, side="left")
    idx_hi = np.searchsorted(c_starts, c_starts + onset_window_s, side="right")
    concurrent = cum[idx_hi] - cum[idx_lo]

    return np.where(concurrent <= profile.max_polyphony, 1.0, 0.0)


def compute_f7_onset_strength(
    candidates: Sequence[CandidateNote],
    normalized_onset_env: np.ndarray,
    sr: int = 44100,
    hop: int = 512,
) -> np.ndarray:
    """w7: その時刻の音源のオンセット強度（正規化、0.0〜1.0）。"""
    n_c = len(candidates)
    if n_c == 0 or len(normalized_onset_env) == 0:
        return np.zeros(n_c, dtype=float)

    c_starts = np.array([c.start_s for c in candidates], dtype=float)
    hop_s = hop / sr
    frames = np.round(c_starts / hop_s).astype(int)

    n_env = len(normalized_onset_env)
    # パディングしてベクトル化最大値抽出
    pad_env = np.pad(normalized_onset_env, (1, 2), mode="edge")
    fr = np.clip(frames + 1, 0, len(pad_env) - 3)
    # 窓 [fr-1, fr+2] の最大値
    f7 = np.maximum.reduce([pad_env[fr - 1], pad_env[fr], pad_env[fr + 1], pad_env[fr + 2]])
    return f7.astype(float)


def compute_f8_grid_distance(
    candidates: Sequence[CandidateNote],
    fine_grid_points: np.ndarray,
    max_dist_s: float = 0.050,
) -> np.ndarray:
    """w8: グリッドからの距離（テンポマップ基準、近いほど高得点: 0.0〜1.0）。"""
    n_c = len(candidates)
    if n_c == 0 or len(fine_grid_points) == 0:
        return np.zeros(n_c, dtype=float)

    c_starts = np.array([c.start_s for c in candidates], dtype=float)
    idx = np.searchsorted(fine_grid_points, c_starts)
    idx = np.clip(idx, 0, len(fine_grid_points) - 1)
    idx_prev = np.clip(idx - 1, 0, len(fine_grid_points) - 1)

    dist = np.minimum(
        np.abs(c_starts - fine_grid_points[idx]),
        np.abs(c_starts - fine_grid_points[idx_prev]),
    )
    # 線形減衰: 0 で 1.0, max_dist_s 以上で 0.0
    return np.maximum(0.0, 1.0 - (dist / max_dist_s))


def compute_f9_panning(
    candidates: Sequence[CandidateNote],
    track_pan: float | None,
    pan_tol: float = 0.25,
) -> np.ndarray:
    """w9: 定位がトラックの定位中央値に近いか (1.0 or 0.0)。"""
    n_c = len(candidates)
    if n_c == 0 or track_pan is None:
        return np.zeros(n_c, dtype=float)

    c_pans = np.array([c.pan if c.pan is not None else 0.0 for c in candidates], dtype=float)
    return np.where(np.abs(c_pans - track_pan) <= pan_tol, 1.0, 0.0)


def extract_features_matrix(
    candidates: Sequence[CandidateNote],
    profile: TrackProfile,
    confirmed_notes: Sequence[EvalNote],
    grid_points: dict[str, np.ndarray],
    fine_grid_points: np.ndarray,
    normalized_onset_env: np.ndarray,
    sr: int = 44100,
    hop: int = 512,
    track_pan: float | None = None,
) -> np.ndarray:
    """候補群に対する特徴量行列 (N, 8 または N, 9) を抽出する。"""
    n_c = len(candidates)
    n_feats = 9 if track_pan is not None else 8
    if n_c == 0:
        return np.zeros((0, n_feats), dtype=float)

    f1 = compute_f1_explained(candidates, confirmed_notes)
    f2 = compute_f2_pitch_range(candidates, profile)
    f3 = compute_f3_pitch_classes(candidates, profile)
    f4 = compute_f4_duration(candidates, profile)
    f5 = compute_f5_used_grid(candidates, profile, grid_points)
    f6 = compute_f6_polyphony(candidates, profile)
    f7 = compute_f7_onset_strength(candidates, normalized_onset_env, sr=sr, hop=hop)
    f8 = compute_f8_grid_distance(candidates, fine_grid_points)

    cols = [f1, f2, f3, f4, f5, f6, f7, f8]
    if track_pan is not None:
        f9 = compute_f9_panning(candidates, track_pan)
        cols.append(f9)

    return np.column_stack(cols)


def calculate_scores(
    base_scores: np.ndarray,
    features: np.ndarray,
    weights: Sequence[float],
) -> np.ndarray:
    """重みベクトル (w1..w8 または w1..w9) を用いて総合スコアを計算する。

    w1 は減点、w2..wn は加点。
    """
    w = np.asarray(weights, dtype=float)
    penalty = w[0] * features[:, 0]
    bonus = features[:, 1:] @ w[1:]
    return base_scores - penalty + bonus


@dataclass
class TrackEvalItem:
    song: str
    track_index: int
    role: str  # 'other', 'bass', 'melody'
    group: str  # 'A' or 'B'
    base_scores: np.ndarray
    features: np.ndarray  # shape (N, 8) or (N, 9)
    c_starts: np.ndarray
    c_pitches: np.ndarray
    n_ref: int
    matching_cand_indices: list[np.ndarray]
    union_ceiling: float
    base_recall_2x: float
    p1_19_recall_2x: float

    def eval_recall(self, weights: Sequence[float], mult: int = 2) -> float:
        """指定重みでの recall@mult× を計算する。"""
        scores = calculate_scores(self.base_scores, self.features, weights)
        order = np.lexsort((self.c_pitches, self.c_starts, -scores))
        ranks = np.empty(len(scores), dtype=int)
        ranks[order] = np.arange(len(scores))

        budget = mult * self.n_ref
        hits = 0
        for match in self.matching_cand_indices:
            if len(match) > 0 and np.min(ranks[match]) < budget:
                hits += 1
        return hits / self.n_ref if self.n_ref > 0 else 0.0


def optimize_weights_coordinate_descent(
    train_items: Sequence[TrackEvalItem],
    candidate_weights: Sequence[float] = (0.0, 0.5, 1.0, 2.0),
    passes: int = 2,
    target_role: str = "other",
    n_features: int | None = None,
    initial_weights: Sequence[float] | None = None,
) -> np.ndarray:
    """座標降下法で特徴量の重みを 2 周最適化する。

    目的: train_items の target_role における平均 recall@2× の最大化。
    同点の場合は過学習を防ぐため小さい重み（0.0 優先）を選択。
    initial_weights: 探索開始点（指定時はこの重み＋新特徴量0.0から開始し、単調非減少を保証）。
    """
    if not train_items:
        dim = n_features if n_features is not None else 8
        if initial_weights is not None:
            w = np.zeros(dim, dtype=float)
            w[:min(len(initial_weights), dim)] = initial_weights[:dim]
            return w
        return np.zeros(dim, dtype=float)

    n_dim = train_items[0].features.shape[1]
    weights = np.zeros(n_dim, dtype=float)
    if initial_weights is not None:
        weights[:min(len(initial_weights), n_dim)] = initial_weights[:n_dim]

    target_items = [it for it in train_items if it.role == target_role]
    if not target_items:
        target_items = list(train_items)

    def mean_recall(w: np.ndarray) -> float:
        return float(np.mean([it.eval_recall(w, mult=2) for it in target_items]))

    # 探索候補値に必ず 0.0 を含め、昇順ソート
    cand_weights = sorted(set(candidate_weights) | {0.0})

    # 初期スコア（初期値のスコアを下回らないことを保証）
    current_best_score = mean_recall(weights)

    for _pass in range(passes):
        for dim in range(n_dim):
            best_val = weights[dim]
            best_score = current_best_score
            for val in cand_weights:
                if val == weights[dim]:
                    continue
                test_w = weights.copy()
                test_w[dim] = val
                sc = mean_recall(test_w)
                # 厳密に上回る場合のみ更新（同点なら既存の最良値を維持）
                if sc > best_score + 1e-6:
                    best_score = sc
                    best_val = val
            weights[dim] = best_val
            current_best_score = best_score

    return weights

