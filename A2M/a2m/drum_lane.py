"""ドラムのクラス単位打点統合（ドラムレーン統合）および方式信頼度スコアリング。

CARD_P1-32:
- ドラムは「位置」と「音色」に分離し、同一クラス内で 20ms 以内の重ね打点を1打点にまとめる。
- 納品側・下書き側ともにクラス単位で打点統合した上で当たり判定を行う。
- 方式ごとの実測 precision に基づく信頼度重み付けスコア（A: 票数, B: precision和, C: log-odds和）を計算。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from a2m.evaluate import EvalNote


@dataclass(frozen=True)
class DrumLaneHit:
    role: str
    drum_class: str
    start_s: float
    end_s: float
    stacked_n: int = 1
    score: float = 0.0

    def to_eval_note(self, pitch: int = 0) -> EvalNote:
        return EvalNote(
            role=self.role,
            pitch=pitch,
            start_s=self.start_s,
            end_s=self.end_s,
            drum_class=self.drum_class,
        )


def stack_drum_lane_hits(
    notes: Sequence[EvalNote | dict[str, Any]],
    window_s: float = 0.020,
) -> list[DrumLaneHit]:
    """同クラスで開始が window_s 以内のドラム音を1打点にまとめる（開始は最も早く、スコアは最大値）。"""
    by_cls: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in notes:
        if isinstance(n, EvalNote):
            cls = n.drum_class or "other"
            start_s = float(n.start_s)
            end_s = float(n.end_s)
            score = float(getattr(n, "score", 0.0))
        else:
            cls = str(n.get("drum_class") or "other")
            start_s = float(n.get("start_s", 0.0))
            end_s = float(n.get("end_s", start_s + 0.1))
            score = float(n.get("score", 0.0))
        by_cls[cls].append({
            "start_s": start_s,
            "end_s": end_s,
            "score": score,
        })

    hits: list[DrumLaneHit] = []
    for cls, n_list in by_cls.items():
        n_list.sort(key=lambda x: x["start_s"])
        i = 0
        while i < len(n_list):
            grp_start = n_list[i]["start_s"]
            grp_end = n_list[i]["end_s"]
            max_score = n_list[i]["score"]
            j = i + 1
            while j < len(n_list) and n_list[j]["start_s"] - grp_start <= window_s:
                if n_list[j]["score"] > max_score:
                    max_score = n_list[j]["score"]
                if n_list[j]["end_s"] > grp_end:
                    grp_end = n_list[j]["end_s"]
                j += 1
            hits.append(
                DrumLaneHit(
                    role="drums",
                    drum_class=cls,
                    start_s=grp_start,
                    end_s=grp_end,
                    stacked_n=j - i,
                    score=max_score,
                )
            )
            i = j

    hits.sort(key=lambda x: x.start_s)
    return hits


def compute_drum_overlap_counts(
    notes: Sequence[EvalNote | dict[str, Any]],
    window_s: float = 0.020,
) -> dict[str, dict[str, int | float]]:
    """クラスごとの単独打点数、重ね打点数、重ね率を算出する。"""
    hits = stack_drum_lane_hits(notes, window_s=window_s)
    single_counts: dict[str, int] = defaultdict(int)
    stacked_counts: dict[str, int] = defaultdict(int)

    for h in hits:
        if h.stacked_n == 1:
            single_counts[h.drum_class] += 1
        else:
            stacked_counts[h.drum_class] += 1

    all_classes = sorted(set(single_counts.keys()) | set(stacked_counts.keys()))
    res: dict[str, dict[str, int | float]] = {}
    for cls in all_classes:
        s = single_counts[cls]
        st = stacked_counts[cls]
        tot = s + st
        ratio = st / tot if tot > 0 else 0.0
        res[cls] = {
            "single": s,
            "stacked": st,
            "total": tot,
            "ratio": ratio,
        }
    return res


def calculate_reliability_score(
    methods: Sequence[str],
    role: str,
    prec_table: dict[str, dict[str, float]],
    mode: str = "B",
) -> float:
    """方式集合と役割に対する信頼度スコアを計算。

    Args:
        methods: 出力した方式名のシーケンス (例: ['M1', 'M2'])
        role: 役割 ('other', 'bass', 'melody', 'drums')
        prec_table: 方式×役割の実測 precision テーブル
        mode: 'A' (票数), 'B' (precision和), 'C' (log-odds和)
    """
    if mode == "A":
        return float(len(methods))
    elif mode == "B":
        return float(sum(prec_table.get(m, {}).get(role, 0.0) for m in methods))
    elif mode == "C":
        s = 0.0
        for m in methods:
            p = prec_table.get(m, {}).get(role, 0.0)
            p_clip = np.clip(p, 1e-4, 1.0 - 1e-4)
            s += float(np.log(p_clip / (1.0 - p_clip)))
        return float(s)
    else:
        raise ValueError(f"Unknown mode: {mode}")
