"""PhoenixMAX render の遅れ測定（仕様 第3.1節）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mido
import numpy as np
import soundfile as sf

TICKS_PER_BEAT = 480
TEMPO_BPM = 120.0
LATENCY_MIDI_NAME = "latency_test.mid"
LATENCY_WAV_NAME = "latency_test.wav"

# MIDI 時刻（秒）。ドラム 37 / ピアノ C4（8分音符）。
LATENCY_DRUM_TIMES_S = (2.0, 4.0, 6.0, 8.0)
LATENCY_PIANO_OFFSET_S = 0.5
ENVELOPE_FRACTION = 0.10
SEARCH_HALF_WINDOW_S = 0.15


@dataclass(frozen=True)
class RenderToolConfig:
    mid_dir: Path
    wav_dir: Path
    latency_s: float | None

    @classmethod
    def from_dict(cls, p1: dict[str, Any]) -> RenderToolConfig:
        rt = p1.get("render_tool", {})
        lat = rt.get("latency_s")
        return cls(
            mid_dir=Path(rt.get("mid_dir", r"C:\PhoenixMAX\mid")),
            wav_dir=Path(rt.get("wav_dir", r"C:\PhoenixMAX\wav")),
            latency_s=None if lat is None else float(lat),
        )


@dataclass(frozen=True)
class LatencyEvent:
    kind: str
    midi_s: float
    channel: int
    pitch: int


@dataclass(frozen=True)
class LatencyMeasurement:
    kind: str
    midi_s: float
    onset_s: float
    delay_s: float


def latency_test_events() -> list[LatencyEvent]:
    events: list[LatencyEvent] = []
    for t in LATENCY_DRUM_TIMES_S:
        events.append(LatencyEvent("drums", t, 9, 37))
        events.append(
            LatencyEvent("piano", t + LATENCY_PIANO_OFFSET_S, 0, 60)
        )
    return events


def _tempo_micro() -> int:
    return mido.bpm2tempo(TEMPO_BPM)


def second_to_tick(t_s: float) -> int:
    return int(round(t_s * TICKS_PER_BEAT * TEMPO_BPM / 60.0))


def write_latency_test_midi(path: Path | str) -> None:
    """試験用 MIDI（GS リセット + リム / ピアノ）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mid = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    tempo = _tempo_micro()

    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    meta.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    # GS Reset (General MIDI)
    meta.append(
        mido.Message(
            "sysex",
            data=[0x41, 0x10, 0x42, 0x12, 0x00, 0x7F, 0x00, 0x01],
            time=0,
        )
    )
    mid.tracks.append(meta)

    eighth = TICKS_PER_BEAT // 2

    def note_events(abs_tick: int, ch: int, pitch: int, dur: int) -> list[tuple[int, mido.Message]]:
        return [
            (abs_tick, mido.Message("note_on", channel=ch, note=pitch, velocity=100, time=0)),
            (
                abs_tick + dur,
                mido.Message("note_off", channel=ch, note=pitch, velocity=0, time=0),
            ),
        ]

    def flush_track(name: str, raw: list[tuple[int, mido.Message]]) -> mido.MidiTrack:
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=name, time=0))
        raw.sort(key=lambda x: (x[0], 0 if x[1].type == "note_off" else 1))
        prev = 0
        for tick, msg in raw:
            track.append(msg.copy(time=max(0, tick - prev)))
            prev = tick
        return track

    drum_raw: list[tuple[int, mido.Message]] = []
    piano_raw: list[tuple[int, mido.Message]] = []
    for t in LATENCY_DRUM_TIMES_S:
        drum_raw.extend(note_events(second_to_tick(t), 9, 37, eighth))
        piano_raw.extend(
            note_events(second_to_tick(t + LATENCY_PIANO_OFFSET_S), 0, 60, eighth)
        )

    mid.tracks.append(flush_track("drums", drum_raw))
    mid.tracks.append(flush_track("piano", piano_raw))
    mid.save(path)


def _envelope(y: np.ndarray, sr: int) -> np.ndarray:
    hop = max(1, sr // 200)
    n = (y.size + hop - 1) // hop
    env = np.zeros(n, dtype=np.float64)
    for i in range(n):
        seg = y[i * hop : i * hop + hop]
        if seg.size:
            env[i] = float(np.sqrt(np.mean(seg * seg)))
    return env


def _first_onset_over_fraction(
    y: np.ndarray, sr: int, center_s: float, *, fraction: float = ENVELOPE_FRACTION
) -> float | None:
    hop = max(1, sr // 200)
    i0 = max(0, int((center_s - SEARCH_HALF_WINDOW_S) * sr))
    i1 = min(y.size, int((center_s + SEARCH_HALF_WINDOW_S) * sr))
    if i1 <= i0:
        return None
    seg = y[i0:i1]
    env = _envelope(seg, sr)
    peak = float(env.max()) if env.size else 0.0
    if peak <= 1e-12:
        return None
    thr = peak * fraction
    hits = np.flatnonzero(env >= thr)
    if hits.size == 0:
        return None
    rel_s = float(hits[0]) * hop / sr
    return i0 / sr + rel_s


def measure_render_latency(
    wav_path: Path | str,
    sr: int | None = None,
) -> list[LatencyMeasurement]:
    wav_path = Path(wav_path)
    y, file_sr = sf.read(str(wav_path), dtype="float64", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    use_sr = file_sr if sr is None else sr
    if use_sr != file_sr:
        raise ValueError("サンプルレート変換は未対応")
    out: list[LatencyMeasurement] = []
    for ev in latency_test_events():
        onset = _first_onset_over_fraction(y, use_sr, ev.midi_s)
        if onset is None:
            continue
        out.append(
            LatencyMeasurement(
                kind=ev.kind,
                midi_s=ev.midi_s,
                onset_s=onset,
                delay_s=onset - ev.midi_s,
            )
        )
    return out


def summarize_delays(measurements: list[LatencyMeasurement]) -> dict[str, Any]:
    by_kind: dict[str, list[float]] = {"drums": [], "piano": []}
    for m in measurements:
        by_kind.setdefault(m.kind, []).append(m.delay_s)
    summary: dict[str, Any] = {}
    for kind, vals in by_kind.items():
        if not vals:
            summary[kind] = {"mean_s": None, "spread_s": None, "n": 0}
            continue
        arr = np.array(vals, dtype=np.float64)
        summary[kind] = {
            "mean_s": float(arr.mean()),
            "spread_s": float(arr.max() - arr.min()),
            "n": len(vals),
        }
    return summary


def synthesize_delayed_clicks(
    delays_s: dict[float, float],
    duration_s: float,
    sr: int,
    *,
    extra_delay_s: float = 0.0,
) -> np.ndarray:
    """midi 時刻 → 追加遅延（秒）の辞書でクリック列を合成（テスト用）。"""
    n = int(round(duration_s * sr))
    t = np.arange(n, dtype=np.float64) / sr
    y = np.zeros(n, dtype=np.float64)
    for midi_s, delay in delays_s.items():
        hit = midi_s + delay + extra_delay_s
        mask = (t >= hit) & (t < hit + 0.02)
        y[mask] += np.sin(2 * np.pi * 440.0 * t[mask])
    peak = float(np.max(np.abs(y)))
    if peak > 1e-12:
        y /= peak
    return y
