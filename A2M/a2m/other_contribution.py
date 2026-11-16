"""その他の union から、方式を1つ抜いたときの下がり。"""

from __future__ import annotations

import numpy as np

from a2m.evaluate import EvalNote
from a2m.pool_union import matched_ref_indices, pitched_union


def exclusive_counts(hits_by_method: dict[str, set[int]]) -> dict[str, int]:
    """その方式だけが当てた正解の数。"""
    owners: dict[int, int] = {}
    for hits in hits_by_method.values():
        for index in hits:
            owners[index] = owners.get(index, 0) + 1
    return {
        method: sum(1 for index in hits if owners[index] == 1)
        for method, hits in hits_by_method.items()
    }


def should_cut_method(drop: float, minimum_drop: float) -> bool:
    """下がりが minimum_drop 未満なら切る。同じ値は残す。"""
    return drop < minimum_drop


def other_method_rows(
    ref_by_song: list[list[EvalNote]],
    est_by_method: dict[str, list[list[EvalNote]]],
    *,
    tol_s: float,
) -> list[dict[str, float | int | str]]:
    """曲ごとの recall の平均。抜いたときの下がりと、独占して当てた音数。"""
    methods = list(est_by_method)
    full_scores: list[float] = []
    without: dict[str, list[float]] = {method: [] for method in methods}
    exclusive = {method: 0 for method in methods}
    for song_i, ref in enumerate(ref_by_song):
        per_method: dict[str, list[EvalNote]] = {}
        hits: dict[str, set[int]] = {}
        pooled: list[EvalNote] = []
        for method in methods:
            notes = [note for note in est_by_method[method][song_i] if note.role == "other"]
            per_method[method] = notes
            pooled.extend(notes)
            hits[method] = matched_ref_indices(ref, notes, tol_s)
        full_scores.append(pitched_union(ref, pooled, tol_s))
        for method, count in exclusive_counts(hits).items():
            exclusive[method] += count
        for method in methods:
            others = [
                note
                for name, notes in per_method.items()
                if name != method
                for note in notes
            ]
            without[method].append(pitched_union(ref, others, tol_s))
    full = float(np.mean(full_scores)) if full_scores else 0.0
    rows = []
    for method in methods:
        left = float(np.mean(without[method])) if without[method] else 0.0
        rows.append({
            "method": method,
            "exclusive": exclusive[method],
            "drop": full - left,
            "full": full,
        })
    return rows
