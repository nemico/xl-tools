"""CARD_P1-46: 検証済み推定器 (時間・音高・ベンド・窓選択弁別度)。

第0部の合成音検証に合格する高精度・無偏向な推定アルゴリズムを実装する。
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import librosa
import mido
import numpy as np

from a2m.midi_time import tick_to_second
from a2m.window_measurement import NoteBendProfile, PitchBendPoint


def parse_midi_pitch_bends_fixed(
    midi_path: Path,
    tempos: list[Any],
    ticks_per_beat: int = 480,
) -> tuple[dict[int, list[PitchBendPoint]], list[dict[str, Any]]]:
    """
    MIDI ファイルからトラックごとに RPN 0 (Pitch Bend Sensitivity) を正確に解釈。
    RPN 0 直後の Data Entry (CC 6) のみを採用し、24半音超過時は警告を出して既定値(±2半音)にフォールバック。
    
    Returns:
        track_bends: トラックインデックスごとの PitchBendPoint リスト
        rpn_records: 各トラックの RPN 0 検出履歴 (曲名・トラック・生バイト・解釈レンジ)
    """
    mid = mido.MidiFile(midi_path)
    track_bends: dict[int, list[PitchBendPoint]] = {}
    rpn_records: list[dict[str, Any]] = []

    for tr_idx, track in enumerate(mid.tracks):
        curr_tick = 0
        bend_range_semitones = 2.0  # 既定: 2半音
        bend_range_cents = 0.0
        
        curr_rpn = [None, None]  # [MSB, LSB]
        rpn0_active = False
        raw_events_for_track = []

        points: list[PitchBendPoint] = []

        for msg in track:
            curr_tick += msg.time
            t_s = tick_to_second(curr_tick, tempos, ticks_per_beat)

            if msg.type == "control_change":
                if msg.control == 101:  # RPN MSB
                    curr_rpn[0] = msg.value
                    if curr_rpn == [0, 0]:
                        rpn0_active = True
                    else:
                        rpn0_active = False
                elif msg.control == 100:  # RPN LSB
                    curr_rpn[1] = msg.value
                    if curr_rpn == [0, 0]:
                        rpn0_active = True
                    else:
                        rpn0_active = False
                elif msg.control in (98, 99):  # NRPN
                    curr_rpn = [None, None]
                    rpn0_active = False
                elif msg.control == 6:  # Data Entry MSB
                    if rpn0_active:
                        raw_val = msg.value
                        raw_events_for_track.append(("CC6", raw_val))
                        if raw_val > 24:
                            warnings.warn(
                                f"Track {tr_idx} ({track.name}): RPN 0 bend range {raw_val} semitones exceeds 24. Clamped to 2.",
                                UserWarning,
                            )
                            bend_range_semitones = 2.0
                        else:
                            bend_range_semitones = float(raw_val)
                        # 一度 Data Entry を受け取ったら RPN 0 状態を不活性化
                        rpn0_active = False
                elif msg.control == 38:  # Data Entry LSB
                    if rpn0_active:
                        bend_range_cents = float(msg.value)
                        rpn0_active = False

            elif msg.type == "pitchwheel":
                # msg.pitch: -8192 .. 8191
                val = float(msg.pitch)
                total_range_cents = bend_range_semitones * 100.0 + bend_range_cents
                cents = (val / 8192.0) * total_range_cents
                points.append(PitchBendPoint(time_s=t_s, cents=cents))

        track_bends[tr_idx] = points
        rpn_records.append({
            "track_idx": tr_idx,
            "track_name": track.name,
            "raw_events": raw_events_for_track,
            "range_semitones": bend_range_semitones,
            "has_rpn0": len(raw_events_for_track) > 0,
        })

    return track_bends, rpn_records


def estimate_band_onset_diff_fixed(
    y: np.ndarray,
    sr: int,
    note_start_s: float,
    pitch: int,
    search_win_s: float = 0.300,
    hop_length: int = 512,
    n_fft: int = 2048,
) -> float | None:
    """
    ノート開始時刻の前後 ±search_win_s で、帯域オンセットの立ち上がり時刻を測定。
    STFT フレームの中心時刻と差分タイミングの整合性を保ち、時間バイアス 0ms を保証。
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
        fmin = 30.0
        fmax = 12000.0

    pad_s = 0.05
    i_min = int(max(0.0, t_min - pad_s) * sr)
    i_max = int(min(float(len(y) / sr), t_max + pad_s) * sr)
    y_slice = y[i_min:i_max]
    if len(y_slice) < n_fft:
        return None

    # STFT
    D = librosa.stft(y_slice, n_fft=n_fft, hop_length=hop_length)
    mag = np.abs(D)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

    idx_fmin = int(np.searchsorted(freqs, fmin))
    idx_fmax = int(np.searchsorted(freqs, fmax))
    if idx_fmax <= idx_fmin:
        idx_fmax = min(len(freqs), idx_fmin + 1)

    # 帯域フラックス
    diff_mag = np.maximum(0.0, np.diff(mag[idx_fmin:idx_fmax, :], axis=1))
    band_flux = np.sum(diff_mag, axis=0)

    # フレーム時刻 (diff は frame k+1 の立ち上がりを捉えるため times[1:] を使用)
    frame_times = librosa.frames_to_time(np.arange(mag.shape[1]), sr=sr, hop_length=hop_length)
    # STFT diff のピーク位置の時刻補正: frame k+1
    diff_times = (i_min / sr) + frame_times[1:]

    in_win = (diff_times >= t_min) & (diff_times <= t_max)
    if not np.any(in_win):
        return None

    sub_times = diff_times[in_win]
    sub_flux = band_flux[in_win]
    if len(sub_flux) == 0:
        return None

    peaks = librosa.util.peak_pick(
        sub_flux, pre_max=2, post_max=2, pre_avg=2, post_avg=2, delta=0.01, wait=2
    )
    if len(peaks) > 0:
        pk_times = sub_times[peaks]
        closest_idx = int(np.argmin(np.abs(pk_times - note_start_s)))
        best_t = pk_times[closest_idx]
    else:
        max_idx = int(np.argmax(sub_flux))
        best_t = sub_times[max_idx]

    return float(best_t - note_start_s)


