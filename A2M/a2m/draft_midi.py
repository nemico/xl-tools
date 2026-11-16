"""採譜の結果を、プロジェクトのテンポマップに合わせた下書きMIDIにする（M4 → Reaper）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mido
import numpy as np

from a2m.project_layout import TempoPoint


@dataclass(frozen=True)
class DraftNote:
    start_tick: int
    end_tick: int
    pitch: int
    velocity: int


def point_ticks(points: list[TempoPoint], tpb: int) -> list[int]:
    """テンポ・拍子の変更点の位置（tick）。変更点は拍の上にあるので、整数に丸める。"""
    ticks = [0]
    for a, b in zip(points, points[1:]):
        ticks.append(ticks[-1] + int(round((b.time_s - a.time_s) * a.bpm / 60.0 * tpb)))
    return ticks


def seconds_to_ticks(points: list[TempoPoint], seconds: np.ndarray, tpb: int) -> np.ndarray:
    """プロジェクトの秒を tick にする（小数のまま返す）。"""
    times = np.array([p.time_s for p in points])
    bpms = np.array([p.bpm for p in points], dtype=float)
    base = np.array(point_ticks(points, tpb), dtype=float)
    s = np.asarray(seconds, dtype=float)
    i = np.clip(np.searchsorted(times, s, side="right") - 1, 0, len(points) - 1)
    return base[i] + (s - times[i]) * bpms[i] / 60.0 * tpb


def ticks_to_seconds(points: list[TempoPoint], ticks: np.ndarray, tpb: int) -> np.ndarray:
    """tick をプロジェクトの秒にする（seconds_to_ticks の逆）。"""
    times = np.array([p.time_s for p in points])
    bpms = np.array([p.bpm for p in points], dtype=float)
    base = np.array(point_ticks(points, tpb), dtype=float)
    t = np.asarray(ticks, dtype=float)
    i = np.clip(np.searchsorted(base, t, side="right") - 1, 0, len(points) - 1)
    return times[i] + (t - base[i]) / tpb * 60.0 / bpms[i]


def snap_ticks(ticks: np.ndarray, tpb: int, grids: list[int]) -> np.ndarray:
    """音の始まりをグリッドにそろえる。拍ごとに、候補のグリッド（1拍を何等分するか）のうち合計のずれが小さいものを使う。"""
    ticks = np.asarray(ticks, dtype=float)
    out = np.zeros(len(ticks), dtype=int)
    beat = np.floor(ticks / tpb + 1e-9).astype(int)
    for b in np.unique(beat):
        sel = np.where(beat == b)[0]
        local = ticks[sel] - b * tpb
        best = None
        for div in grids:
            step = tpb / div
            snapped = np.round(local / step) * step
            err = float(np.sum(np.abs(snapped - local)))
            if best is None or err < best[0] - 1e-9:
                best = (err, snapped)
        out[sel] = np.round(b * tpb + best[1]).astype(int)
    return out


def make_mono(notes: list[DraftNote], min_len: int) -> list[DraftNote]:
    """単音のパート用：同じ位置の重なりは高いほうを残し、次の音にかぶる長さは切る。"""
    by_start: dict[int, DraftNote] = {}
    for n in sorted(notes, key=lambda n: (n.start_tick, -n.pitch)):
        by_start.setdefault(n.start_tick, n)
    seq = [by_start[k] for k in sorted(by_start)]
    out = []
    for n, nxt in zip(seq, seq[1:] + [None]):
        end = max(n.end_tick, n.start_tick + min_len)
        if nxt is not None:
            end = min(end, nxt.start_tick)
        out.append(DraftNote(n.start_tick, end, n.pitch, n.velocity))
    return out


def guide_melody(notes: list[DraftNote], unit: int, legato_max_gap: int, min_len: int) -> list[DraftNote]:
    """歌メロの納品の形に直す。

    位置を unit（10tick）の倍数にそろえる。次の音との隙間が legato_max_gap 以下ならつなぐ（レガート）。
    長さは min_len 以上（次の音にはかぶらない）。単音であること（make_mono の後に呼ぶ）。
    """
    seq = sorted(notes, key=lambda n: n.start_tick)
    out: list[DraftNote] = []
    for i, n in enumerate(seq):
        start = int(round(n.start_tick / unit)) * unit
        end = int(round(n.end_tick / unit)) * unit
        nxt = int(round(seq[i + 1].start_tick / unit)) * unit if i + 1 < len(seq) else None
        if nxt is not None and nxt - end <= legato_max_gap:
            end = nxt
        end = max(end, start + min_len)
        if nxt is not None:
            end = min(end, nxt)
        if end > start:
            out.append(DraftNote(start, end, n.pitch, n.velocity))
    return out


def conductor_track(points: list[TempoPoint], tpb: int) -> mido.MidiTrack:
    track = mido.MidiTrack()
    last = 0
    for p, tick in zip(points, point_ticks(points, tpb)):
        delta = tick - last
        if p.numerator is not None:
            track.append(mido.MetaMessage("time_signature", numerator=p.numerator, denominator=4, time=delta))
            delta = 0
        track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(p.bpm), time=delta))
        last = tick
    return track


def note_track(name: str, channel: int, notes: list[DraftNote], off_velocity: int) -> mido.MidiTrack:
    """channel は 1始まり。"""
    events = []
    for n in notes:
        events.append((n.start_tick, 1, mido.Message("note_on", channel=channel - 1, note=n.pitch, velocity=n.velocity)))
        events.append((n.end_tick, 0, mido.Message("note_off", channel=channel - 1, note=n.pitch, velocity=off_velocity)))
    track = mido.MidiTrack([mido.MetaMessage("track_name", name=name, time=0)])
    last = 0
    for tick, _, msg in sorted(events, key=lambda e: (e[0], e[1])):
        track.append(msg.copy(time=tick - last))
        last = tick
    return track


def write_draft(path: Path, points: list[TempoPoint], tracks: list[tuple[str, int, list[DraftNote]]], tpb: int, off_velocity: int) -> None:
    mid = mido.MidiFile(type=1, ticks_per_beat=tpb)
    mid.tracks.append(conductor_track(points, tpb))
    for name, channel, notes in tracks:
        mid.tracks.append(note_track(name, channel, notes, off_velocity))
    mid.save(path)


def to_draft_notes(notes, timeline, d: dict, tpb: int, velocity: int, fixed_len: int | None) -> list[DraftNote]:
    """採譜の結果（音源の秒）を、プロジェクトの tick にしてグリッドにそろえる。曲の頭（M2）より前は捨てる。"""
    if not notes:
        return []
    shift = timeline.item_position_s - timeline.item_offset_s
    start = seconds_to_ticks(timeline.points, np.array([n[0] for n in notes]) + shift, tpb)
    end = seconds_to_ticks(timeline.points, np.array([n[1] for n in notes]) + shift, tpb)
    if d.get("quantize", False):
        s = snap_ticks(start, tpb, d["grids"])
        e_raw = snap_ticks(end, tpb, d["grids"])
    else:  # モデルが出した位置のまま（tick に丸めるだけ）。クオンタイズは人が Reaper で行う
        s = np.round(start).astype(int)
        e_raw = np.round(end).astype(int)
    e = s + fixed_len if fixed_len else np.maximum(e_raw, s + d["min_len_ticks"])
    first = int(round(seconds_to_ticks(timeline.points, np.array([timeline.bar_times_s[0]]), tpb)[0]))
    out = {(int(a), n[2]): DraftNote(int(a), int(b), n[2], velocity) for a, b, n in zip(s, e, notes) if a >= first}
    return sorted(out.values(), key=lambda n: (n.start_tick, n.pitch))
