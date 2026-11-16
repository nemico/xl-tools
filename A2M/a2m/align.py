"""音源 mp3 と納品 MIDI の位置合わせ（仕様 第3節）。"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import librosa
import numpy as np
from scipy.signal import correlate
from sklearn.linear_model import HuberRegressor

from a2m.evaluate import GT_EXCLUDED_ROLES, EvalNote
from a2m.karaoke_midi import KaraokeMidiData, KaraokeNote, load_karaoke_midi
from a2m.intro_dump import measure_start_tick, tick_to_measure_number
from a2m.midi_time import (
    m2_start_tick,
    measure_length_ticks,
    second_to_tick,
    tick_to_second,
    time_signature_at_tick,
)

TAIL_WINDOW_BARS = 8
TAIL_SEARCH_S = 0.5
TAIL_MISMATCH_THRESHOLD_S = 0.05

EXPORT_ROLES = frozenset({"melody", "melody2", "bass", "drums", "other"})
CHROMA_ROLES = frozenset({"melody", "melody2", "bass", "other"})


@dataclass(frozen=True)
class AlignConfig:
    sr: int
    hop: int
    search_min_s: float
    search_max_s: float
    local_window_bars: int
    local_range_s: float
    blur_sigma_s: float
    drum_weight: float
    warn_scale_dev: float
    warn_residual_s: float
    w_on: float
    w_ch: float
    margin_min: float
    second_peak_exclude_s: float
    min_onsets_per_window: int
    min_overlap_ratio: float
    chroma_n_fft: int
    min_windows: int
    edge_tol_s: float
    warn_edge_ratio: float
    render_confirm_tol_s: float
    feature_bias_s: float
    midi_render_gap_warn_ms: float

    @classmethod
    def from_dict(cls, p1: dict[str, Any]) -> AlignConfig:
        a = p1["align"]
        return cls(
            sr=int(p1["sr"]),
            hop=int(p1["hop"]),
            search_min_s=float(a["search_min_s"]),
            search_max_s=float(a["search_max_s"]),
            local_window_bars=int(a["local_window_bars"]),
            local_range_s=float(a["local_range_s"]),
            blur_sigma_s=float(a["blur_sigma_s"]),
            drum_weight=float(a["drum_weight"]),
            warn_scale_dev=float(a["warn_scale_dev"]),
            warn_residual_s=float(a["warn_residual_s"]),
            w_on=float(a["w_on"]),
            w_ch=float(a["w_ch"]),
            margin_min=float(a["margin_min"]),
            second_peak_exclude_s=float(a["second_peak_exclude_s"]),
            min_onsets_per_window=int(a["min_onsets_per_window"]),
            min_overlap_ratio=float(a["min_overlap_ratio"]),
            chroma_n_fft=int(a["chroma_n_fft"]),
            min_windows=int(a["min_windows"]),
            edge_tol_s=float(a["edge_tol_s"]),
            warn_edge_ratio=float(a["warn_edge_ratio"]),
            render_confirm_tol_s=float(a.get("render_confirm_tol_s", 0.02)),
            feature_bias_s=float(a.get("feature_bias_s", 0.0)),
            midi_render_gap_warn_ms=float(a.get("midi_render_gap_warn_ms", 20.0)),
        )


@dataclass
class LocalAlignSegment:
    midi_start_s: float
    midi_end_s: float
    center_s: float
    local_offset_s: float
    used_for_fit: bool
    skip_reason: str | None = None
    stuck_edge: bool = False


@dataclass
class AlignResult:
    offset: float
    scale: float
    margin: float
    peak1_offset_s: float
    peak1_score: float
    peak2_offset_s: float | None
    peak2_score: float | None
    best_offset_s_on: float
    best_offset_s_ch: float
    residual_median_s: float | None
    residual_max_s: float | None
    windows_total: int
    windows_used: int
    windows_stuck_edge: int
    local_segments: list[LocalAlignSegment] = field(default_factory=list)
    align_warning: bool = False
    warning_reasons: list[str] = field(default_factory=list)
    first_audio_onset_s: float = 0.0
    render_check: dict[str, Any] | None = None
    tail_check_s: float | None = None
    countin: dict[str, Any] | None = None
    alignment_source: str | None = None
    compare_diff_ms: list[float] | None = None
    midi_align: dict[str, Any] | None = None
    render_align: dict[str, Any] | None = None
    content_mismatch_intervals: list[dict[str, Any]] = field(default_factory=list)
    time_mismatch_intervals: list[dict[str, Any]] = field(default_factory=list)
    audio_check: dict[str, Any] | None = None
    delay_copy: list[dict[str, Any]] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "offset": self.offset,
            "scale": self.scale,
            "margin": self.margin,
            "peak1_offset_s": self.peak1_offset_s,
            "peak1_score": self.peak1_score,
            "peak2_offset_s": self.peak2_offset_s,
            "peak2_score": self.peak2_score,
            "best_offset_s_on": self.best_offset_s_on,
            "best_offset_s_ch": self.best_offset_s_ch,
            "residual_median_s": self.residual_median_s,
            "residual_max_s": self.residual_max_s,
            "windows_total": self.windows_total,
            "windows_used": self.windows_used,
            "windows_stuck_edge": self.windows_stuck_edge,
            "align_warning": self.align_warning,
            "warning_reasons": self.warning_reasons,
            "first_audio_onset_s": self.first_audio_onset_s,
            "local_segments": [asdict(s) for s in self.local_segments],
        }
        if self.render_check is not None:
            d["render_check"] = self.render_check
        if self.tail_check_s is not None:
            d["tail_check_s"] = self.tail_check_s
        if self.countin is not None:
            d["countin"] = self.countin
        if self.alignment_source is not None:
            d["alignment_source"] = self.alignment_source
        if self.compare_diff_ms is not None:
            d["compare_diff_ms"] = self.compare_diff_ms
        if self.midi_align is not None:
            d["midi_align"] = self.midi_align
        if self.render_align is not None:
            d["render_align"] = self.render_align
            off_r = self.render_align.get("offset_r")
            if off_r is not None:
                d["render_offset_r"] = off_r
        if self.content_mismatch_intervals:
            d["content_mismatch_intervals"] = self.content_mismatch_intervals
        if self.time_mismatch_intervals:
            d["time_mismatch_intervals"] = self.time_mismatch_intervals
        if self.delay_copy:
            d["delay_copy"] = self.delay_copy
        if self.audio_check is not None:
            d["audio_check"] = self.audio_check
        return d


@dataclass(frozen=True)
class RenderAlignWeights:
    w_on: float
    w_ch: float
    w_rms: float

    @classmethod
    def from_p1(cls, p1: dict[str, Any]) -> RenderAlignWeights:
        rt = p1.get("render_tool", {})
        return cls(
            w_on=float(rt.get("w_on", 0.4)),
            w_ch=float(rt.get("w_ch", 0.4)),
            w_rms=float(rt.get("w_rms", 0.2)),
        )


def hop_seconds(cfg: AlignConfig) -> float:
    return cfg.hop / cfg.sr


def load_audio_mono(path: Path | str, sr: int) -> np.ndarray:
    y, _ = librosa.load(path, sr=sr, mono=True)
    return y.astype(np.float64, copy=False)


def _normalize_chroma_frames(chroma: np.ndarray) -> np.ndarray:
    out = chroma.astype(np.float64, copy=True)
    norms = np.linalg.norm(out, axis=0)
    mask = norms > 1e-12
    out[:, mask] /= norms[mask]
    return out


def audio_onset_chroma(
    y: np.ndarray,
    cfg: AlignConfig,
    *,
    stage_times_s: dict[str, float] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    t0 = time.perf_counter()
    onset = librosa.onset.onset_strength(y=y, sr=cfg.sr, hop_length=cfg.hop)
    if stage_times_s is not None:
        stage_times_s["onset_strength"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    chroma = librosa.feature.chroma_stft(
        y=y,
        sr=cfg.sr,
        hop_length=cfg.hop,
        n_fft=cfg.chroma_n_fft,
    )
    chroma = _normalize_chroma_frames(chroma)
    if stage_times_s is not None:
        stage_times_s["chroma_stft"] = time.perf_counter() - t0
    n = min(onset.size, chroma.shape[1])
    onset = onset[:n]
    chroma = chroma[:, :n]
    return onset, chroma


def audio_rms_db_curve(y: np.ndarray, cfg: AlignConfig) -> np.ndarray:
    hop = cfg.hop
    n_frames = max(0, (y.size - hop) // hop + 1) if y.size else 0
    if n_frames == 0:
        return np.array([], dtype=np.float64)
    out = np.zeros(n_frames, dtype=np.float64)
    for i in range(n_frames):
        seg = y[i * hop : i * hop + hop]
        rms = float(np.sqrt(np.mean(seg * seg)))
        out[i] = 20.0 * np.log10(max(rms, 1e-12))
    return out


def audio_feature_streams(
    y: np.ndarray,
    cfg: AlignConfig,
    *,
    stage_times_s: dict[str, float] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    on, ch = audio_onset_chroma(y, cfg, stage_times_s=stage_times_s)
    rms = audio_rms_db_curve(y, cfg)
    n = min(on.size, ch.shape[1], rms.size)
    return on[:n], ch[:, :n], rms[:n]


def shift_feature_streams_to_midi_time(
    on: np.ndarray,
    ch: np.ndarray,
    rms: np.ndarray,
    latency_s: float,
    cfg: AlignConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """render 秒 → MIDI 秒に合わせる（MIDI = render − latency）。"""
    if abs(latency_s) <= 1e-9:
        return on, ch, rms
    hop_s = hop_seconds(cfg)
    shift_frames = int(round(latency_s / hop_s))
    if shift_frames > 0:
        z_on = np.zeros(shift_frames, dtype=np.float64)
        z_ch = np.zeros((12, shift_frames), dtype=np.float64)
        z_rms = np.full(shift_frames, rms[0] if rms.size else -120.0, dtype=np.float64)
        return (
            np.concatenate([z_on, on]),
            np.concatenate([z_ch, ch], axis=1),
            np.concatenate([z_rms, rms]),
        )
    if shift_frames < 0:
        drop = -shift_frames
        return on[drop:], ch[:, drop:], rms[drop:]
    return on, ch, rms


def _gaussian_kernel(sigma_bins: float, radius: int | None = None) -> np.ndarray:
    if sigma_bins <= 0:
        return np.array([1.0])
    if radius is None:
        radius = max(1, int(3.0 * sigma_bins))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    k = np.exp(-0.5 * (x / sigma_bins) ** 2)
    k /= k.sum()
    return k


def _blur_1d(x: np.ndarray, sigma_bins: float) -> np.ndarray:
    if sigma_bins <= 0:
        return x
    k = _gaussian_kernel(sigma_bins)
    return np.convolve(x, k, mode="same")


def alignment_notes(midi: KaraokeMidiData) -> list[KaraokeNote]:
    skip = GT_EXCLUDED_ROLES | frozenset({"chorus", "exclusive", "countin"})
    return [n for n in midi.notes if n.role not in skip]


def chroma_notes(midi: KaraokeMidiData) -> list[KaraokeNote]:
    return [n for n in midi.notes if n.role in CHROMA_ROLES]


def build_pulse_train(
    note_onsets_s: list[tuple[float, float]],
    n_frames: int,
    cfg: AlignConfig,
) -> np.ndarray:
    hop_s = hop_seconds(cfg)
    out = np.zeros(n_frames, dtype=np.float64)
    for t_s, weight in note_onsets_s:
        if t_s < 0:
            continue
        idx = int(round(t_s / hop_s))
        if 0 <= idx < n_frames:
            out[idx] += weight
    sigma_bins = cfg.blur_sigma_s / hop_s
    return _blur_1d(out, sigma_bins)


def build_midi_chroma(
    notes: list[KaraokeNote],
    n_frames: int,
    cfg: AlignConfig,
) -> np.ndarray:
    hop_s = hop_seconds(cfg)
    chroma = np.zeros((12, n_frames), dtype=np.float64)
    for n in notes:
        if n.role not in CHROMA_ROLES:
            continue
        pitch_class = int(n.pitch) % 12
        i0 = max(0, int(np.floor(n.start_s / hop_s)))
        i1 = min(n_frames, int(np.ceil(n.end_s / hop_s)) + 1)
        for i in range(i0, i1):
            chroma[pitch_class, i] += 1.0
    for i in range(n_frames):
        v = chroma[:, i]
        norm = np.linalg.norm(v)
        if norm > 1e-12:
            chroma[:, i] = v / norm
    return chroma


def notes_to_onsets(notes: list[KaraokeNote], cfg: AlignConfig) -> list[tuple[float, float]]:
    onsets: list[tuple[float, float]] = []
    for n in notes:
        w = cfg.drum_weight if n.role == "drums" else 1.0
        onsets.append((n.start_s, w))
    return onsets


def build_midi_feature_streams(
    midi: KaraokeMidiData,
    n_frames: int,
    cfg: AlignConfig,
) -> tuple[np.ndarray, np.ndarray, list[KaraokeNote], list[tuple[float, float]]]:
    """MIDI パルス・クロマ。feature_bias_s はここだけで MIDI 側時刻に加算する。"""
    align_notes = alignment_notes(midi)
    onsets = notes_to_onsets(align_notes, cfg)
    bias = cfg.feature_bias_s
    if bias != 0.0:
        onsets = [(t + bias, w) for t, w in onsets]
    midi_on = build_pulse_train(onsets, n_frames, cfg)
    cnotes = chroma_notes(midi)
    if bias != 0.0:
        cnotes = [
            replace(n, start_s=n.start_s + bias, end_s=n.end_s + bias) for n in cnotes
        ]
    midi_ch = build_midi_chroma(cnotes, n_frames, cfg)
    return midi_on, midi_ch, align_notes, onsets


def count_onsets_in_window(onsets: list[tuple[float, float]], t0: float, t1: float) -> int:
    return sum(1 for t, _ in onsets if t0 <= t < t1)


def count_strength_peaks_in_window(
    strength: np.ndarray,
    t0: float,
    t1: float,
    hop_s: float,
    *,
    min_fraction: float = 0.10,
) -> int:
    i0, i1 = _slice_frame_range(t0, t1, hop_s, strength.size)
    seg = strength[i0:i1]
    if seg.size == 0:
        return 0
    peak = float(seg.max())
    if peak <= 1e-12:
        return 0
    return int(np.sum(seg >= peak * min_fraction))


def audio_content_span_frames(strength: np.ndarray, hop_s: float) -> int:
    if strength.size == 0:
        return 1
    peak = float(strength.max())
    if peak <= 1e-12:
        return 1
    hits = np.flatnonzero(strength >= peak * 0.05)
    if hits.size == 0:
        return 1
    return max(1, int(hits[-1] - hits[0] + 1))


def _zscore(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    std = float(values.std())
    if std <= 1e-12:
        return np.zeros_like(values)
    return (values - values.mean()) / std


def overlap_frame_count(lag: int, nx: int, ny: int) -> int:
    """audio[lag+i] と midi[i] の重なりフレーム数。"""
    L = int(lag)
    if L >= 0:
        return max(0, min(ny, nx - L))
    n0 = -L
    return max(0, min(nx, ny + L))


def correlate_lag_index(lag: int, ny: int) -> int:
    """scipy.signal.correlate(audio, midi, mode='full') 上の lag 添字。"""
    return int(lag) + ny - 1


def lag_index_to_offset_s(lag_index: int | np.ndarray, hop_s: float) -> float | np.ndarray:
    """配列 lag → 秒。音源[i] と MIDI[i+lag] を重ねる（lag = i_audio − i_midi）。"""
    return np.asarray(lag_index, dtype=np.float64) * hop_s


def midi_content_span_frames(notes: list[KaraokeNote], hop_s: float) -> int:
    if not notes:
        return 1
    t0 = min(n.start_s for n in notes)
    t1 = max(n.end_s for n in notes)
    i0 = int(np.floor(t0 / hop_s))
    i1 = int(np.ceil(t1 / hop_s))
    return max(1, i1 - i0)


def _pearson_equal_segments(a: np.ndarray, b: np.ndarray) -> float:
    if a.size != b.size or a.size <= 1:
        return 0.0
    a = a - a.mean()
    b = b - b.mean()
    sa, sb = a.std(), b.std()
    if sa <= 1e-12 or sb <= 1e-12:
        return 0.0
    return float(np.mean(a / sa * b / sb))


def _chroma_dot_mean(ac: np.ndarray, mc: np.ndarray) -> float:
    if ac.shape[1] != mc.shape[1]:
        return 0.0
    dots: list[float] = []
    for i in range(ac.shape[1]):
        ai, mi = ac[:, i], mc[:, i]
        if np.linalg.norm(ai) <= 1e-12 or np.linalg.norm(mi) <= 1e-12:
            continue
        dots.append(float(np.dot(ai, mi)))
    if not dots:
        return 0.0
    return float(np.mean(dots))


def _pearson_curve_fft(
    x: np.ndarray,
    y: np.ndarray,
    lags: np.ndarray,
    *,
    min_overlap_frames: int = 1,
) -> np.ndarray:
    x = x.astype(np.float64, copy=False)
    y = y.astype(np.float64, copy=False)
    nx, ny = x.size, y.size
    if nx == 0 or ny == 0 or lags.size == 0:
        return np.zeros(lags.size, dtype=np.float64)
    sxy = correlate(x, y, mode="full", method="fft")
    csx = np.concatenate(([0.0], np.cumsum(x)))
    csx2 = np.concatenate(([0.0], np.cumsum(x * x)))
    csy = np.concatenate(([0.0], np.cumsum(y)))
    csy2 = np.concatenate(([0.0], np.cumsum(y * y)))
    out = np.zeros(lags.size, dtype=np.float64)
    for i, lag in enumerate(lags):
        L = int(lag)
        n = overlap_frame_count(L, nx, ny)
        if n < max(2, min_overlap_frames):
            continue
        idx = correlate_lag_index(L, ny)
        if idx < 0 or idx >= sxy.size:
            continue
        if L >= 0:
            sum_x = csx[L + n] - csx[L]
            sum_x2 = csx2[L + n] - csx2[L]
            sum_y = csy[n]
            sum_y2 = csy2[n]
        else:
            n0 = -L
            sum_x = csx[n]
            sum_x2 = csx2[n]
            sum_y = csy[n0 + n] - csy[n0]
            sum_y2 = csy2[n0 + n] - csy2[n0]
        sum_xy = sxy[idx]
        cov = sum_xy - sum_x * sum_y / n
        var_x = sum_x2 - sum_x * sum_x / n
        var_y = sum_y2 - sum_y * sum_y / n
        if var_x <= 1e-24 or var_y <= 1e-24:
            continue
        out[i] = cov / np.sqrt(var_x * var_y)
    return out


def _chroma_score_curve_fft(
    audio_ch: np.ndarray,
    midi_ch: np.ndarray,
    lags: np.ndarray,
    *,
    min_overlap_frames: int = 1,
) -> np.ndarray:
    nx = audio_ch.shape[1]
    ny = midi_ch.shape[1]
    if nx == 0 or ny == 0 or lags.size == 0:
        return np.zeros(lags.size, dtype=np.float64)
    has_a = (np.linalg.norm(audio_ch, axis=0) > 1e-12).astype(np.float64)
    has_m = (np.linalg.norm(midi_ch, axis=0) > 1e-12).astype(np.float64)
    dot_full = np.zeros(nx + ny - 1, dtype=np.float64)
    for pc in range(audio_ch.shape[0]):
        dot_full += correlate(audio_ch[pc], midi_ch[pc], mode="full", method="fft")
    both = correlate(has_a, has_m, mode="full", method="fft")
    out = np.zeros(lags.size, dtype=np.float64)
    for i, lag in enumerate(lags):
        L = int(lag)
        n = overlap_frame_count(L, nx, ny)
        if n < max(1, min_overlap_frames):
            continue
        idx = correlate_lag_index(L, ny)
        if idx < 0 or idx >= both.size:
            continue
        n_both = float(both[idx])
        if n_both <= 0.5:
            continue
        out[i] = dot_full[idx] / n_both
    return out


def score_curve(
    audio_on: np.ndarray,
    audio_ch: np.ndarray,
    midi_on: np.ndarray,
    midi_ch: np.ndarray,
    cfg: AlignConfig,
    *,
    search_min_s: float | None = None,
    search_max_s: float | None = None,
    midi_span_frames: int | None = None,
    ref_rms: np.ndarray | None = None,
    mov_rms: np.ndarray | None = None,
    score_weights: RenderAlignWeights | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    hop_s = hop_seconds(cfg)
    lo = cfg.search_min_s if search_min_s is None else search_min_s
    hi = cfg.search_max_s if search_max_s is None else search_max_s
    lag_min = int(np.floor(lo / hop_s))
    lag_max = int(np.ceil(hi / hop_s))
    lags = np.arange(lag_min, lag_max + 1, dtype=np.int64)
    offsets = lag_index_to_offset_s(lags, hop_s)
    span = midi_span_frames if midi_span_frames is not None else midi_on.size
    min_ov = int(np.ceil(cfg.min_overlap_ratio * span))
    s_on = _pearson_curve_fft(
        audio_on, midi_on, lags, min_overlap_frames=min_ov
    )
    s_ch = _chroma_score_curve_fft(
        audio_ch, midi_ch, lags, min_overlap_frames=min_ov
    )
    s_on_n = _zscore(s_on)
    s_ch_n = _zscore(s_ch)
    w_on = cfg.w_on
    w_ch = cfg.w_ch
    w_rms = 0.0
    if score_weights is not None:
        w_on, w_ch, w_rms = score_weights.w_on, score_weights.w_ch, score_weights.w_rms
    combined = w_on * s_on_n + w_ch * s_ch_n
    if (
        w_rms > 0.0
        and ref_rms is not None
        and mov_rms is not None
        and ref_rms.size == audio_on.size
        and mov_rms.size == midi_on.size
    ):
        s_rms = _pearson_curve_fft(
            ref_rms, mov_rms, lags, min_overlap_frames=min_ov
        )
        combined = combined + w_rms * _zscore(s_rms)
    nx, ny = audio_on.size, midi_on.size
    for i, lag in enumerate(lags):
        if overlap_frame_count(int(lag), nx, ny) < min_ov:
            combined[i] = -np.inf
    return offsets, s_on, s_ch, combined, lags


def _slice_frame_range(t0_s: float, t1_s: float, hop_s: float, n_max: int) -> tuple[int, int]:
    i0 = max(0, int(np.floor(t0_s / hop_s)))
    i1 = min(n_max, int(np.ceil(t1_s / hop_s)))
    return i0, max(i0, i1)


def score_refined_offset_in_window(
    audio_on: np.ndarray,
    audio_ch: np.ndarray,
    midi_on: np.ndarray,
    midi_ch: np.ndarray,
    cfg: AlignConfig,
    *,
    t0_s: float,
    t1_s: float,
    scale: float,
    offset_s: float,
    search_range_s: float,
    ref_rms: np.ndarray | None = None,
    mov_rms: np.ndarray | None = None,
    score_weights: RenderAlignWeights | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """MIDI 区間 [t0_s,t1_s) と音源の切り出し（局所・曲尻共通）。

    音源の秒 = MIDIの秒 × scale + offset_s より、MIDI フレーム mi0 の先頭は
    音源フレーム round(scale × mi0 + offset_s / hop_s)（lag 添字は lag_index_to_offset_s）。
    候補 offset は offset_s ± search_range_s を rel_lag フレーム刻みで試す。
    """
    hop_s = hop_seconds(cfg)
    dr_min = int(np.floor(-search_range_s / hop_s))
    dr_max = int(np.ceil(search_range_s / hop_s))
    rel_lags = np.arange(dr_min, dr_max + 1, dtype=np.int64)

    mi0, mi1 = _slice_frame_range(t0_s, t1_s, hop_s, midi_on.size)
    if mi1 <= mi0:
        return np.array([]), np.array([])
    m_on = midi_on[mi0:mi1]
    m_ch = midi_ch[:, mi0:mi1]
    w = m_on.size

    s_on = np.zeros(rel_lags.size, dtype=np.float64)
    s_ch = np.zeros(rel_lags.size, dtype=np.float64)
    s_rms = np.zeros(rel_lags.size, dtype=np.float64)
    m_rms = mov_rms[mi0:mi1] if mov_rms is not None and mov_rms.size >= mi1 else None
    for i, dr in enumerate(rel_lags):
        delta_s = float(lag_index_to_offset_s(int(dr), hop_s))
        o_cand = offset_s + delta_s
        a0 = int(round(scale * float(mi0) + o_cand / hop_s))
        a1 = a0 + w
        if a0 < 0 or a1 > audio_on.size:
            continue
        a_seg = audio_on[a0:a1]
        ac_seg = audio_ch[:, a0:a1]
        if a_seg.size != w:
            continue
        s_on[i] = _pearson_equal_segments(a_seg, m_on)
        s_ch[i] = _chroma_dot_mean(ac_seg, m_ch)
        if m_rms is not None and ref_rms is not None:
            a_rms = ref_rms[a0:a1]
            if a_rms.size == w:
                s_rms[i] = _pearson_equal_segments(a_rms, m_rms)

    s_on_n = _zscore(s_on)
    s_ch_n = _zscore(s_ch)
    w_on = cfg.w_on
    w_ch = cfg.w_ch
    w_rms = 0.0
    if score_weights is not None:
        w_on, w_ch, w_rms = score_weights.w_on, score_weights.w_ch, score_weights.w_rms
    combined = w_on * s_on_n + w_ch * s_ch_n
    if w_rms > 0.0 and mov_rms is not None and ref_rms is not None:
        combined = combined + w_rms * _zscore(s_rms)
    offsets = offset_s + lag_index_to_offset_s(rel_lags, hop_s)
    return offsets, combined


def pick_peaks(
    offsets: np.ndarray,
    scores: np.ndarray,
    cfg: AlignConfig,
) -> tuple[tuple[float, float], tuple[float, float] | None]:
    if offsets.size == 0:
        return (0.0, 0.0), None
    i1 = int(np.argmax(scores))
    peak1 = (float(offsets[i1]), float(scores[i1]))
    peak2: tuple[float, float] | None = None
    order = np.argsort(scores)[::-1]
    for idx in order:
        if idx == i1:
            continue
        if abs(float(offsets[idx]) - peak1[0]) <= cfg.second_peak_exclude_s:
            continue
        peak2 = (float(offsets[idx]), float(scores[idx]))
        break
    return peak1, peak2


def iter_midi_bar_windows_from_m2(
    midi: KaraokeMidiData,
    bars: int,
    *,
    max_time_s: float | None = None,
) -> list[tuple[float, float, float]]:
    tpb = midi.ticks_per_beat
    start_tick = m2_start_tick(midi.time_signatures, tpb)
    end_s = midi.duration_s + 1.0
    if max_time_s is not None:
        end_s = min(end_s, max_time_s)
    max_tick = second_to_tick(end_s, midi.tempos, tpb)
    tick = start_tick
    windows: list[tuple[float, float, float]] = []
    while tick < max_tick:
        ts = time_signature_at_tick(tick, midi.time_signatures)
        span = measure_length_ticks(ts, tpb) * bars
        end_tick = min(tick + span, max_tick)
        if end_tick <= tick:
            break
        center_tick = (tick + end_tick) // 2
        t0 = tick_to_second(tick, midi.tempos, tpb)
        t1 = tick_to_second(end_tick, midi.tempos, tpb)
        c = tick_to_second(center_tick, midi.tempos, tpb)
        windows.append((t0, t1, c))
        tick = end_tick
    return windows


def render_rpp_item_times(offset_r: float, mp3_length_s: float) -> tuple[float, float, float]:
    position = max(0.0, offset_r)
    soffs = max(0.0, -offset_r)
    length = mp3_length_s - position
    return position, soffs, length


def render_timeline_to_midi_align(
    offset_r: float,
    scale_r: float,
    latency_s: float,
) -> tuple[float, float]:
    """mp3 = scale_r × render + offset_r、MIDI = render − latency → mp3 = scale_r × MIDI + (…)."""
    return scale_r, scale_r * latency_s + offset_r


def mp3_time_from_midi_align(t_s: float, scale: float, offset: float) -> float:
    return scale * t_s + offset


def mp3_time_from_render_align(
    midi_t_s: float,
    scale_r: float,
    offset_r: float,
    latency_s: float,
) -> float:
    return scale_r * (midi_t_s + latency_s) + offset_r


def three_point_midi_times(midi: KaraokeMidiData) -> tuple[float, float, float]:
    tpb = midi.ticks_per_beat
    sigs = midi.time_signatures
    start_tick = m2_start_tick(sigs, tpb)
    head = tick_to_second(start_tick, midi.tempos, tpb)
    tail_w = tail_bar_window(midi, TAIL_WINDOW_BARS)
    if tail_w is not None:
        tail = tail_w[2]
    else:
        tail = max(head, midi.duration_s * 0.9)
    mid = max(head, min(tail, 0.5 * (head + midi.duration_s)))
    return head, mid, tail


def compare_align_at_three_points(
    midi: KaraokeMidiData,
    *,
    midi_scale: float,
    midi_offset: float,
    render_scale: float,
    render_offset_r: float,
    latency_s: float,
    tol_s: float,
) -> tuple[list[float], bool]:
    diffs_ms: list[float] = []
    for t in three_point_midi_times(midi):
        mp3_m = mp3_time_from_midi_align(t, midi_scale, midi_offset)
        mp3_r = mp3_time_from_render_align(t, render_scale, render_offset_r, latency_s)
        diffs_ms.append(abs(mp3_m - mp3_r) * 1000.0)
    confirmed = all(d <= tol_s * 1000.0 for d in diffs_ms)
    return diffs_ms, confirmed


def align_result_summary_dict(result: AlignResult) -> dict[str, Any]:
    return {
        "offset": result.offset,
        "scale": result.scale,
        "margin": result.margin,
        "peak1_offset_s": result.peak1_offset_s,
        "peak1_score": result.peak1_score,
        "peak2_offset_s": result.peak2_offset_s,
        "peak2_score": result.peak2_score,
        "best_offset_s_on": result.best_offset_s_on,
        "best_offset_s_ch": result.best_offset_s_ch,
        "residual_median_s": result.residual_median_s,
        "residual_max_s": result.residual_max_s,
        "windows_total": result.windows_total,
        "windows_used": result.windows_used,
        "windows_stuck_edge": result.windows_stuck_edge,
        "align_warning": result.align_warning,
        "warning_reasons": result.warning_reasons,
        "tail_check_s": result.tail_check_s,
    }


def fit_scale_offset_huber(
    centers: np.ndarray,
    offsets: np.ndarray,
) -> tuple[float, float, np.ndarray]:
    """L(c) ≈ (scale−1)*c + offset  →  L = alpha*c + beta, scale = 1+alpha."""
    if centers.size < 2:
        return 1.0, float(offsets[0]) if offsets.size else 0.0, np.zeros(0)
    reg = HuberRegressor(fit_intercept=True)
    reg.fit(centers.reshape(-1, 1), offsets)
    alpha = float(reg.coef_[0])
    intercept = float(reg.intercept_)
    scale = 1.0 + alpha
    pred = alpha * centers + intercept
    return scale, intercept, offsets - pred


def align_midi_to_audio(
    midi: KaraokeMidiData,
    audio_on: np.ndarray,
    audio_ch: np.ndarray,
    cfg: AlignConfig,
    *,
    stage_times_s: dict[str, float] | None = None,
) -> AlignResult:
    hop_s = hop_seconds(cfg)
    n_frames = audio_on.size
    midi_on, midi_ch, align_notes, _onsets_feat = build_midi_feature_streams(
        midi, n_frames, cfg
    )
    onsets = notes_to_onsets(align_notes, cfg)

    span_frames = midi_content_span_frames(align_notes, hop_s)
    t_global = time.perf_counter()
    offsets, s_on, s_ch, combined, _lags = score_curve(
        audio_on,
        audio_ch,
        midi_on,
        midi_ch,
        cfg,
        midi_span_frames=span_frames,
    )
    peak1, peak2 = pick_peaks(offsets, combined, cfg)
    global_offset = peak1[0]
    margin = peak1[1] - peak2[1] if peak2 is not None else peak1[1]
    best_on = float(offsets[int(np.argmax(s_on))]) if s_on.size else global_offset
    best_ch = float(offsets[int(np.argmax(s_ch))]) if s_ch.size else global_offset

    warnings: list[str] = []
    if peak2 is not None and margin < cfg.margin_min:
        warnings.append("ambiguous_offset")
    if stage_times_s is not None:
        stage_times_s["global"] = time.perf_counter() - t_global

    audio_duration_s = n_frames * hop_s
    windows = iter_midi_bar_windows_from_m2(
        midi, cfg.local_window_bars, max_time_s=audio_duration_s
    )
    segments: list[LocalAlignSegment] = []
    fit_centers: list[float] = []
    fit_offsets: list[float] = []
    stuck = 0

    t_local = time.perf_counter()
    for t0, t1, c in windows:
        n_on = count_onsets_in_window(onsets, t0, t1)
        if n_on < cfg.min_onsets_per_window:
            segments.append(
                LocalAlignSegment(
                    t0, t1, c, global_offset, False, skip_reason="too_few_onsets"
                )
            )
            continue
        loc_offsets, loc_combined = score_refined_offset_in_window(
            audio_on,
            audio_ch,
            midi_on,
            midi_ch,
            cfg,
            t0_s=t0,
            t1_s=t1,
            scale=1.0,
            offset_s=global_offset,
            search_range_s=cfg.local_range_s,
        )
        if loc_combined.size == 0:
            segments.append(
                LocalAlignSegment(t0, t1, c, global_offset, False, skip_reason="no_score")
            )
            continue
        li = int(np.argmax(loc_combined))
        local_off = float(loc_offsets[li])
        lo = global_offset - cfg.local_range_s
        hi = global_offset + cfg.local_range_s
        stuck_edge = (
            abs(local_off - lo) <= cfg.edge_tol_s or abs(local_off - hi) <= cfg.edge_tol_s
        )
        if stuck_edge:
            stuck += 1
        used = not stuck_edge
        segments.append(
            LocalAlignSegment(
                t0,
                t1,
                c,
                local_off,
                used,
                skip_reason="stuck_edge" if stuck_edge else None,
                stuck_edge=stuck_edge,
            )
        )
        if used:
            fit_centers.append(c)
            fit_offsets.append(local_off)
    if stage_times_s is not None:
        stage_times_s["local"] = time.perf_counter() - t_local

    scale = 1.0
    offset = global_offset
    residual_median: float | None = None
    residual_max: float | None = None

    t_fit = time.perf_counter()
    if len(fit_centers) >= cfg.min_windows:
        c_arr = np.array(fit_centers, dtype=np.float64)
        o_arr = np.array(fit_offsets, dtype=np.float64)
        scale, offset, resid = fit_scale_offset_huber(c_arr, o_arr)
        if resid.size:
            abs_r = np.abs(resid)
            residual_median = float(np.median(abs_r))
            residual_max = float(np.max(abs_r))
    else:
        warnings.append("too_few_windows")
        scale = 1.0
        offset = global_offset
    if stage_times_s is not None:
        stage_times_s["fit"] = time.perf_counter() - t_fit

    if abs(scale - 1.0) >= cfg.warn_scale_dev:
        warnings.append(f"scale_deviation:{scale:.6f}")
    if residual_median is not None and residual_median > cfg.warn_residual_s:
        warnings.append(f"residual_median:{residual_median:.4f}")
    if windows and stuck / len(windows) > cfg.warn_edge_ratio:
        warnings.append(f"edge_stuck_ratio:{stuck}/{len(windows)}")

    tail_check_s = compute_tail_check_s(
        midi,
        audio_on,
        audio_ch,
        midi_on,
        midi_ch,
        cfg,
        scale=scale,
        offset=offset,
    )
    if abs(tail_check_s) > TAIL_MISMATCH_THRESHOLD_S:
        warnings.append("tail_mismatch")

    first_onset = first_onset_above_fraction(audio_on, hop_s, 0.10)

    result = AlignResult(
        offset=offset,
        scale=scale,
        margin=float(margin),
        peak1_offset_s=peak1[0],
        peak1_score=peak1[1],
        peak2_offset_s=peak2[0] if peak2 else None,
        peak2_score=peak2[1] if peak2 else None,
        best_offset_s_on=best_on,
        best_offset_s_ch=best_ch,
        residual_median_s=residual_median,
        residual_max_s=residual_max,
        windows_total=len(windows),
        windows_used=len(fit_centers),
        windows_stuck_edge=stuck,
        local_segments=segments,
        align_warning=bool(warnings),
        warning_reasons=warnings,
        first_audio_onset_s=first_onset,
        tail_check_s=tail_check_s,
    )

    return result


def align_mp3_to_render(
    midi: KaraokeMidiData,
    mp3_on: np.ndarray,
    mp3_ch: np.ndarray,
    mp3_rms: np.ndarray,
    render_on: np.ndarray,
    render_ch: np.ndarray,
    render_rms: np.ndarray,
    cfg: AlignConfig,
    weights: RenderAlignWeights,
    *,
    stage_times_s: dict[str, float] | None = None,
) -> AlignResult:
    """原曲 mp3 と render.wav（MIDI 時刻に揃えた特徴）の位置合わせ。"""
    hop_s = hop_seconds(cfg)
    n_frames = min(mp3_on.size, render_on.size, mp3_rms.size, render_rms.size)
    mp3_on = mp3_on[:n_frames]
    mp3_ch = mp3_ch[:, :n_frames]
    mp3_rms = mp3_rms[:n_frames]
    render_on = render_on[:n_frames]
    render_ch = render_ch[:, :n_frames]
    render_rms = render_rms[:n_frames]

    span_frames = audio_content_span_frames(render_on, hop_s)
    t_global = time.perf_counter()
    offsets, s_on, s_ch, combined, _lags = score_curve(
        mp3_on,
        mp3_ch,
        render_on,
        render_ch,
        cfg,
        midi_span_frames=span_frames,
        ref_rms=mp3_rms,
        mov_rms=render_rms,
        score_weights=weights,
    )
    peak1, peak2 = pick_peaks(offsets, combined, cfg)
    global_offset = peak1[0]
    margin = peak1[1] - peak2[1] if peak2 is not None else peak1[1]
    best_on = float(offsets[int(np.argmax(s_on))]) if s_on.size else global_offset
    best_ch = float(offsets[int(np.argmax(s_ch))]) if s_ch.size else global_offset

    warnings: list[str] = []
    if peak2 is not None and margin < cfg.margin_min:
        warnings.append("ambiguous_offset")
    if stage_times_s is not None:
        stage_times_s["render_global"] = time.perf_counter() - t_global

    audio_duration_s = n_frames * hop_s
    windows = iter_midi_bar_windows_from_m2(
        midi, cfg.local_window_bars, max_time_s=audio_duration_s
    )
    segments: list[LocalAlignSegment] = []
    fit_centers: list[float] = []
    fit_offsets: list[float] = []
    stuck = 0

    t_local = time.perf_counter()
    for t0, t1, c in windows:
        n_on = count_strength_peaks_in_window(render_on, t0, t1, hop_s)
        if n_on < cfg.min_onsets_per_window:
            segments.append(
                LocalAlignSegment(
                    t0, t1, c, global_offset, False, skip_reason="too_few_onsets"
                )
            )
            continue
        loc_offsets, loc_combined = score_refined_offset_in_window(
            mp3_on,
            mp3_ch,
            render_on,
            render_ch,
            cfg,
            t0_s=t0,
            t1_s=t1,
            scale=1.0,
            offset_s=global_offset,
            search_range_s=cfg.local_range_s,
            ref_rms=mp3_rms,
            mov_rms=render_rms,
            score_weights=weights,
        )
        if loc_combined.size == 0:
            segments.append(
                LocalAlignSegment(t0, t1, c, global_offset, False, skip_reason="no_score")
            )
            continue
        li = int(np.argmax(loc_combined))
        local_off = float(loc_offsets[li])
        lo = global_offset - cfg.local_range_s
        hi = global_offset + cfg.local_range_s
        stuck_edge = (
            abs(local_off - lo) <= cfg.edge_tol_s or abs(local_off - hi) <= cfg.edge_tol_s
        )
        if stuck_edge:
            stuck += 1
        used = not stuck_edge
        segments.append(
            LocalAlignSegment(
                t0,
                t1,
                c,
                local_off,
                used,
                skip_reason="stuck_edge" if stuck_edge else None,
                stuck_edge=stuck_edge,
            )
        )
        if used:
            fit_centers.append(c)
            fit_offsets.append(local_off)
    if stage_times_s is not None:
        stage_times_s["render_local"] = time.perf_counter() - t_local

    scale = 1.0
    offset = global_offset
    residual_median: float | None = None
    residual_max: float | None = None

    t_fit = time.perf_counter()
    if len(fit_centers) >= cfg.min_windows:
        c_arr = np.array(fit_centers, dtype=np.float64)
        o_arr = np.array(fit_offsets, dtype=np.float64)
        scale, offset, resid = fit_scale_offset_huber(c_arr, o_arr)
        if resid.size:
            abs_r = np.abs(resid)
            residual_median = float(np.median(abs_r))
            residual_max = float(np.max(abs_r))
    else:
        warnings.append("too_few_windows")
        scale = 1.0
        offset = global_offset
    if stage_times_s is not None:
        stage_times_s["render_fit"] = time.perf_counter() - t_fit

    if abs(scale - 1.0) >= cfg.warn_scale_dev:
        warnings.append(f"scale_deviation:{scale:.6f}")
    if residual_median is not None and residual_median > cfg.warn_residual_s:
        warnings.append(f"residual_median:{residual_median:.4f}")
    if windows and stuck / len(windows) > cfg.warn_edge_ratio:
        warnings.append(f"edge_stuck_ratio:{stuck}/{len(windows)}")

    tail_check_s = compute_tail_check_s(
        midi,
        mp3_on,
        mp3_ch,
        render_on,
        render_ch,
        cfg,
        scale=scale,
        offset=offset,
    )
    if abs(tail_check_s) > TAIL_MISMATCH_THRESHOLD_S:
        warnings.append("tail_mismatch")

    first_onset = first_onset_above_fraction(mp3_on, hop_s, 0.10)

    return AlignResult(
        offset=offset,
        scale=scale,
        margin=float(margin),
        peak1_offset_s=peak1[0],
        peak1_score=peak1[1],
        peak2_offset_s=peak2[0] if peak2 else None,
        peak2_score=peak2[1] if peak2 else None,
        best_offset_s_on=best_on,
        best_offset_s_ch=best_ch,
        residual_median_s=residual_median,
        residual_max_s=residual_max,
        windows_total=len(windows),
        windows_used=len(fit_centers),
        windows_stuck_edge=stuck,
        local_segments=segments,
        align_warning=bool(warnings),
        warning_reasons=warnings,
        first_audio_onset_s=first_onset,
        tail_check_s=tail_check_s,
    )


def first_onset_above_fraction(strength: np.ndarray, hop_s: float, fraction: float) -> float:
    if strength.size == 0:
        return 0.0
    peak = float(strength.max())
    if peak <= 1e-12:
        return 0.0
    thr = peak * fraction
    hits = np.flatnonzero(strength >= thr)
    if hits.size == 0:
        return 0.0
    return float(hits[0]) * hop_s


def transform_time(t_s: float, result: AlignResult) -> float:
    return result.scale * t_s + result.offset


def aligned_eval_notes(
    midi: KaraokeMidiData,
    result: AlignResult,
    *,
    exclude_track_names: frozenset[str] | None = None,
) -> list[EvalNote]:
    excluded = exclude_track_names or frozenset()
    out: list[EvalNote] = []
    for n in midi.notes:
        if n.track_name in excluded:
            continue
        if n.role not in EXPORT_ROLES:
            continue
        out.append(
            EvalNote(
                role=n.role,
                pitch=n.pitch,
                start_s=transform_time(n.start_s, result),
                end_s=transform_time(n.end_s, result),
                drum_class=n.drum_class,
            )
        )
    return out


def eval_exclude_note(start_aligned_s: float, result: AlignResult) -> bool:
    return start_aligned_s < 0.0


def last_note_measure(midi: KaraokeMidiData) -> int | None:
    tpb = midi.ticks_per_beat
    sigs = midi.time_signatures
    ticks = [
        second_to_tick(n.start_s, midi.tempos, tpb)
        for n in midi.notes
        if n.role not in GT_EXCLUDED_ROLES and n.role != "countin"
    ]
    if not ticks:
        return None
    return tick_to_measure_number(max(ticks), sigs, tpb)


def tail_bar_window(
    midi: KaraokeMidiData,
    bars: int = TAIL_WINDOW_BARS,
) -> tuple[float, float, float] | None:
    last_m = last_note_measure(midi)
    if last_m is None:
        return None
    tpb = midi.ticks_per_beat
    sigs = midi.time_signatures
    first_m = max(1, last_m - bars + 1)
    t0_tick = measure_start_tick(first_m, sigs, tpb)
    t1_tick = measure_start_tick(last_m + 1, sigs, tpb)
    t0 = tick_to_second(t0_tick, midi.tempos, tpb)
    t1 = tick_to_second(t1_tick, midi.tempos, tpb)
    center = 0.5 * (t0 + t1)
    return t0, t1, center


def predicted_offset_at_midi_time(c_s: float, scale: float, offset: float) -> float:
    return offset + (scale - 1.0) * c_s


def compute_tail_check_s(
    midi: KaraokeMidiData,
    audio_on: np.ndarray,
    audio_ch: np.ndarray,
    midi_on: np.ndarray,
    midi_ch: np.ndarray,
    cfg: AlignConfig,
    *,
    scale: float,
    offset: float,
) -> float:
    window = tail_bar_window(midi, TAIL_WINDOW_BARS)
    if window is None:
        return 0.0
    t0, t1, center = window
    predicted = predicted_offset_at_midi_time(center, scale, offset)
    loc_offsets, loc_combined = score_refined_offset_in_window(
        audio_on,
        audio_ch,
        midi_on,
        midi_ch,
        cfg,
        t0_s=t0,
        t1_s=t1,
        scale=scale,
        offset_s=offset,
        search_range_s=TAIL_SEARCH_S,
    )
    if loc_combined.size == 0:
        return 0.0
    li = int(np.argmax(loc_combined))
    found = float(loc_offsets[li])
    return found - predicted


def align_song_paths(
    audio_path: Path,
    midi_path: Path,
    cfg: AlignConfig,
) -> tuple[AlignResult, KaraokeMidiData, np.ndarray]:
    mp3_y = load_audio_mono(audio_path, cfg.sr)
    audio_on, audio_ch = audio_onset_chroma(mp3_y, cfg)
    midi = load_karaoke_midi(midi_path)
    result = align_midi_to_audio(
        midi,
        audio_on,
        audio_ch,
        cfg,
    )
    return result, midi, mp3_y


def write_align_json(path: Path, result: AlignResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.to_json_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def onset_strength_curve(y: np.ndarray, cfg: AlignConfig) -> np.ndarray:
    on, _ = audio_onset_chroma(y, cfg)
    return on
