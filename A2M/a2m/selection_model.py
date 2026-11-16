"""LightGBM による役割別選別モデルの学習・予測・下書き評価モジュール。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import lightgbm as lgb
import numpy as np

from a2m.edit_cost_eval import (
    calculate_overall_reduction_rate,
    calculate_reduction_rate,
    compute_track_edit_cost,
    resolve_monophonic_overlaps,
)
from a2m.evaluate import EvalNote
from a2m.selection_data import ALL_METHODS

ROLES = ("melody", "bass", "drums", "other")

FEATURE_COLUMNS = [
    "pitch",
    "duration",
    "n_methods",
    "w1_explained",
    "w2_pitch_range",
    "w3_pitch_classes",
    "w4_duration",
    "w5_used_grid",
    "w6_polyphony",
    "w7_onset_strength",
    "w8_grid_distance",
    "w9_panning",
    "track_polyphonic",
    "track_pitch_min",
    "track_pitch_max",
    "track_used_grids_count",
    "bpm",
    "time_sig_num",
    "time_sig_den",
    "song_duration_s",
] + [f"method_{m}" for m in ALL_METHODS]


def prepare_feature_array(rows: Sequence[dict[str, Any]]) -> np.ndarray:
    """行データのリストから LightGBM 用の 2D numpy 配列を作成する。"""
    if not rows:
        return np.empty((0, len(FEATURE_COLUMNS)), dtype=np.float32)
    matrix = np.zeros((len(rows), len(FEATURE_COLUMNS)), dtype=np.float32)
    for i, r in enumerate(rows):
        for j, col in enumerate(FEATURE_COLUMNS):
            matrix[i, j] = float(r.get(col, 0.0))
    return matrix


@dataclass
class RoleModel:
    role: str
    model: lgb.LGBMClassifier
    feature_names: list[str]

    def predict_proba(self, rows: Sequence[dict[str, Any]]) -> np.ndarray:
        """各候補が正解に対応する確率 (0.0〜1.0) を返す。"""
        if not rows:
            return np.empty(0, dtype=np.float32)
        X = prepare_feature_array(rows)
        # binary classification: column 1 is probability of class 1
        probs = self.model.predict_proba(X)[:, 1]
        return probs.astype(np.float32)

    def feature_importances(self) -> dict[str, float]:
        """各特徴量の重要度（ゲイン）の辞書を返す。"""
        importances = self.model.booster_.feature_importance(importance_type="gain")
        total = float(np.sum(importances))
        if total <= 0:
            total = 1.0
        return {
            name: float(imp) / total for name, imp in zip(self.feature_names, importances)
        }


def train_role_model(
    role: str,
    train_rows: Sequence[dict[str, Any]],
    valid_rows: Sequence[dict[str, Any]],
    n_estimators: int = 200,
    learning_rate: float = 0.05,
    random_state: int = 21,
) -> RoleModel:
    """指定された役割の候補データで LightGBM モデルを学習する。

    検証用データで改善が止まったら早期終了する。
    """
    X_train = prepare_feature_array(train_rows)
    y_train = np.array([int(r.get("label", 0)) for r in train_rows], dtype=int)

    X_val = prepare_feature_array(valid_rows)
    y_val = np.array([int(r.get("label", 0)) for r in valid_rows], dtype=int)

    # 陽性例・陰性例が存在しない場合のガード
    if len(np.unique(y_train)) < 2:
        clf = lgb.LGBMClassifier(n_estimators=1, random_state=random_state, verbose=-1)
        clf.fit(X_train, y_train)
        return RoleModel(role=role, model=clf, feature_names=list(FEATURE_COLUMNS))

    clf = lgb.LGBMClassifier(
        objective="binary",
        n_estimators=n_estimators,
        learning_rate=learning_rate,
        num_leaves=31,
        random_state=random_state,
        verbose=-1,
    )

    eval_set = [(X_val, y_val)] if len(y_val) > 0 and len(np.unique(y_val)) >= 2 else None
    callbacks = (
        [lgb.early_stopping(stopping_rounds=20, verbose=False)]
        if eval_set is not None
        else None
    )

    clf.fit(
        X_train,
        y_train,
        eval_set=eval_set,
        callbacks=callbacks,
    )

    return RoleModel(role=role, model=clf, feature_names=list(FEATURE_COLUMNS))


def rank_and_pick_track_draft(
    role: str,
    candidates: Sequence[tuple[EvalNote, dict[str, Any]]],
    model: RoleModel,
    n_ref: int,
    is_monophonic: bool = False,
) -> list[EvalNote]:
    """1つのトラックに対し、モデルの予測確率の高い順に n_ref 個のノートを採る。

    単音トラックの場合は重なりを1つに絞る。
    """
    if n_ref <= 0 or not candidates:
        return []

    eval_notes = [c[0] for c in candidates]
    feature_rows = [c[1] for c in candidates]
    probs = model.predict_proba(feature_rows)

    cands_with_score = list(zip(eval_notes, probs))

    if is_monophonic:
        return resolve_monophonic_overlaps(cands_with_score, target_count=n_ref)

    # 和音トラック（ドラムやその他ポリフォニック）: スコア降順、開始時刻昇順で上位 n_ref 件
    cands_with_score.sort(key=lambda x: (-x[1], x[0].start_s))
    selected = [note for note, _s in cands_with_score[:n_ref]]
    selected.sort(key=lambda n: n.start_s)
    return selected
