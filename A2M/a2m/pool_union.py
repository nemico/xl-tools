"""公式候補プールの union recall。

P1-28d の測り方（曲ごとの recall の平均、BASE_METHODS、音高一致 ±50ms、
ドラムはクラス積み）に固定する。分離モデル比較の「現状」行はここから出す。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

from a2m.align import AlignConfig
from a2m.countin import CountinConfig
from a2m.evaluate import (
    EvalConfig,
    EvalNote,
    evaluate_drums,
    eval_window_bounds,
    filter_notes_excluding_intervals,
    ref_notes_for_eval_role,
)
from a2m.p1_export import audio_duration_seconds, method_notes_from_json
from a2m.p1_ref_notes import load_ref_eval_notes


def _matched_ref_count(ref_notes: list[EvalNote], est_notes: list[EvalNote], tol: float) -> int:
    """同音高・±tol の1対1照合で当たった正解数。"""
    if not ref_notes or not est_notes:
        return 0
    est_by_pitch: dict[int, list[float]] = defaultdict(list)
    for n in est_notes:
        est_by_pitch[n.pitch].append(n.start_s)
    matched = 0
    for pitch, group in _group_pitch(ref_notes).items():
        est_t = est_by_pitch.get(pitch)
        if not est_t:
            continue
        ref_t = np.array([n.start_s for n in group], dtype=float)
        est_arr = np.array(est_t, dtype=float)
        order = np.argsort(est_arr)
        est_sorted = est_arr[order]
        pairs: list[tuple[float, int, int]] = []
        for i, rt in enumerate(ref_t):
            lo = int(np.searchsorted(est_sorted, float(rt) - tol))
            hi = int(np.searchsorted(est_sorted, float(rt) + tol, side="right"))
            for k in range(lo, hi):
                pairs.append((abs(float(rt) - float(est_sorted[k])), i, int(order[k])))
        pairs.sort()
        used_r: set[int] = set()
        used_e: set[int] = set()
        for _d, i, j in pairs:
            if i in used_r or j in used_e:
                continue
            used_r.add(i)
            used_e.add(j)
            matched += 1
    return matched


def _group_pitch(notes: list[EvalNote]) -> dict[int, list[EvalNote]]:
    grouped: dict[int, list[EvalNote]] = defaultdict(list)
    for n in notes:
        grouped[n.pitch].append(n)
    return grouped


def matched_ref_indices(ref_notes: list[EvalNote], est_notes: list[EvalNote], tol: float) -> set[int]:
    """同音高・±tol の1対1照合で当たった正解の位置。数え方は _matched_ref_count と同じ。"""
    if not ref_notes or not est_notes:
        return set()
    est_by_pitch: dict[int, list[float]] = defaultdict(list)
    for note in est_notes:
        est_by_pitch[note.pitch].append(note.start_s)
    grouped: dict[int, list[tuple[int, EvalNote]]] = defaultdict(list)
    for index, note in enumerate(ref_notes):
        grouped[note.pitch].append((index, note))
    hit: set[int] = set()
    for pitch, group in grouped.items():
        est_t = est_by_pitch.get(pitch)
        if not est_t:
            continue
        ref_t = np.array([note.start_s for _index, note in group], dtype=float)
        est_arr = np.array(est_t, dtype=float)
        order = np.argsort(est_arr)
        est_sorted = est_arr[order]
        pairs: list[tuple[float, int, int]] = []
        for local_i, start_s in enumerate(ref_t):
            lo = int(np.searchsorted(est_sorted, float(start_s) - tol))
            hi = int(np.searchsorted(est_sorted, float(start_s) + tol, side="right"))
            for k in range(lo, hi):
                pairs.append((abs(float(start_s) - float(est_sorted[k])), local_i, int(order[k])))
        pairs.sort()
        used_r: set[int] = set()
        used_e: set[int] = set()
        for _dist, local_i, est_i in pairs:
            if local_i in used_r or est_i in used_e:
                continue
            used_r.add(local_i)
            used_e.add(est_i)
            hit.add(group[local_i][0])
    return hit


def pitched_union(ref_notes: list[EvalNote], est_notes: list[EvalNote], tol: float) -> float:
    if not ref_notes:
        return 0.0
    return _matched_ref_count(ref_notes, est_notes, tol) / len(ref_notes)

# P1-28d 公式プール。M18/M20 は不採用のまま入れない。
BASE_METHODS = [
    "M1", "M2", "M3", "M4", "M6", "M8", "M10", "M11", "M12",
    "M13", "M14", "M15", "M16", "M17", "M19",
]

# 小数点3桁の公式値（results/p1/rebaseline_results.json の union_ceilings）
OFFICIAL_UNION_3DP = {
    "other": 0.803,
    "bass": 0.765,
    "melody": 0.793,
    "drums": 0.637,
}


def load_p1_config() -> dict:
    with Path("config.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)["p1"]


def song_names(karaoke_dir: Path | None = None) -> list[str]:
    root = karaoke_dir or Path("data/karaoke")
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _window(song: str, cfg: dict) -> tuple[list[EvalNote], list[EvalNote]] | None:
    song_dir = Path("data/karaoke") / song
    song_out = Path("results/p1") / song
    align_p = song_out / "align.json"
    if not align_p.is_file():
        return None
    align = json.loads(align_p.read_text(encoding="utf-8"))
    audio = song_out / "audio.wav"
    if not audio.is_file():
        audio = song_dir / "audio.mp3"
    dur = audio_duration_seconds(audio)
    align_cfg = AlignConfig.from_dict(cfg)
    countin_cfg = CountinConfig.from_dict(cfg)
    ref_flat = load_ref_eval_notes(
        song_dir, song_out,
        align=align, countin_cfg=countin_cfg, align_cfg=align_cfg,
        latency_s=float(cfg.get("latency_s", 0.0)), sr=int(cfg["sr"]),
    )
    bounds = eval_window_bounds(ref_flat, dur)
    if bounds is None:
        return None
    t0, t1 = bounds
    exclude_iv = list(align.get("content_mismatch_intervals") or [])
    ref_win = [n for n in ref_flat if t0 <= n.start_s <= t1]
    ref_win = filter_notes_excluding_intervals(ref_win, exclude_iv, dur)
    return ref_win, exclude_iv  # type: ignore[return-value]


def load_method_notes(song: str, method: str, t0: float | None = None, t1: float | None = None) -> list[EvalNote]:
    path = Path("results/p1") / song / "raw" / f"{method}.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    notes = method_notes_from_json(data.get("notes") or [])
    if t0 is None:
        return notes
    return [n for n in notes if t0 <= n.start_s <= t1]


def union_by_song(
    cfg: dict,
    extra_methods: list[str] | None = None,
) -> dict[str, list[float]]:
    """曲ごとの union recall。extra_methods の raw JSON を公式プールに足す。"""
    eval_cfg = EvalConfig.from_dict(cfg["eval"])
    extras = extra_methods or []
    out: dict[str, list[float]] = {"other": [], "bass": [], "melody": [], "drums": []}
    for song in song_names():
        song_out = Path("results/p1") / song
        align_p = song_out / "align.json"
        if not align_p.is_file():
            continue
        align = json.loads(align_p.read_text(encoding="utf-8"))
        audio = song_out / "audio.wav"
        if not audio.is_file():
            audio = Path("data/karaoke") / song / "audio.mp3"
        dur = audio_duration_seconds(audio)
        align_cfg = AlignConfig.from_dict(cfg)
        countin_cfg = CountinConfig.from_dict(cfg)
        ref_flat = load_ref_eval_notes(
            Path("data/karaoke") / song, song_out,
            align=align, countin_cfg=countin_cfg, align_cfg=align_cfg,
            latency_s=float(cfg.get("latency_s", 0.0)), sr=int(cfg["sr"]),
        )
        bounds = eval_window_bounds(ref_flat, dur)
        if bounds is None:
            continue
        t0, t1 = bounds
        exclude_iv = list(align.get("content_mismatch_intervals") or [])
        ref_win = filter_notes_excluding_intervals(
            [n for n in ref_flat if t0 <= n.start_s <= t1], exclude_iv, dur,
        )
        pool: list[EvalNote] = []
        for method in list(BASE_METHODS) + extras:
            for n in load_method_notes(song, method):
                if t0 <= n.start_s <= t1:
                    pool.append(n)
        pool = filter_notes_excluding_intervals(pool, exclude_iv, dur)
        for role in ("other", "bass", "melody"):
            ref_r = ref_notes_for_eval_role(ref_win, role)
            est_r = [n for n in pool if n.role == role]
            out[role].append(pitched_union(ref_r, est_r, eval_cfg.onset_tol_s))
        ref_d = [n for n in ref_win if n.role == "drums"]
        est_d = [n for n in pool if n.role == "drums"]
        res_d = evaluate_drums(ref_d, est_d, eval_cfg)
        d_hits = sum(float(row["r"]) * int(row["ref_stacks"]) for row in res_d["per_class_detail"].values())
        d_ref = sum(int(row["ref_stacks"]) for row in res_d["per_class_detail"].values())
        out["drums"].append(d_hits / d_ref if d_ref else 0.0)
    return out


def mean_union(by_song: dict[str, list[float]]) -> dict[str, float]:
    return {role: float(np.mean(vals)) if vals else 0.0 for role, vals in by_song.items()}


def official_union(cfg: dict | None = None) -> dict[str, float]:
    cfg = cfg or load_p1_config()
    return mean_union(union_by_song(cfg))


def assert_role_not_constant(rows: list[dict[str, float]], roles: list[str]) -> None:
    """5行以上で、ある役割の値が全行一致なら測定失敗。"""
    if len(rows) < 5:
        return
    for role in roles:
        vals = [round(float(row[role]), 6) for row in rows]
        if len(set(vals)) == 1:
            raise RuntimeError(
                f"測定失敗: 役割 {role} が {len(rows)} 行すべて {vals[0]} で一致している"
            )


def single_model_does_not_exceed_pool(
    pool: dict[str, float],
    alone: dict[str, float],
    roles: list[str],
) -> None:
    for role in roles:
        if alone[role] > pool[role] + 1e-9:
            raise AssertionError(
                f"{role}: 単体 union {alone[role]:.4f} がプール {pool[role]:.4f} を超えている"
            )