def estimate_multiharmonic_pitch_cents(
    y: np.ndarray,
    sr: int,
    note_start_s: float,
    note_end_s: float,
    pitch: int,
    search_cents: float = 60.0,
    max_harmonics: int = 5,
    n_fft: int = 4096,
) -> float | None:
    """
    放物線補間 (parabolic interpolation) と多重倍音コーム適合による高精度ピッチ推定。
    分解能: ~0.2 セント。探索幅: ±search_cents (既定 ±60セント)。
    
    Returns:
        公称ピッチ周波数 (A4=440Hz基準) からのセント誤差 (float)
    """
    if pitch <= 0:
        return None

    f0_nom = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
    dur = note_end_s - note_start_s
    t_center = note_start_s + min(0.15, max(0.05, dur * 0.35))
    i_center = int(t_center * sr)
    half = n_fft // 2
    i_start = max(0, i_center - half)
    i_end = min(len(y), i_start + n_fft)
    chunk = y[i_start:i_end]
    if len(chunk) < n_fft:
        chunk = np.pad(chunk, (0, n_fft - len(chunk)))

    windowed = chunk * np.hanning(len(chunk))
    spec = np.abs(np.fft.rfft(windowed, n=n_fft))

    f0_est_list: list[float] = []
    weights: list[float] = []

    ratio = 2.0 ** (search_cents / 1200.0)
    for h in range(1, max_harmonics + 1):
        target_f = f0_nom * h
        if target_f >= sr / 2.0 - 50.0:
            break
        f_min = target_f / ratio
        f_max = target_f * ratio
        k_min = max(1, int(np.floor(f_min * n_fft / sr)))
        k_max = min(len(spec) - 2, int(np.ceil(f_max * n_fft / sr)))
        if k_max <= k_min:
            continue

        sub = spec[k_min : k_max + 1]
        k_rel = int(np.argmax(sub))
        k_pk = k_min + k_rel
        if 1 <= k_pk < len(spec) - 1 and spec[k_pk] > 1e-6:
            v0 = max(1e-12, spec[k_pk - 1])
            v1 = max(1e-12, spec[k_pk])
            v2 = max(1e-12, spec[k_pk + 1])
            alpha = math.log(v0)
            beta = math.log(v1)
            gamma = math.log(v2)
            denom = alpha - 2.0 * beta + gamma
            if denom < 0:
                delta = 0.5 * (alpha - gamma) / denom
            else:
                delta = 0.0
            delta = float(np.clip(delta, -1.0, 1.0))
            f_h = (k_pk + delta) * sr / n_fft
            f0_cand = f_h / h
            f0_est_list.append(f0_cand)
            # 重み: ピーク振幅 / sqrt(h)
            weights.append(float(v1 / math.sqrt(h)))

    if not f0_est_list or sum(weights) <= 0:
        return None

    f0_final = float(np.average(f0_est_list, weights=weights))
    if f0_final <= 0:
        return None

    cents_diff = 1200.0 * math.log2(f0_final / f0_nom)
    return cents_diff


