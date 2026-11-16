"""MIDI tick と秒の変換（テンポマップ使用）。"""

from __future__ import annotations

from dataclasses import dataclass

import mido


@dataclass(frozen=True)
class TempoEvent:
    tick: int
    tempo: int  # microseconds per beat


@dataclass(frozen=True)
class TimeSignatureEvent:
    tick: int
    numerator: int
    denominator: int


def merge_tempo_events(tracks: list[mido.MidiTrack]) -> list[TempoEvent]:
    events: list[TempoEvent] = []
    for track in tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "set_tempo":
                events.append(TempoEvent(tick=tick, tempo=msg.tempo))
    events.sort(key=lambda e: e.tick)
    if not events:
        events = [TempoEvent(tick=0, tempo=mido.bpm2tempo(120))]
    deduped: list[TempoEvent] = []
    for ev in events:
        if deduped and deduped[-1].tick == ev.tick:
            deduped[-1] = ev
        else:
            deduped.append(ev)
    return deduped


def merge_time_signature_events(tracks: list[mido.MidiTrack]) -> list[TimeSignatureEvent]:
    events: list[TimeSignatureEvent] = []
    for track in tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "time_signature":
                events.append(
                    TimeSignatureEvent(
                        tick=tick,
                        numerator=msg.numerator,
                        denominator=msg.denominator,
                    )
                )
    events.sort(key=lambda e: e.tick)
    if not events:
        events = [TimeSignatureEvent(tick=0, numerator=4, denominator=4)]
    deduped: list[TimeSignatureEvent] = []
    for ev in events:
        if deduped and deduped[-1].tick == ev.tick:
            deduped[-1] = ev
        else:
            deduped.append(ev)
    return deduped


def tick_to_second(tick: int, tempos: list[TempoEvent], ticks_per_beat: int) -> float:
    if tick <= 0:
        return 0.0
    total = 0.0
    prev_tick = 0
    for i, ev in enumerate(tempos):
        next_tick = tempos[i + 1].tick if i + 1 < len(tempos) else tick
        segment_end = min(tick, next_tick)
        if segment_end > prev_tick:
            total += mido.tick2second(
                segment_end - prev_tick, ticks_per_beat, ev.tempo
            )
            prev_tick = segment_end
        if prev_tick >= tick:
            break
    return total


def second_to_tick(second: float, tempos: list[TempoEvent], ticks_per_beat: int) -> int:
    if second <= 0.0:
        return 0
    last_tempo = tempos[-1].tempo
    # テンポイベントが tick=0 のみでも、曲全体の tick を探索できる上限にする
    hi = int(mido.second2tick(second + 1.0, ticks_per_beat, last_tempo)) + 1
    hi = max(hi, tempos[-1].tick + 1)
    lo = 0
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if tick_to_second(mid, tempos, ticks_per_beat) <= second:
            lo = mid
        else:
            hi = mid
    if tick_to_second(hi, tempos, ticks_per_beat) <= second:
        return hi
    return lo


def tempo_to_bpm(tempo: int) -> float:
    return mido.tempo2bpm(tempo)


def measure_length_ticks(ts: TimeSignatureEvent, ticks_per_beat: int) -> int:
    return ts.numerator * ticks_per_beat * 4 // ts.denominator


def bar_head_ticks(
    signatures: list[TimeSignatureEvent], ticks_per_beat: int, end_tick: int
) -> list[tuple[int, float]]:
    """拍子の変更を踏まえた小節頭の一覧。(tick, その小節の4分音符の数) を end_tick の手前まで返す。"""
    bars: list[tuple[int, float]] = []
    tick = 0
    while tick < end_tick:
        length = measure_length_ticks(time_signature_at_tick(tick, signatures), ticks_per_beat)
        bars.append((tick, length / ticks_per_beat))
        tick += length
    return bars


def time_signature_at_tick(tick: int, signatures: list[TimeSignatureEvent]) -> TimeSignatureEvent:
    current = signatures[0]
    for sig in signatures:
        if sig.tick > tick:
            break
        current = sig
    return current


def m2_start_tick(signatures: list[TimeSignatureEvent], ticks_per_beat: int) -> int:
    """M1 終了 = M2 開始 tick（1 小節目がセットアップ）。"""
    sig0 = signatures[0]
    return measure_length_ticks(sig0, ticks_per_beat)


def format_time_signature(sig: TimeSignatureEvent) -> str:
    return f"{sig.numerator}/{sig.denominator}"


def second_to_rpp_tick(second: float, tempo_bpm: float, *, ppq: int = 960) -> int:
    """Reaper RPP 埋め込み MIDI 用 tick（マスターテンポ一定）。"""
    if second <= 0.0:
        return 0
    return int(round(second * tempo_bpm / 60.0 * ppq))
