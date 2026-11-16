"""第6.1b節: 音源に合わせた Reaper テンポマップとマーカー。"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from a2m.intro_dump import measure_start_tick, tick_to_measure_number
from a2m.karaoke_midi import KaraokeMidiData
from a2m.midi_time import second_to_tick, time_signature_at_tick, tick_to_second
from a2m.warp import WarpResult

PT_LINE_RE = re.compile(r"^\s*PT\s+([0-9.+-eE]+)\s+")


@dataclass(frozen=True)
class BarHead:
    measure: int
    tick: int
    audio_s: float


@dataclass(frozen=True)
class TempoMapBuild:
    bar_heads: list[BarHead]
    tempo_pt_lines: list[str]
    marker_lines: list[str]


def _beats_per_measure(ts) -> float:
    return float(ts.numerator * 4 / ts.denominator)


def _reaper_ts_code(ts) -> int:
    return 65536 * int(ts.denominator) + int(ts.numerator)


def compute_bar_head_times(midi: KaraokeMidiData, warp: WarpResult) -> list[BarHead]:
    sigs = midi.time_signatures
    tpb = midi.ticks_per_beat
    end_tick = second_to_tick(midi.duration_s, midi.tempos, tpb)
    last_m = tick_to_measure_number(end_tick, sigs, tpb)
    heads: list[BarHead] = []
    for m in range(1, last_m + 1):
        tick = measure_start_tick(m, sigs, tpb)
        midi_s = tick_to_second(tick, midi.tempos, tpb)
        audio_s = warp.audio_at_midi(midi_s)
        heads.append(BarHead(measure=m, tick=tick, audio_s=float(audio_s)))
    return heads


def build_tempo_map(midi: KaraokeMidiData, warp: WarpResult) -> TempoMapBuild:
    heads = compute_bar_head_times(midi, warp)
    sigs = midi.time_signatures
    tpb = midi.ticks_per_beat
    pt_lines: list[str] = []
    marker_lines: list[str] = []
    prev_sig = time_signature_at_tick(0, sigs)
    for i, h in enumerate(heads):
        ts = time_signature_at_tick(h.tick, sigs)
        if i + 1 < len(heads):
            dt = heads[i + 1].audio_s - h.audio_s
            beats = _beats_per_measure(ts)
            bpm = beats / dt * 60.0 if dt > 1e-9 else float(midi.master_tempo_bpm)
        else:
            bpm = float(midi.master_tempo_bpm)
        bpm = float(np.clip(bpm, 40.0, 210.0))
        if i == 0 or ts.numerator != prev_sig.numerator or ts.denominator != prev_sig.denominator:
            code = _reaper_ts_code(ts)
            pt_lines.append(f"  PT {h.audio_s:.9f} {bpm:.9f} {code} 0 6")
            prev_sig = ts
        else:
            pt_lines.append(f"  PT {h.audio_s:.9f} {bpm:.9f} 0")
        if h.measure >= 2:
            marker_lines.append(
                f'  MARKER {h.measure} {h.audio_s:.9f} "M{h.measure}" 0'
            )
    return TempoMapBuild(bar_heads=heads, tempo_pt_lines=pt_lines, marker_lines=marker_lines)


def tempo_envelope_block(pt_lines: list[str]) -> list[str]:
    if not pt_lines:
        return []
    return [
        "  <TEMPOENVEX",
        "    EGUID {00000000-0000-0000-0000-000000000001}",
        "    ACT 0 -1",
        "    VIS 1 0 1",
        "    LANEHEIGHT 0 0",
        "    ARM 0",
        "    DEFSHAPE 1 -1 -1",
        *pt_lines,
        "  >",
    ]


def parse_tempo_pt_times(rpp_text: str) -> list[float]:
    times: list[float] = []
    in_tempo = False
    for line in rpp_text.splitlines():
        if line.strip().startswith("<TEMPOENVEX"):
            in_tempo = True
            continue
        if in_tempo and line.strip() == ">":
            break
        if not in_tempo:
            continue
        m = PT_LINE_RE.match(line)
        if m:
            times.append(float(m.group(1)))
    return times


def verify_tempo_map_pt(
    rpp_path,
    expected_heads: list[BarHead],
    *,
    tol_ms: float = 1.0,
) -> bool:
    from pathlib import Path

    text = Path(rpp_path).read_text(encoding="utf-8")
    pts = parse_tempo_pt_times(text)
    exp = [h.audio_s for h in expected_heads]
    if len(pts) != len(exp):
        return False
    tol = tol_ms / 1000.0
    return all(abs(a - b) <= tol for a, b in zip(pts, exp, strict=True))