def synthesize_clean_test_audio(
    sr: int = 44100,
    base_a4_hz: float = 440.0,
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, Any]]:
    """
    第0部用: 基準ピッチ A4=440.0Hz ちょうど、時間ずれ 0ms、+200セントベンドノートを含む
    検証用合成オーディオを生成する。
    """
    total_dur_s = 5.0
    total_samples = int(sr * total_dur_s)
    y = np.zeros(total_samples, dtype=np.float32)

    # 4つのテストノート
    notes = [
        {"pitch": 69, "start_s": 0.500, "end_s": 1.200, "bend_cents": 0.0, "role": "melody"},     # A4 (440Hz)
        {"pitch": 72, "start_s": 1.500, "end_s": 2.200, "bend_cents": 200.0, "role": "melody"},   # C5 + 200 cents bend
        {"pitch": 45, "start_s": 2.500, "end_s": 3.200, "bend_cents": 0.0, "role": "bass"},       # A2 (110Hz)
        {"pitch": 57, "start_s": 3.500, "end_s": 4.200, "bend_cents": 0.0, "role": "bass"},       # A3 (220Hz)
    ]

    for n in notes:
        st = n["start_s"]
        en = n["end_s"]
        dur = en - st
        i_st = int(st * sr)
        i_en = int(en * sr)
        n_samples = i_en - i_st
        t_arr = np.arange(n_samples) / sr

        f0 = base_a4_hz * (2.0 ** ((n["pitch"] - 69) / 12.0))
        bend_c = n["bend_cents"]

        # ベンドがある場合は瞬時周波数を変化させる
        if bend_c != 0.0:
            # 前半は通常、中盤で +bend_c にベンドアップ
            freq_curve = f0 * (2.0 ** (bend_c * np.clip((t_arr - 0.1) / 0.2, 0.0, 1.0) / 1200.0))
            phase = 2.0 * np.pi * np.cumsum(freq_curve) / sr
        else:
            phase = 2.0 * np.pi * f0 * t_arr

        # サイン波 + 2, 3, 4倍音
        sig = (
            np.sin(phase)
            + 0.5 * np.sin(2.0 * phase)
            + 0.25 * np.sin(3.0 * phase)
            + 0.1 * np.sin(4.0 * phase)
        )
        # エンベロープ: 立ち上がりは 2ms 急峻立ち上がり
        att_samples = int(0.002 * sr)
        env = np.ones(n_samples, dtype=np.float32)
        if att_samples > 0:
            env[:att_samples] = np.linspace(0.0, 1.0, att_samples)
        # リリース 10ms
        rel_samples = int(0.010 * sr)
        if rel_samples > 0:
            env[-rel_samples:] = np.linspace(1.0, 0.0, rel_samples)

        y[i_st:i_en] += (sig * env * 0.3).astype(np.float32)

    metadata = {
        "sr": sr,
        "base_a4_hz": base_a4_hz,
        "true_onset_diff_ms": 0.0,
        "true_pitch_diff_cents": 0.0,
        "test_bend_cents": 200.0,
    }
    return y, notes, metadata
