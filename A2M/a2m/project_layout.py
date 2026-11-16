"""グリッド（音源の秒）を、納品の形のプロジェクトの時間軸に置く（M1）。

- M1 はセットアップ小節（4/4、Master Tempo ＝ 一番長く演奏されるBPM）。
- 曲は M2 から。音が鳴り始める位置が小節頭でなければ、M2 を短い小節（1/4〜）にして吸収する。
- テンポと拍子の変更点を、プロジェクトの秒で返す。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from a2m.beat_grid import BeatGrid


@dataclass(frozen=True)
class TempoPoint:
    time_s: float
    bpm: int
    numerator: int | None  # 拍子（n/4）が変わる所だけ入る


@dataclass(frozen=True)
class ProjectTimeline:
    master_bpm: int
    item_position_s: float  # 音源アイテムを置く位置
    item_offset_s: float  # 音源の頭を捨てる長さ（無音が M1 より長いとき）
    points: list[TempoPoint]
    bar_times_s: np.ndarray  # M2 以降の小節頭（プロジェクトの秒）


def first_sound_s(mono: np.ndarray, sr: int, threshold_db: float, frame_s: float) -> float:
    """音が鳴り始める時刻。最大の音量から threshold_db 下がった所を初めて超える位置。"""
    hop = max(1, int(sr * frame_s))
    n = len(mono) // hop
    if n == 0:
        return 0.0
    rms = np.sqrt(np.mean(mono[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    level = 20 * np.log10(rms / (rms.max() + 1e-12))
    idx = np.where(level >= threshold_db)[0]
    return float(idx[0] * hop / sr) if len(idx) else 0.0


def _interval_bpm(n_points: int, segments) -> np.ndarray:
    bpm = np.zeros(max(n_points - 1, 1), dtype=int)
    for k, seg in enumerate(segments):
        end = segments[k + 1].bar_index if k + 1 < len(segments) else len(bpm)
        bpm[seg.bar_index : end] = seg.bpm
    return bpm


def build_timeline(grid: BeatGrid, sound_start_s: float, eps_s: float, lead_bars: int = 0) -> ProjectTimeline:
    """lead_bars：セットアップ小節（M1）の後ろに足す空きの小節（4/4、Master Tempo）。打ち込み中に音色やドラムキットを
    入れ替える余裕のため。納品のときは詰める（M1 の次が曲の頭）。"""
    g = np.asarray(grid.grid_times, dtype=float)
    bpb = grid.beats_per_bar
    bar_idx = np.asarray(grid.bar_idx, dtype=int)
    bpm = _interval_bpm(len(g), grid.segments)
    # 音の鳴り始めより前までグリッドを伸ばす
    period0 = 60.0 / bpm[0]
    n_ext = max(0, int(math.ceil((g[0] - sound_start_s) / period0)) + bpb)
    if n_ext:
        g = np.concatenate([g[0] - period0 * np.arange(n_ext, 0, -1), g])
        bpm = np.concatenate([np.full(n_ext, bpm[0]), bpm])
        bar_idx = bar_idx + n_ext
    heads = list(bar_idx)
    while heads[0] - bpb >= 0:
        heads.insert(0, heads[0] - bpb)
    heads_t = g[heads]
    j = int(np.searchsorted(heads_t, sound_start_s - eps_s))
    j = min(j, len(heads) - 1)
    gap = heads_t[j] - sound_start_s
    pickup = 0 if gap <= eps_s else min(bpb, max(1, int(math.ceil((gap - eps_s) / period0 - 1e-6))))
    m2_idx = heads[j] - pickup
    # 小節の長さ（拍）。半端に長い小節は、普通の小節＋残りに分ける
    lengths: list[int] = [pickup] if pickup else []
    starts: list[int] = [m2_idx] if pickup else []
    for a, b in zip(heads[j:], heads[j + 1 :] + [heads[-1] + bpb]):
        pos, left = a, b - a
        while left > bpb:
            starts.append(pos)
            lengths.append(bpb)
            pos, left = pos + bpb, left - bpb
        starts.append(pos)
        lengths.append(left)
    dur: dict[int, float] = {}
    for n in range(m2_idx, len(bpm)):
        dur[int(bpm[n])] = dur.get(int(bpm[n]), 0.0) + 60.0 / bpm[n]
    master = max(dur, key=lambda b: (dur[b], -b))
    m1_len = (1 + lead_bars) * 4 * 60.0 / master
    shift = m1_len - g[m2_idx]  # プロジェクトの秒 = 音源の秒 + shift
    points = [TempoPoint(0.0, master, 4)]
    numer_at = {s: l for s, l in zip(starts, lengths)}
    cur_bpm, cur_num = None, None
    for n in range(m2_idx, len(g) - 1):
        num = numer_at.get(n)
        new_num = num if num is not None and num != cur_num else None
        new_bpm = int(bpm[n]) if int(bpm[n]) != cur_bpm else None
        if n == m2_idx:
            new_num, new_bpm = numer_at[n], int(bpm[n])
        if new_num is not None or new_bpm is not None:
            points.append(TempoPoint(float(g[n] + shift), int(bpm[n]), new_num))
        cur_bpm = int(bpm[n])
        if num is not None:
            cur_num = num
    bars = np.asarray([g[s] + shift for s in starts if s < len(g)])
    return ProjectTimeline(
        master_bpm=int(master),
        item_position_s=max(shift, 0.0),
        item_offset_s=max(-shift, 0.0),
        points=points,
        bar_times_s=bars,
    )


def timeline_to_dict(tl: ProjectTimeline) -> dict:
    return {
        "master_bpm": int(tl.master_bpm),
        "item_position_s": float(tl.item_position_s),
        "item_offset_s": float(tl.item_offset_s),
        "points": [[float(p.time_s), int(p.bpm), None if p.numerator is None else int(p.numerator)] for p in tl.points],
        "bar_times_s": [float(x) for x in tl.bar_times_s],
    }


def timeline_from_dict(d: dict) -> ProjectTimeline:
    return ProjectTimeline(
        master_bpm=int(d["master_bpm"]),
        item_position_s=float(d["item_position_s"]),
        item_offset_s=float(d["item_offset_s"]),
        points=[TempoPoint(float(t), int(b), None if n is None else int(n)) for t, b, n in d["points"]],
        bar_times_s=np.asarray(d["bar_times_s"], dtype=float),
    )
