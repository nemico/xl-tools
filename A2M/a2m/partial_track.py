"""CARD_P1-28: 正弦波の追跡（Sinusoidal Partial Tracking）モジュール。

STFTから各フレームの周波数ピークを放物線補間で検出し、時間方向に接続して
ノート候補を生成する。
- まとめない版: 全追跡をノート化
- 倍音をまとめる版: 同時発音のうち 2倍・3倍・4倍音（±50セント）を抑制
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Sequence

import librosa
import numpy as np

from a2m.evaluate import EvalNote


@dataclass(frozen=True)
class PartialTrackConfig:
    n_fft: int = 4096
    hop: int = 441
    peak_threshold_db: float = -60.0
    freq_tolerance_cents: float = 50.0
    max_gap_frames: int = 3
    min_track_duration_s: float = 0.050
    pitch_split_semitones: float = 0.5
    harmonic_multiples: tuple[int, ...] = (2, 3, 4)
    harmonic_tolerance_cents: float = 50.0
    min_freq_hz: float = 27.5
    max_freq_hz: float = 4186.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PartialTrackConfig:
        return cls(
            n_fft=int(d.get("n_fft", 4096)),
            hop=int(d.get("hop", 441)),
            peak_threshold_db=float(d.get("peak_threshold_db", -60.0)),
            freq_tolerance_cents=float(d.get("freq_tolerance_cents", 50.0)),
            max_gap_frames=int(d.get("max_gap_frames", 3)),
            min_track_duration_s=float(d.get("min_track_duration_s", 0.050)),
            pitch_split_semitones=float(d.get("pitch_split_semitones", 0.5)),
            harmonic_multiples=tuple(d.get("harmonic_multiples", [2, 3, 4])),
            harmonic_tolerance_cents=float(d.get("harmonic_tolerance_cents", 50.0)),
            min_freq_hz=float(d.get("min_freq_hz", 27.5)),
            max_freq_hz=float(d.get("max_freq_hz", 4186.0)),
        )


@dataclass
class SpectralPeak:
    frame: int
    freq_hz: float
    amp: float


@dataclass
class PartialTrack:
    track_id: int
    points: list[SpectralPeak]
    last_frame: int
    last_freq: float


def extract_frame_peaks(
    y: np.ndarray,
    sr: int = 44100,
    cfg: PartialTrackConfig = PartialTrackConfig(),
) -> list[list[SpectralPeak]]:
    """STFT を計算し、各フレームの局所ピークを放物線補間で検出する。"""
    if len(y) == 0:
        return []

    if y.ndim > 1:
        y = np.mean(y, axis=1)

    X = librosa.stft(y, n_fft=cfg.n_fft, hop_length=cfg.hop, window="hann")
    M = np.abs(X)  # shape: (1 + n_fft // 2, n_frames)
    n_bins, n_frames = M.shape

    if n_bins < 3 or n_frames == 0:
        return [[] for _ in range(n_frames)]

    # 1. 周波数軸での局所最大判定 (bin 1 〜 n_bins - 2)
    is_peak = (M[1:-1, :] > M[:-2, :]) & (M[1:-1, :] > M[2:, :])

    # 2. フレーム最大から -60dB 以内の閾値判定
    max_per_frame = np.max(M, axis=0, keepdims=True)
    min_amp_thresh = max_per_frame * (10.0 ** (cfg.peak_threshold_db / 20.0))
    valid_mask = is_peak & (M[1:-1, :] >= min_amp_thresh) & (M[1:-1, :] > 1e-9)

    # 3. ピーク位置の取得と放物線補間
    peak_k_indices, peak_t_indices = np.where(valid_mask)
    # peak_k_indices は M[1:-1, :] に対するインデックスなので、元の bin は +1
    k_bins = peak_k_indices + 1
    t_frames = peak_t_indices

    alpha = 20.0 * np.log10(M[k_bins - 1, t_frames] + 1e-12)
    beta = 20.0 * np.log10(M[k_bins, t_frames] + 1e-12)
    gamma = 20.0 * np.log10(M[k_bins + 1, t_frames] + 1e-12)

    denom = alpha - 2.0 * beta + gamma
    delta = np.where(np.abs(denom) > 1e-9, 0.5 * (alpha - gamma) / denom, 0.0)
    delta = np.clip(delta, -0.5, 0.5)

    interp_bins = k_bins.astype(float) + delta
    freqs = interp_bins * (float(sr) / float(cfg.n_fft))
    amps_db = beta - 0.25 * (alpha - gamma) * delta
    amps = 10.0 ** (amps_db / 20.0)

    # 周波数範囲フィルタ
    freq_ok = (freqs >= cfg.min_freq_hz) & (freqs <= cfg.max_freq_hz)
    valid_k = k_bins[freq_ok]
    valid_t = t_frames[freq_ok]
    valid_f = freqs[freq_ok]
    valid_a = amps[freq_ok]

    peaks_by_frame: list[list[SpectralPeak]] = [[] for _ in range(n_frames)]
    for t, f, a in zip(valid_t, valid_f, valid_a):
        peaks_by_frame[int(t)].append(SpectralPeak(frame=int(t), freq_hz=float(f), amp=float(a)))

    return peaks_by_frame


def track_partials(
    peaks_by_frame: list[list[SpectralPeak]],
    sr: int = 44100,
    cfg: PartialTrackConfig = PartialTrackConfig(),
) -> list[PartialTrack]:
    """時間方向につなぎ、50ms 未満の追跡を破棄する。"""
    active_tracks: list[PartialTrack] = []
    finished_tracks: list[PartialTrack] = []
    next_track_id = 0

    for t, frame_peaks in enumerate(peaks_by_frame):
        # 1. 許容欠落フレーム (max_gap_frames) を超えたアクティブトラックを終了
        still_active: list[PartialTrack] = []
        for tr in active_tracks:
            if t - tr.last_frame > cfg.max_gap_frames:
                finished_tracks.append(tr)
            else:
                still_active.append(tr)
        active_tracks = still_active

        if not frame_peaks:
            continue

        # 2. アクティブトラックと現在フレームのピークをマッチング
        if active_tracks:
            active_tracks.sort(key=lambda tr: tr.last_freq)
            frame_peaks.sort(key=lambda p: p.freq_hz)
            p_freqs = [p.freq_hz for p in frame_peaks]
            ratio_low = 2.0 ** (-cfg.freq_tolerance_cents / 1200.0)
            ratio_high = 2.0 ** (cfg.freq_tolerance_cents / 1200.0)

            pairs: list[tuple[float, int, int]] = []
            l_idx = 0
            n_p = len(p_freqs)
            for tr_idx, tr in enumerate(active_tracks):
                min_f = tr.last_freq * ratio_low
                max_f = tr.last_freq * ratio_high
                while l_idx < n_p and p_freqs[l_idx] < min_f:
                    l_idx += 1
                p_idx = l_idx
                while p_idx < n_p and p_freqs[p_idx] <= max_f:
                    cents = 1200.0 * abs(math.log2(p_freqs[p_idx] / tr.last_freq))
                    if cents <= cfg.freq_tolerance_cents:
                        pairs.append((cents, tr_idx, p_idx))
                    p_idx += 1

            pairs.sort(key=lambda x: (x[0], x[1], x[2]))
            matched_tracks = set()
            matched_peaks = set()

            for _cents, tr_idx, p_idx in pairs:
                if tr_idx in matched_tracks or p_idx in matched_peaks:
                    continue
                matched_tracks.add(tr_idx)
                matched_peaks.add(p_idx)

                p = frame_peaks[p_idx]
                tr = active_tracks[tr_idx]
                tr.points.append(p)
                tr.last_frame = t
                tr.last_freq = p.freq_hz

            # 未マッチのピークは新規トラック作成
            for p_idx, p in enumerate(frame_peaks):
                if p_idx not in matched_peaks:
                    active_tracks.append(
                        PartialTrack(
                            track_id=next_track_id,
                            points=[p],
                            last_frame=t,
                            last_freq=p.freq_hz,
                        )
                    )
                    next_track_id += 1
        else:
            for p in frame_peaks:
                active_tracks.append(
                    PartialTrack(
                        track_id=next_track_id,
                        points=[p],
                        last_frame=t,
                        last_freq=p.freq_hz,
                    )
                )
                next_track_id += 1

    finished_tracks.extend(active_tracks)

    # 3. 50ms 未満の追跡を捨てる
    survived: list[PartialTrack] = []
    hop_s = cfg.hop / sr
    for tr in finished_tracks:
        if not tr.points:
            continue
        first_frame = tr.points[0].frame
        last_frame = tr.points[-1].frame
        dur_s = (last_frame - first_frame + 1) * hop_s
        if dur_s >= cfg.min_track_duration_s:
            survived.append(tr)

    return survived


def tracks_to_notes(
    tracks: list[PartialTrack],
    sr: int = 44100,
    cfg: PartialTrackConfig = PartialTrackConfig(),
    role: str = "other",
) -> list[tuple[EvalNote, float]]:
    """追跡をノートに変換する。途中で半音以上動いた場合は分割する。

    Returns:
        list of (EvalNote, median_freq_hz)
    """
    notes_with_freq: list[tuple[EvalNote, float]] = []
    hop_s = cfg.hop / sr

    for tr in tracks:
        if not tr.points:
            continue

        pts = tr.points
        midi_floats = [69.0 + 12.0 * math.log2(p.freq_hz / 440.0) for p in pts]

        # 半音変化で区切る
        seg_start_idx = 0
        current_ref_pitch = round(midi_floats[0])

        for i in range(1, len(pts)):
            if abs(midi_floats[i] - current_ref_pitch) >= cfg.pitch_split_semitones:
                seg_pts = pts[seg_start_idx:i]
                seg_midis = midi_floats[seg_start_idx:i]
                start_s = seg_pts[0].frame * hop_s
                end_s = (seg_pts[-1].frame + 1) * hop_s
                dur = end_s - start_s
                if dur >= cfg.min_track_duration_s:
                    med_p = int(round(float(np.median(seg_midis))))
                    med_f = float(np.median([p.freq_hz for p in seg_pts]))
                    notes_with_freq.append((EvalNote(role=role, pitch=med_p, start_s=start_s, end_s=end_s), med_f))

                seg_start_idx = i
                current_ref_pitch = round(midi_floats[i])

        # 最後の区間
        seg_pts = pts[seg_start_idx:]
        seg_midis = midi_floats[seg_start_idx:]
        start_s = seg_pts[0].frame * hop_s
        end_s = (seg_pts[-1].frame + 1) * hop_s
        dur = end_s - start_s
        if dur >= cfg.min_track_duration_s:
            med_p = int(round(float(np.median(seg_midis))))
            med_f = float(np.median([p.freq_hz for p in seg_pts]))
            notes_with_freq.append((EvalNote(role=role, pitch=med_p, start_s=start_s, end_s=end_s), med_f))

    return notes_with_freq


def suppress_harmonics(
    notes_with_freq: list[tuple[EvalNote, float]],
    cfg: PartialTrackConfig = PartialTrackConfig(),
) -> list[EvalNote]:
    """同時に鳴っている追跡のうち、低いほうの 2倍・3倍・4倍音（±50セント）を捨てる。"""
    if not notes_with_freq:
        return []

    # 周波数順にソート (基本波から倍音を判定)
    notes_sorted = sorted(notes_with_freq, key=lambda item: item[1])
    n = len(notes_sorted)
    is_harmonic = [False] * n

    freqs = [item[1] for item in notes_sorted]
    starts = [item[0].start_s for item in notes_sorted]
    ends = [item[0].end_s for item in notes_sorted]

    ratio_low = 2.0 ** (-cfg.harmonic_tolerance_cents / 1200.0)
    ratio_high = 2.0 ** (cfg.harmonic_tolerance_cents / 1200.0)

    for h in cfg.harmonic_multiples:
        h_f = float(h)
        l_idx = 0
        r_idx = 0
        for i in range(n):
            if is_harmonic[i]:
                continue
            f_i = freqs[i]
            min_f = f_i * h_f * ratio_low
            max_f = f_i * h_f * ratio_high
            while l_idx < n and freqs[l_idx] < min_f:
                l_idx += 1
            if r_idx < l_idx:
                r_idx = l_idx
            while r_idx < n and freqs[r_idx] <= max_f:
                r_idx += 1

            s_i = starts[i]
            e_i = ends[i]
            dur_i = e_i - s_i
            for j in range(l_idx, r_idx):
                if is_harmonic[j] or j <= i:
                    continue
                s_j = starts[j]
                e_j = ends[j]
                if e_j <= s_i or s_j >= e_i:
                    continue
                overlap = (e_i if e_i < e_j else e_j) - (s_i if s_i > s_j else s_j)
                dur_shorter = dur_i if dur_i < (e_j - s_j) else (e_j - s_j)
                if overlap >= min(0.030, dur_shorter * 0.3):
                    is_harmonic[j] = True

    return [note for (note, _f), harm in zip(notes_sorted, is_harmonic) if not harm]


def sinusoidal_transcribe(
    y: np.ndarray,
    sr: int = 44100,
    cfg: PartialTrackConfig = PartialTrackConfig(),
    role: str = "other",
    suppress_harmonics_flag: bool = False,
) -> list[EvalNote]:
    """正弦波の追跡による採譜（まとめない版 / 倍音をまとめる版）。"""
    peaks = extract_frame_peaks(y, sr, cfg)
    tracks = track_partials(peaks, sr, cfg)
    notes_with_freq = tracks_to_notes(tracks, sr, cfg, role=role)

    if suppress_harmonics_flag:
        return suppress_harmonics(notes_with_freq, cfg)
    return [note for note, _f in notes_with_freq]
