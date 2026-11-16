"""CARD_P1-45: データに基づく見本窓（時間・音高・ベンド・窓位置）の実測と最適化。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import librosa
import mido
import numpy as np

from a2m.midi_time import tick_to_second


@dataclass
class PitchBendPoint:
    time_s: float
    cents: float


@dataclass
class NoteBendProfile:
    has_bend: bool
    max_abs_cents: float
    mean_cents: float
    bend_type: str  # "none", "onset", "held", "vibrato"
    bend_points: list[PitchBendPoint] = field(default_factory=list)


def parse_midi_track_pitch_bends(
    midi_path: Path,
    tempos: list[Any],
    ticks_per_beat: int = 480,
) -> dict[int, list[PitchBendPoint]]:
    """
    MIDI ファイルからトラックごとに RPN 0 (Pitch Bend Sensitivity) を解釈し、
    ピッチベンドイベントを秒単位のセント値に変換して返す。
    """
    mid = mido.MidiFile(midi_path)
    track_bends: dict[int, list[PitchBendPoint]] = {}

    for tr_idx, track in enumerate(mid.tracks):
        curr_tick = 0
        bend_range_semitones = 2.0  # デフォルト 2半音 (200セント)
        bend_range_cents = 0.0
        rpn_msb = None
        rpn_lsb = None

        points: list[PitchBendPoint] = []

        for msg in track:
            curr_tick += msg.time
            t_s = tick_to_second(curr_tick, tempos, ticks_per_beat)

            if msg.type == "control_change":
                if msg.control == 101:  # RPN MSB
                    rpn_msb = msg.value
                elif msg.control == 100:  # RPN LSB
                    rpn_lsb = msg.value
                elif msg.control == 6:  # Data Entry MSB
                    if rpn_msb == 0 and rpn_lsb == 0:
                        bend_range_semitones = float(msg.value)
                elif msg.control == 38:  # Data Entry LSB
                    if rpn_msb == 0 and rpn_lsb == 0:
                        bend_range_cents = float(msg.value)

            elif msg.type == "pitchwheel":
                # pitch: -8192 to 8191 (中心 0)
                # mido では msg.pitch は -8192 .. 8191
                val = float(msg.pitch)
                total_range_cents = bend_range_semitones * 100.0 + bend_range_cents
                cents = (val / 8192.0) * total_range_cents
                points.append(PitchBendPoint(time_s=t_s, cents=cents))

        track_bends[tr_idx] = points

    return track_bends


def analyze_note_bend(
    start_s: float,
    end_s: float,
    bend_points: list[PitchBendPoint],
    tol_cents: float = 10.0,
) -> NoteBendProfile:
    """1つのノート区間に重なるピッチベンドを解析する。"""
    overlapping = [p for p in bend_points if start_s - 0.05 <= p.time_s <= end_s + 0.05]
    if not overlapping:
        return NoteBendProfile(has_bend=False, max_abs_cents=0.0, mean_cents=0.0, bend_type="none")

    cents_vals = [p.cents for p in overlapping]
    abs_vals = [abs(c) for c in cents_vals]
    max_abs = float(max(abs_vals))

    if max_abs < tol_cents:
        return NoteBendProfile(has_bend=False, max_abs_cents=max_abs, mean_cents=float(np.mean(cents_vals)), bend_type="none", bend_points=overlapping)

    # 形状の判定
    dur = max(1e-3, end_s - start_s)
    onset_points = [p.cents for p in overlapping if p.time_s <= start_s + 0.25 * dur]
    late_points = [p.cents for p in overlapping if p.time_s > start_s + 0.5 * dur]

    # ゼロクロス回数でビブラートを判定
    zero_crossings = 0
    mean_val = float(np.mean(cents_vals))
    centered = [c - mean_val for c in cents_vals]
    for i in range(len(centered) - 1):
        if centered[i] * centered[i + 1] < 0:
            zero_crossings += 1

    if zero_crossings >= 3:
        b_type = "vibrato"
    elif onset_points and max([abs(c) for c in onset_points]) >= tol_cents and (not late_points or max([abs(c) for c in late_points]) < tol_cents):
        b_type = "onset"
    else:
        b_type = "held"

    return NoteBendProfile(
        has_bend=True,
        max_abs_cents=max_abs,
        mean_cents=float(np.mean(cents_vals)),
        bend_type=b_type,
        bend_points=overlapping,
    )


def measure_band_onset_diff(
    y: np.ndarray,
    sr: int,
    note_start_s: float,
    pitch: int,
    search_win_s: float = 0.300,
    hop_length: int = 441,
) -> float | None:
    """
    ノート開始時刻の前後 ±search_win_s で、ノートの帯域に絞った立ち上がりを探し、
    一番近い山との差 (t_peak - note_start_s) を秒単位で返す。
    """
    t_min = max(0.0, note_start_s - search_win_s)
    t_max = min(float(len(y) / sr), note_start_s + search_win_s)
    if t_max <= t_min:
        return None

    # 周波数帯域の決定
    if pitch > 0:
        f0 = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
        fmin = max(20.0, f0 * 0.7)
        fmax = min(float(sr / 2.0), f0 * 3.5)
    else:
        # ドラム等の全帯域
        fmin = 30.0
        fmax = 12000.0

    # 区間切り出し (前後に少し余白を持たせる)
    pad_s = 0.1
    i_min = int(max(0.0, t_min - pad_s) * sr)
    i_max = int(min(float(len(y) / sr), t_max + pad_s) * sr)
    y_slice = y[i_min:i_max]
    if len(y_slice) < hop_length * 4:
        return None

    try:
        onset_env = librosa.onset.onset_strength(
            y=y_slice,
            sr=sr,
            fmin=fmin,
            fmax=fmax,
            hop_length=hop_length,
        )
    except Exception:
        return None

    frames = np.arange(len(onset_env))
    times = (i_min / sr) + librosa.frames_to_time(frames, sr=sr, hop_length=hop_length)

    # 検索範囲 [t_min, t_max] 内の山 (peaks) を検出
    in_range = (times >= t_min) & (times <= t_max)
    if not np.any(in_range):
        return None

    valid_times = times[in_range]
    valid_env = onset_env[in_range]

    # 局所ピーク検出
    peaks = librosa.util.peak_pick(
        valid_env,
        pre_max=3,
        post_max=3,
        pre_avg=3,
        post_avg=3,
        delta=0.01,
        wait=3,
    )
    if len(peaks) > 0:
        peak_times = valid_times[peaks]
        # note_start_s に最も近い山
        closest_idx = int(np.argmin(np.abs(peak_times - note_start_s)))
        return float(peak_times[closest_idx] - note_start_s)
    else:
        # ピークが見つからなければ区間内最大値
        max_idx = int(np.argmax(valid_env))
        return float(valid_times[max_idx] - note_start_s)


def measure_pitch_diff_cents(
    y: np.ndarray,
    sr: int,
    note_start_s: float,
    note_end_s: float,
    pitch: int,
    search_cents: float = 200.0,
    n_fft: int = 4096,
) -> float | None:
    """
    ノート区間の中央付近で、公称周波数の前後 ±search_cents 内の最大振幅周波数を探し、
    公称との差 (cents) を返す。
    """
    if pitch <= 0:
        return None

    f0 = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
    ratio = 2.0 ** (search_cents / 1200.0)
    f_min = f0 / ratio
    f_max = f0 * ratio

    # ノートの前半〜中盤の音響
    dur = note_end_s - note_start_s
    t_center = note_start_s + min(0.15, max(0.05, dur * 0.3))
    i_center = int(t_center * sr)
    half = n_fft // 2
    i_start = max(0, i_center - half)
    i_end = min(len(y), i_start + n_fft)
    chunk = y[i_start:i_end]
    if len(chunk) < n_fft:
        chunk = np.pad(chunk, (0, n_fft - len(chunk)))

    windowed = chunk * np.hanning(len(chunk))
    spec = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)

    idx_min = int(np.searchsorted(freqs, f_min))
    idx_max = int(np.searchsorted(freqs, f_max))
    if idx_max <= idx_min:
        idx_max = min(len(freqs), idx_min + 1)
        idx_min = max(0, idx_max - 1)

    sub_spec = spec[idx_min:idx_max]
    if len(sub_spec) == 0:
        return None

    peak_local_idx = int(np.argmax(sub_spec))
    f_actual = float(freqs[idx_min + peak_local_idx])
    if f_actual <= 0:
        return None

    diff_cents = 1200.0 * math.log2(f_actual / f0)
    return diff_cents
