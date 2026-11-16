"""カウントイン判定（仕様 第2.5節）。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from a2m.align import AlignConfig, AlignResult, transform_time
from a2m.intro_dump import (
    first_note_measure_from_m2,
    measure_start_tick,
    tick_to_measure_number,
)
from a2m.karaoke_midi import KaraokeMidiData, KaraokeNote
from a2m.midi_time import tick_to_second, time_signature_at_tick

COUNTIN_ROLE = "countin"
LOUD_FRAME_DBFS = -60.0


@dataclass(frozen=True)
class CountinConfig:
    max_bars: int
    silence_db: float

    @classmethod
    def from_dict(cls, p1: dict[str, Any]) -> CountinConfig:
        c = p1.get("countin", {})
        return cls(
            max_bars=int(c.get("max_bars", 3)),
            silence_db=float(c.get("silence_db", 30.0)),
        )


@dataclass(frozen=True)
class BeatVolumeInfo:
    measure: int
    beat: int
    midi_start_s: float
    midi_end_s: float
    audio_start_s: float
    audio_end_s: float
    rms_db: float
    silent: bool


@dataclass
class CountinResult:
    present: bool
    beat_count: int
    start_measure_beat: str | None
    end_measure_beat: str | None
    removed_by_role: dict[str, int]
    beat_infos: list[BeatVolumeInfo]
    offset_pass1: float
    scale_pass1: float = 1.0
    offset_pass2: float | None = None
    countin_keys: set[tuple[float, int, int]] = field(default_factory=set)
    orig_role_by_key: dict[tuple[float, int, int], str] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "present": self.present,
            "beat_count": self.beat_count,
            "start_measure_beat": self.start_measure_beat,
            "end_measure_beat": self.end_measure_beat,
            "removed_by_role": dict(self.removed_by_role),
            "offset_pass1": self.offset_pass1,
            "scale_pass1": self.scale_pass1,
            "offset_pass2": self.offset_pass2,
            "offset_delta": (
                None
                if self.offset_pass2 is None
                else self.offset_pass2 - self.offset_pass1
            ),
            "beats": [
                {
                    "measure_beat": f"M{b.measure}.{b.beat}",
                    "midi_start_s": b.midi_start_s,
                    "audio_start_s": b.audio_start_s,
                    "rms_db": float(b.rms_db),
                    "silent": bool(b.silent),
                }
                for b in self.beat_infos
            ],
        }
        return d


def format_measure_beat(measure: int, beat: int) -> str:
    return f"M{measure}.{beat}"


def tick_to_measure_beat(
    tick: int, signatures: list, ticks_per_beat: int
) -> tuple[int, int]:
    measure = tick_to_measure_number(tick, signatures, ticks_per_beat)
    mstart = measure_start_tick(measure, signatures, ticks_per_beat)
    beat = (tick - mstart) // ticks_per_beat + 1
    return measure, int(beat)


def iter_beats_in_bars(
    first_measure: int,
    max_bars: int,
    midi: KaraokeMidiData,
) -> list[tuple[int, int, float, float]]:
    """(measure, beat, start_s, end_s) を拍順に返す。"""
    tpb = midi.ticks_per_beat
    sigs = midi.time_signatures
    start_tick = measure_start_tick(first_measure, sigs, tpb)
    end_tick = measure_start_tick(first_measure + max_bars, sigs, tpb)
    out: list[tuple[int, int, float, float]] = []
    tick = start_tick
    while tick < end_tick:
        ts = time_signature_at_tick(tick, sigs)
        beat_ticks = tpb * 4 // ts.denominator
        m, b = tick_to_measure_beat(tick, sigs, tpb)
        t0 = tick_to_second(tick, midi.tempos, tpb)
        t1 = tick_to_second(min(tick + beat_ticks, end_tick), midi.tempos, tpb)
        out.append((m, b, t0, t1))
        tick += beat_ticks
    return out


def _frame_rms_db(y: np.ndarray, sr: int, hop: int) -> np.ndarray:
    n_frames = max(0, (y.size - hop) // hop + 1) if y.size else 0
    if n_frames == 0:
        return np.array([], dtype=np.float64)
    out = np.zeros(n_frames, dtype=np.float64)
    for i in range(n_frames):
        seg = y[i * hop : i * hop + hop]
        rms = float(np.sqrt(np.mean(seg * seg)))
        out[i] = 20.0 * np.log10(max(rms, 1e-12))
    return out


def audio_reference_db(y: np.ndarray, sr: int, hop: int) -> float:
    db = _frame_rms_db(y, sr, hop)
    loud = db[db > LOUD_FRAME_DBFS]
    if loud.size == 0:
        return LOUD_FRAME_DBFS
    return float(np.median(loud))


def segment_rms_db(y: np.ndarray, sr: int, t0: float, t1: float) -> float:
    i0 = max(0, int(np.floor(t0 * sr)))
    i1 = min(y.size, int(np.ceil(t1 * sr)))
    if i1 <= i0:
        return LOUD_FRAME_DBFS - 120.0
    seg = y[i0:i1]
    rms = float(np.sqrt(np.mean(seg * seg)))
    return 20.0 * np.log10(max(rms, 1e-12))


def _note_key(n: KaraokeNote) -> tuple[float, int, int]:
    return (round(n.start_s, 6), n.pitch, n.channel)


def detect_countin(
    midi: KaraokeMidiData,
    mp3_y: np.ndarray,
    align_result: AlignResult,
    countin_cfg: CountinConfig,
    align_cfg: AlignConfig,
    *,
    render_y: np.ndarray | None = None,
    latency_s: float = 0.0,
) -> CountinResult:
    first_m = first_note_measure_from_m2(midi)
    ref_db = audio_reference_db(mp3_y, align_cfg.sr, align_cfg.hop)
    threshold_db = ref_db - countin_cfg.silence_db
    render_loud_db: float | None = None
    if render_y is not None and render_y.size:
        ref_render = audio_reference_db(render_y, align_cfg.sr, align_cfg.hop)
        render_loud_db = ref_render - 20.0
    beats = iter_beats_in_bars(first_m, countin_cfg.max_bars, midi)

    beat_infos: list[BeatVolumeInfo] = []

    for m, b, t0, t1 in beats:
        a0 = transform_time(t0, align_result)
        a1 = transform_time(t1, align_result)
        rms_db = segment_rms_db(mp3_y, align_cfg.sr, a0, a1)
        silent = a0 < 0.0 or rms_db < threshold_db
        countin_beat = silent
        if render_y is not None and render_loud_db is not None:
            r0 = t0 + latency_s
            r1 = t1 + latency_s
            render_db = segment_rms_db(render_y, align_cfg.sr, r0, r1)
            countin_beat = silent and render_db >= render_loud_db
        beat_infos.append(
            BeatVolumeInfo(
                measure=m,
                beat=b,
                midi_start_s=t0,
                midi_end_s=t1,
                audio_start_s=a0,
                audio_end_s=a1,
                rms_db=rms_db,
                silent=countin_beat,
            )
        )
        if not countin_beat:
            break

    silent_beats = []
    for info in beat_infos:
        if info.silent:
            silent_beats.append(info)
        else:
            break

    present = len(silent_beats) > 0
    if not present:
        return CountinResult(
            present=False,
            beat_count=0,
            start_measure_beat=None,
            end_measure_beat=None,
            removed_by_role={},
            beat_infos=beat_infos,
            offset_pass1=align_result.offset,
            scale_pass1=align_result.scale,
        )

    interval_start_s = silent_beats[0].midi_start_s
    interval_end_s = silent_beats[-1].midi_end_s
    countin_keys: set[tuple[float, int, int]] = set()
    orig_role_by_key: dict[tuple[float, int, int], str] = {}
    removed: dict[str, int] = {}
    for n in midi.notes:
        if interval_start_s <= n.start_s < interval_end_s:
            key = _note_key(n)
            countin_keys.add(key)
            orig_role_by_key[key] = n.role
            removed[n.role] = removed.get(n.role, 0) + 1

    return CountinResult(
        present=True,
        beat_count=len(silent_beats),
        start_measure_beat=format_measure_beat(
            silent_beats[0].measure, silent_beats[0].beat
        ),
        end_measure_beat=format_measure_beat(
            silent_beats[-1].measure, silent_beats[-1].beat
        ),
        removed_by_role=removed,
        beat_infos=beat_infos,
        offset_pass1=align_result.offset,
        scale_pass1=align_result.scale,
        countin_keys=countin_keys,
        orig_role_by_key=orig_role_by_key,
    )


def apply_countin_tags(midi: KaraokeMidiData, result: CountinResult) -> KaraokeMidiData:
    if not result.countin_keys:
        return midi
    new_notes: list[KaraokeNote] = []
    for n in midi.notes:
        if _note_key(n) in result.countin_keys:
            new_notes.append(replace(n, role=COUNTIN_ROLE))
        else:
            new_notes.append(n)
    return replace(midi, notes=new_notes)


def countin_eval_notes(
    midi: KaraokeMidiData,
    result: CountinResult,
    align_result: AlignResult,
) -> list:
    from a2m.evaluate import EvalNote

    out: list[EvalNote] = []
    for n in midi.notes:
        key = _note_key(n)
        if key not in result.countin_keys:
            continue
        role = result.orig_role_by_key.get(key, n.role)
        out.append(
            EvalNote(
                role=role,
                pitch=n.pitch,
                start_s=transform_time(n.start_s, align_result),
                end_s=transform_time(n.end_s, align_result),
                drum_class=n.drum_class,
            )
        )
    return out
