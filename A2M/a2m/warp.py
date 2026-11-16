"""MIDI → 音源の時間ワープ（仕様 第3.2節：local 既定、dtw オプション）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from a2m.align import (
    AlignConfig,
    AlignResult,
    build_midi_feature_streams,
    hop_seconds,
)

INF = 1e18


@dataclass(frozen=True)
class WarpConfig:
    method: str
    step_s: float
    win_s: float
    range_s: float
    peak_margin: float
    median_pts: int
    max_dev_s: float
    band_s: float
    median_s: float
    slope_min: float
    slope_max: float
    w_chroma: float
    w_onset: float
    ok_median_ms: float
    ok_max_ms: float
    conf_min: float

    @classmethod
    def from_dict(cls, p1: dict[str, Any]) -> WarpConfig:
        w = p1.get("warp", {})
        return cls(
            method=str(w.get("method", "local")).lower(),
            step_s=float(w.get("step_s", 2.0)),
            win_s=float(w.get("win_s", 8.0)),
            range_s=float(w.get("range_s", 0.3)),
            peak_margin=float(w.get("peak_margin", 0.15)),
            median_pts=int(w.get("median_pts", 5)),
            max_dev_s=float(w.get("max_dev_s", 0.3)),
            band_s=float(w.get("band_s", 1.0)),
            median_s=float(w.get("median_s", 2.0)),
            slope_min=float(w.get("slope_min", 0.98)),
            slope_max=float(w.get("slope_max", 1.02)),
            w_chroma=float(w.get("w_chroma", 0.7)),
            w_onset=float(w.get("w_onset", 0.3)),
            ok_median_ms=float(w.get("ok_median_ms", 20.0)),
            ok_max_ms=float(w.get("ok_max_ms", 50.0)),
            conf_min=float(w.get("conf_min", 0.3)),
        )


@dataclass
class WarpResult:
    hop_s: float
    midi_times_s: np.ndarray
    audio_times_s: np.ndarray
    linear_offset: float
    linear_scale: float
    used_warp: bool
    warnings: list[str] = field(default_factory=list)
    residual_median_ms: float | None = None
    residual_max_ms: float | None = None
    linear_residual_median_ms: float | None = None
    linear_residual_max_ms: float | None = None
    linear_n_over_20ms: int | None = None
    warp_n_over_20ms: int | None = None
    countin_end_s: float = 0.0
    compute_time_s: float | None = None
    warp_method: str | None = None
    lag_points_total: int | None = None
    lag_points_used: int | None = None
    lag_points: list[dict[str, Any]] = field(default_factory=list)
    rejection_stats: dict[str, int] = field(default_factory=dict)
    content_mismatch_intervals: list[dict[str, Any]] = field(default_factory=list)
    time_mismatch_intervals: list[dict[str, Any]] = field(default_factory=list)
    eval_residual_median_ms: float | None = None
    eval_residual_max_ms: float | None = None

    def midi_at_audio(self, t_audio_s: float) -> float:
        """採譜の秒（音声）を、納品MIDIの秒へ戻す。"""
        scale = self.linear_scale if abs(self.linear_scale) > 1e-9 else 1.0
        return (float(t_audio_s) - self.linear_offset) / scale

    def audio_at_midi(self, t_midi_s: float) -> float:
        if self.midi_times_s.size == 0:
            return self.linear_scale * t_midi_s + self.linear_offset
        return float(
            np.interp(
                t_midi_s,
                self.midi_times_s,
                self.audio_times_s,
                left=self.audio_times_s[0],
                right=self.audio_times_s[-1],
            )
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "hop_s": self.hop_s,
            "midi_times_s": self.midi_times_s.tolist(),
            "audio_times_s": self.audio_times_s.tolist(),
            "linear_offset": self.linear_offset,
            "linear_scale": self.linear_scale,
            "used_warp": self.used_warp,
            "warnings": self.warnings,
            "residual_median_ms": self.residual_median_ms,
            "residual_max_ms": self.residual_max_ms,
            "linear_residual_median_ms": self.linear_residual_median_ms,
            "linear_residual_max_ms": self.linear_residual_max_ms,
            "linear_n_over_20ms": self.linear_n_over_20ms,
            "warp_n_over_20ms": self.warp_n_over_20ms,
            "countin_end_s": self.countin_end_s,
            "compute_time_s": self.compute_time_s,
            "warp_method": self.warp_method,
            "lag_points_total": self.lag_points_total,
            "lag_points_used": self.lag_points_used,
            "lag_points": self.lag_points,
            "rejection_stats": self.rejection_stats,
            "content_mismatch_intervals": self.content_mismatch_intervals,
            "time_mismatch_intervals": self.time_mismatch_intervals,
            "eval_residual_median_ms": self.eval_residual_median_ms,
            "eval_residual_max_ms": self.eval_residual_max_ms,
        }

    @classmethod
    def from_json_dict(cls, d: dict[str, Any]) -> WarpResult:
        return cls(
            hop_s=float(d["hop_s"]),
            midi_times_s=np.array(d["midi_times_s"], dtype=np.float64),
            audio_times_s=np.array(d["audio_times_s"], dtype=np.float64),
            linear_offset=float(d["linear_offset"]),
            linear_scale=float(d["linear_scale"]),
            used_warp=bool(d.get("used_warp", True)),
            warnings=list(d.get("warnings", [])),
            residual_median_ms=d.get("residual_median_ms"),
            residual_max_ms=d.get("residual_max_ms"),
            linear_residual_median_ms=d.get("linear_residual_median_ms"),
            linear_residual_max_ms=d.get("linear_residual_max_ms"),
            linear_n_over_20ms=d.get("linear_n_over_20ms"),
            warp_n_over_20ms=d.get("warp_n_over_20ms"),
            countin_end_s=float(d.get("countin_end_s", 0.0)),
            compute_time_s=d.get("compute_time_s"),
            warp_method=d.get("warp_method"),
            lag_points_total=d.get("lag_points_total"),
            lag_points_used=d.get("lag_points_used"),
            lag_points=list(d.get("lag_points", [])),
            rejection_stats=dict(d.get("rejection_stats", {})),
            content_mismatch_intervals=list(d.get("content_mismatch_intervals", [])),
            time_mismatch_intervals=list(d.get("time_mismatch_intervals", [])),
            eval_residual_median_ms=d.get("eval_residual_median_ms"),
            eval_residual_max_ms=d.get("eval_residual_max_ms"),
        )


def build_warp_feature_matrix(on: np.ndarray, ch: np.ndarray) -> np.ndarray:
    n = min(on.size, ch.shape[1])
    on = on[:n].astype(np.float64, copy=False)
    ch = ch[:, :n].astype(np.float64, copy=False)
    peak = float(on.max())
    if peak > 1e-12:
        on = on / peak
    out = np.zeros((n, 13), dtype=np.float64)
    out[:, :12] = ch.T
    out[:, 12] = on
    return out


def _frame_distance(a: np.ndarray, b: np.ndarray, cfg: WarpConfig) -> float:
    cos_ch = float(np.dot(a[:12], b[:12]))
    sim = cfg.w_chroma * cos_ch + cfg.w_onset * float(a[12] * b[12])
    return 1.0 - sim


def _band_j_range(
    i: int,
    hop_s: float,
    scale: float,
    offset: float,
    band_s: float,
    m_max: int,
) -> tuple[int, int]:
    if m_max <= 0:
        return 0, -1
    t_mp3 = i * hop_s
    if abs(scale) <= 1e-12:
        scale = 1.0
    j_center = (t_mp3 - offset) / scale / hop_s
    half = band_s / abs(scale) / hop_s
    j0 = int(np.floor(j_center - half))
    j1 = int(np.ceil(j_center + half))
    j_lo = max(0, j0)
    j_hi = min(m_max - 1, j1)
    if j_lo > j_hi:
        width = max(1, int(np.ceil(2.0 * half)))
        if j_center > m_max - 1:
            j_hi = m_max - 1
            j_lo = max(0, j_hi - width)
        else:
            j_lo = 0
            j_hi = min(m_max - 1, width)
    return j_lo, j_hi


def banded_dtw(
    mp3_feat: np.ndarray,
    partner_feat: np.ndarray,
    hop_s: float,
    scale: float,
    offset: float,
    cfg: WarpConfig,
    *,
    i_start: int = 0,
    j_start: int = 0,
) -> tuple[list[tuple[int, int]], bool]:
    """mp3 フレーム i ↔ partner フレーム j。帯外張り付きが多い場合 stuck=True。"""
    n = mp3_feat.shape[0]
    m = partner_feat.shape[0]
    if n == 0 or m == 0:
        return [], True

    dp: dict[tuple[int, int], float] = {}
    prev: dict[tuple[int, int], tuple[int, int] | None] = {}

    j_lo, j_hi = _band_j_range(i_start, hop_s, scale, offset, cfg.band_s, m)
    j_lo = max(j_lo, j_start)
    for j in range(j_lo, j_hi + 1):
        dp[(i_start, j)] = _frame_distance(mp3_feat[i_start], partner_feat[j], cfg)
        prev[(i_start, j)] = None

    for i in range(i_start + 1, n):
        j_lo, j_hi = _band_j_range(i, hop_s, scale, offset, cfg.band_s, m)
        if j_lo > j_hi:
            continue
        for j in range(j_lo, j_hi + 1):
            c = _frame_distance(mp3_feat[i], partner_feat[j], cfg)
            best = INF
            best_p: tuple[int, int] | None = None
            for di, dj in ((1, 1), (1, 2), (2, 1)):
                pi, pj = i - di, j - dj
                if pi < i_start or pj < j_start:
                    continue
                if (pi, pj) not in dp:
                    continue
                v = dp[(pi, pj)] + c
                if v < best:
                    best = v
                    best_p = (pi, pj)
            if best_p is None:
                continue
            dp[(i, j)] = best
            prev[(i, j)] = best_p

    if not dp:
        return [], True

    max_i = max(i for i, _ in dp)
    best_end: tuple[int, int] | None = None
    best_val = INF
    for (i, j), cost in dp.items():
        if i < max_i:
            continue
        if cost < best_val:
            best_val = cost
            best_end = (i, j)
    if best_end is None:
        for (i, j), cost in dp.items():
            if cost < best_val:
                best_val = cost
                best_end = (i, j)
    if best_end is None:
        return [], True

    path: list[tuple[int, int]] = []
    cur: tuple[int, int] | None = best_end
    while cur is not None:
        path.append(cur)
        cur = prev.get(cur)
    path.reverse()

    stuck = len(path) < 10
    return path, stuck


def _median_filter_1d(x: np.ndarray, width_frames: int) -> np.ndarray:
    if width_frames <= 1:
        return x.copy()
    r = width_frames // 2
    out = np.zeros_like(x)
    for i in range(x.size):
        i0 = max(0, i - r)
        i1 = min(x.size, i + r + 1)
        out[i] = float(np.median(x[i0:i1]))
    return out


def _enforce_monotonic(x: np.ndarray) -> np.ndarray:
    return np.maximum.accumulate(x)


def _limit_slope(x: np.ndarray, hop_s: float, s_min: float, s_max: float) -> np.ndarray:
    if x.size <= 1:
        return x.copy()
    out = x.copy()
    for i in range(1, out.size):
        d = out[i] - out[i - 1]
        d_min = s_min * hop_s
        d_max = s_max * hop_s
        if d < d_min:
            out[i] = out[i - 1] + d_min
        elif d > d_max:
            out[i] = out[i - 1] + d_max
    return out


def _limit_correction_slope(
    corr: np.ndarray,
    hop_s: float,
    scale: float,
    s_min: float,
    s_max: float,
) -> np.ndarray:
    if corr.size <= 1:
        return corr.copy()
    out = corr.copy()
    d_min = (s_min - scale) * hop_s
    d_max = (s_max - scale) * hop_s
    for i in range(1, out.size):
        d = out[i] - out[i - 1]
        if d < d_min:
            out[i] = out[i - 1] + d_min
        elif d > d_max:
            out[i] = out[i - 1] + d_max
    return out


def path_to_midi_audio_map(
    path: list[tuple[int, int]],
    hop_s: float,
    n_midi: int,
) -> np.ndarray:
    """partner フレーム j（≈ MIDI 時刻 j*hop）→ mp3 時刻。"""
    buckets: list[list[float]] = [[] for _ in range(n_midi)]
    for i, j in path:
        if 0 <= j < n_midi:
            buckets[j].append(i * hop_s)
    out = np.full(n_midi, np.nan, dtype=np.float64)
    for j, vals in enumerate(buckets):
        if vals:
            out[j] = float(np.median(vals))
    # 補間
    idx = np.flatnonzero(~np.isnan(out))
    if idx.size == 0:
        return out
    all_i = np.arange(n_midi, dtype=np.float64)
    filled = np.interp(all_i, idx.astype(np.float64), out[idx])
    return filled


def linear_warp(n_midi: int, hop_s: float, scale: float, offset: float) -> WarpResult:
    midi_t = np.arange(n_midi, dtype=np.float64) * hop_s
    audio_t = scale * midi_t + offset
    return WarpResult(
        hop_s=hop_s,
        midi_times_s=midi_t,
        audio_times_s=audio_t,
        linear_offset=offset,
        linear_scale=scale,
        used_warp=False,
    )


def _correlation_at_lag(a: np.ndarray, b: np.ndarray, dr: int) -> float | None:
    n = min(a.size, b.size)
    if dr >= 0:
        x, y = a[dr:n], b[: n - dr]
    else:
        d = -dr
        x, y = a[: n - d], b[d:n]
    if x.size < 5:
        return None
    if float(x.std()) <= 1e-12 or float(y.std()) <= 1e-12:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def _corr_to_confidence(best_c: float) -> float:
    return float(np.clip((best_c + 1.0) / 2.0, 0.0, 1.0))


def _correlation_peak_parabolic(
    mp3_seg: np.ndarray,
    partner_seg: np.ndarray,
    hop_s: float,
    search_s: float,
    peak_margin: float,
    *,
    center_lag_s: float = 0.0,
) -> tuple[float | None, float | None, str | None]:
    """相互相関の最大を放物線内挿。戻り値 (lag_s, confidence, reject_reason)。"""
    n = min(mp3_seg.size, partner_seg.size)
    if n < int(1.0 / hop_s):
        return None, None, "quiet"
    a, b = mp3_seg[:n], partner_seg[:n]
    if float(a.sum()) + float(b.sum()) <= 1e-9:
        return None, None, "quiet"
    dr_min = int(np.floor((center_lag_s - search_s) / hop_s))
    dr_max = int(np.ceil((center_lag_s + search_s) / hop_s))
    scores: list[tuple[int, float]] = []
    for dr in range(dr_min, dr_max + 1):
        c = _correlation_at_lag(a, b, dr)
        if c is not None:
            scores.append((dr, c))
    if not scores:
        return None, None, "quiet"
    scores.sort(key=lambda t: t[1], reverse=True)
    best_dr, best_c = scores[0]
    confidence = _corr_to_confidence(best_c)
    second_c = scores[1][1] if len(scores) > 1 else -1.0
    if best_c - second_c < peak_margin:
        return None, confidence, "ambiguous_peak"
    lag_dr = float(best_dr)
    neighbors = [(dr, c) for dr, c in scores if abs(dr - best_dr) <= 1]
    if len(neighbors) >= 3:
        pts = sorted(neighbors, key=lambda t: t[0])
        drs = [float(p[0]) for p in pts]
        cs = [p[1] for p in pts]
        if len(drs) == 3 and abs(drs[1] - best_dr) < 0.1:
            denom = cs[0] - 2.0 * cs[1] + cs[2]
            if abs(denom) > 1e-12:
                delta = 0.5 * (cs[0] - cs[2]) / denom
                delta = float(np.clip(delta, -1.0, 1.0))
                lag_dr = best_dr + delta
    return lag_dr * hop_s, confidence, None


def _median_filter_points(times: np.ndarray, lags: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or lags.size == 0:
        return lags.copy()
    r = width // 2
    out = np.zeros_like(lags)
    for i in range(lags.size):
        i0 = max(0, i - r)
        i1 = min(lags.size, i + r + 1)
        out[i] = float(np.median(lags[i0:i1]))
    return out


def _enforce_monotonic_points(lags: np.ndarray) -> np.ndarray:
    return np.maximum.accumulate(lags)


def _collect_local_lag_windows(
    mp3_on: np.ndarray,
    partner_mp3: np.ndarray,
    hop_s: float,
    duration_s: float,
    warp_cfg: WarpConfig,
    *,
    countin_end_s: float,
    exclude_centers: set[float] | None = None,
) -> tuple[list[float], list[float], list[float], dict[str, int], list[dict[str, Any]]]:
    half_win = warp_cfg.win_s * 0.5
    t = max(countin_end_s + half_win, half_win)
    stats = {
        "windows": 0,
        "quiet": 0,
        "ambiguous_peak": 0,
        "median_outlier": 0,
        "linear_deviation": 0,
        "low_confidence": 0,
        "content_interval": 0,
        "accepted_fit": 0,
        "peak_ok": 0,
    }
    cand_t: list[float] = []
    cand_lag: list[float] = []
    cand_conf: list[float] = []
    lag_records: list[dict[str, Any]] = []
    exclude_centers = exclude_centers or set()

    while t + half_win <= duration_s:
        stats["windows"] += 1
        if any(abs(t - ec) < 0.01 for ec in exclude_centers):
            stats["content_interval"] += 1
            t += warp_cfg.step_s
            continue
        i0 = max(0, int(np.floor((t - half_win) / hop_s)))
        i1 = min(mp3_on.size, int(np.ceil((t + half_win) / hop_s)))
        if i1 - i0 < int(2.0 / hop_s):
            t += warp_cfg.step_s
            continue
        center_lag = 0.0
        if cand_t:
            if len(cand_t) >= 2:
                coef = np.polyfit(np.array(cand_t), np.array(cand_lag), 1)
                center_lag = -float(np.polyval(coef, t))
            else:
                center_lag = -cand_lag[0]
        lag, conf, reason = _correlation_peak_parabolic(
            mp3_on[i0:i1],
            partner_mp3[i0:i1],
            hop_s,
            warp_cfg.range_s,
            warp_cfg.peak_margin,
            center_lag_s=center_lag,
        )
        if reason == "quiet":
            stats["quiet"] += 1
            t += warp_cfg.step_s
            continue
        if reason == "ambiguous_peak":
            stats["ambiguous_peak"] += 1
            lag_records.append(
                {
                    "t_s": t,
                    "correction_s": None,
                    "confidence": conf,
                    "used_fit": False,
                    "reject": "ambiguous_peak",
                }
            )
            t += warp_cfg.step_s
            continue
        assert lag is not None and conf is not None
        stats["peak_ok"] += 1
        correction = -lag
        cand_t.append(t)
        cand_lag.append(correction)
        cand_conf.append(conf)
        t += warp_cfg.step_s

    if not cand_t:
        return [], [], [], stats, lag_records

    times = np.array(cand_t, dtype=np.float64)
    lags = np.array(cand_lag, dtype=np.float64)
    confs = np.array(cand_conf, dtype=np.float64)
    used = np.ones(lags.size, dtype=bool)
    med = float(np.median(lags))
    median_bad = np.abs(lags - med) > warp_cfg.max_dev_s
    stats["median_outlier"] = int(np.sum(median_bad))
    used &= ~median_bad

    if int(np.sum(used)) >= 3:
        coef = np.polyfit(times[used], lags[used], 1)
        pred = np.polyval(coef, times)
        linear_bad = np.abs(lags - pred) > warp_cfg.max_dev_s
        stats["linear_deviation"] = int(np.sum(linear_bad & used))
        used &= ~linear_bad
    elif lags.size >= 1:
        linear_bad = np.abs(lags) > warp_cfg.max_dev_s
        stats["linear_deviation"] = int(np.sum(linear_bad))
        used &= ~linear_bad

    low_conf = confs < warp_cfg.conf_min
    stats["low_confidence"] = int(np.sum(low_conf & used))
    used &= confs >= warp_cfg.conf_min
    stats["accepted_fit"] = int(np.sum(used))

    for i in range(times.size):
        lag_records.append(
            {
                "t_s": float(times[i]),
                "correction_s": float(lags[i]),
                "confidence": float(confs[i]),
                "used_fit": bool(used[i]),
                "reject": None
                if used[i]
                else (
                    "low_confidence"
                    if low_conf[i]
                    else (
                        "median_outlier"
                        if median_bad[i]
                        else "linear_deviation"
                    )
                ),
            }
        )

    times_u = times[used].tolist()
    lags_u = lags[used].tolist()
    conf_u = confs[used].tolist()
    return times_u, lags_u, conf_u, stats, lag_records


def _confidence_interval_curve(
    point_t: np.ndarray,
    point_conf: np.ndarray,
    duration_s: float,
    *,
    interval_s: float = 10.0,
) -> tuple[list[float], list[float]]:
    if point_t.size == 0:
        return [], []
    starts: list[float] = []
    confs: list[float] = []
    t = 0.0
    while t < duration_s:
        t1 = t + interval_s
        mask = (point_t >= t) & (point_t < t1)
        if np.any(mask):
            c = float(np.min(point_conf[mask]))
        else:
            c = float(
                np.interp(
                    t + interval_s * 0.5,
                    point_t,
                    point_conf,
                    left=float(point_conf[0]),
                    right=float(point_conf[-1]),
                )
            )
        starts.append(t)
        confs.append(c)
        t += interval_s
    return starts, confs


def classify_mismatch_intervals(
    interval_starts: list[float],
    interval_conf: list[float],
    interval_residual_ms: list[float | None],
    warp_cfg: WarpConfig,
    *,
    interval_s: float = 10.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    content: list[dict[str, Any]] = []
    time_mm: list[dict[str, Any]] = []
    for t0, conf, res_ms in zip(
        interval_starts, interval_conf, interval_residual_ms, strict=True
    ):
        t1 = t0 + interval_s
        if conf < warp_cfg.conf_min:
            content.append(
                {"start_s": t0, "end_s": t1, "confidence": float(conf)}
            )
        elif res_ms is not None and abs(res_ms) > warp_cfg.ok_max_ms:
            time_mm.append(
                {
                    "start_s": t0,
                    "end_s": t1,
                    "confidence": float(conf),
                    "residual_ms": float(res_ms),
                }
            )
    return content, time_mm


def compute_interval_lags_ms(
    mp3_on: np.ndarray,
    partner_on_mp3: np.ndarray,
    hop_s: float,
    duration_s: float,
    *,
    interval_s: float = 10.0,
    search_s: float = 0.3,
    skip_intervals: list[dict[str, Any]] | None = None,
) -> tuple[list[float], list[float | None]]:
    skip_intervals = skip_intervals or []
    starts: list[float] = []
    lags_ms: list[float | None] = []
    n = min(mp3_on.size, partner_on_mp3.size)
    t = 0.0
    while t < duration_s:
        t1 = t + interval_s
        skipped = any(
            iv["start_s"] <= t + interval_s * 0.5 < iv["end_s"]
            for iv in skip_intervals
        )
        i0 = max(0, int(np.floor(t / hop_s)))
        i1 = min(n, int(np.ceil(t1 / hop_s)))
        lag: float | None = None
        if not skipped and i1 - i0 >= int(2.0 / hop_s):
            lag_s = _segment_residual_lag_s(
                mp3_on[i0:i1], partner_on_mp3[i0:i1], hop_s, search_s
            )
            lag = None if lag_s is None else lag_s * 1000.0
        starts.append(t)
        lags_ms.append(lag)
        t += interval_s
    return starts, lags_ms


def residual_stats_from_interval_lags(
    lags_ms: list[float | None],
) -> tuple[float, float, int]:
    vals = [abs(x) for x in lags_ms if x is not None]
    if not vals:
        return 0.0, 0.0, 0
    arr = np.array(vals, dtype=np.float64)
    return float(np.median(arr)), float(np.max(arr)), int(np.sum(arr > 20.0))


def write_align_map_png(
    path: Path,
    *,
    interval_starts: list[float],
    interval_s: float,
    residual_ms: list[float | None],
    confidence: list[float],
    content_intervals: list[dict[str, Any]],
    time_intervals: list[dict[str, Any]],
) -> None:
    import matplotlib.pyplot as plt

    if not interval_starts:
        return
    xs = np.array(interval_starts, dtype=np.float64) + interval_s * 0.5
    res = np.array(
        [np.nan if v is None else v for v in residual_ms], dtype=np.float64
    )
    conf = np.array(confidence, dtype=np.float64)
    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(11, 5), layout="tight")
    ax0, ax1 = axes
    ax0.plot(xs, res, linewidth=0.9, color="steelblue")
    ax0.axhline(50.0, color="orange", linestyle="--", linewidth=0.7, label="50ms")
    ax0.axhline(-50.0, color="orange", linestyle="--", linewidth=0.7)
    ax0.set_ylabel("residual (ms)")
    for iv in content_intervals:
        ax0.axvspan(iv["start_s"], iv["end_s"], color="salmon", alpha=0.25)
    for iv in time_intervals:
        ax0.axvspan(iv["start_s"], iv["end_s"], color="gold", alpha=0.2)
    ax1.plot(xs, conf, linewidth=0.9, color="seagreen")
    ax1.axhline(0.3, color="gray", linestyle="--", linewidth=0.7)
    ax1.set_ylabel("confidence")
    ax1.set_xlabel("time (s)")
    ax1.set_ylim(-0.05, 1.05)
    for iv in content_intervals:
        ax1.axvspan(iv["start_s"], iv["end_s"], color="salmon", alpha=0.25)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def warp_from_local_lags(
    mp3_on: np.ndarray,
    mp3_ch: np.ndarray,
    partner_on: np.ndarray,
    partner_ch: np.ndarray,
    align: AlignResult,
    align_cfg: AlignConfig,
    warp_cfg: WarpConfig,
    *,
    countin_end_s: float = 0.0,
) -> WarpResult:
    del mp3_ch, partner_ch
    hop_s = hop_seconds(align_cfg)
    scale = align.scale
    offset = align.offset
    n_mp3 = mp3_on.size
    n_midi = min(partner_on.size, mp3_on.size)
    j_start = min(n_midi - 1, max(0, int(np.floor(countin_end_s / hop_s))))

    partner_mp3 = resample_partner_linear(partner_on[:n_midi], n_mp3, hop_s, scale, offset)
    duration_s = n_mp3 * hop_s
    warnings: list[str] = []
    midi_t = np.arange(n_midi, dtype=np.float64) * hop_s
    linear_a = scale * midi_t + offset

    times_u, lags_u, conf_u, stats, lag_records = _collect_local_lag_windows(
        mp3_on,
        partner_mp3,
        hop_s,
        duration_s,
        warp_cfg,
        countin_end_s=countin_end_s,
    )
    n_total = stats["peak_ok"]

    n_peak = max(1, stats["peak_ok"])
    if stats["accepted_fit"] < max(1, int(np.ceil(0.5 * n_peak))):
        wr = linear_warp(n_midi, hop_s, scale, offset)
        wr.warnings = ["warp_too_few_points"]
        wr.warp_method = "local"
        wr.lag_points_total = n_total
        wr.lag_points_used = 0
        wr.rejection_stats = stats
        wr.lag_points = lag_records
        wr.countin_end_s = countin_end_s
        return wr

    pt = np.array(times_u, dtype=np.float64)
    pc = np.array(conf_u, dtype=np.float64)
    iv_starts, iv_conf = _confidence_interval_curve(pt, pc, duration_s)
    content_pre, _ = classify_mismatch_intervals(
        iv_starts, iv_conf, [None] * len(iv_starts), warp_cfg
    )

    def _in_content(t_s: float) -> bool:
        return any(iv["start_s"] <= t_s < iv["end_s"] for iv in content_pre)

    if content_pre:
        filt = [(t, l, c) for t, l, c in zip(times_u, lags_u, conf_u) if not _in_content(t)]
        if filt:
            times_u, lags_u, conf_u = [list(x) for x in zip(*filt)]
        stats["content_interval"] = int(
            stats.get("content_interval", 0)
            + sum(1 for p in lag_records if p.get("used_fit") and _in_content(p["t_s"]))
        )

    if not times_u:
        wr = linear_warp(n_midi, hop_s, scale, offset)
        wr.warnings = ["warp_too_few_points"]
        wr.warp_method = "local"
        wr.lag_points_total = n_total
        wr.lag_points_used = 0
        wr.rejection_stats = stats
        wr.lag_points = lag_records
        wr.countin_end_s = countin_end_s
        return wr

    times_a = np.array(times_u, dtype=np.float64)
    lags_a = np.array(lags_u, dtype=np.float64)
    lags_a = _median_filter_points(times_a, lags_a, warp_cfg.median_pts)
    lags_a = _enforce_monotonic_points(lags_a)

    corr_map = np.zeros(n_midi, dtype=np.float64)
    for j in range(j_start, n_midi):
        t_lin = linear_a[j]
        corr_map[j] = float(
            np.interp(t_lin, times_a, lags_a, left=lags_a[0], right=lags_a[-1])
        )
    ct = corr_map[j_start:].copy()
    ct = _enforce_monotonic(ct)
    ct = _limit_correction_slope(
        ct, hop_s, scale, warp_cfg.slope_min, warp_cfg.slope_max
    )
    corr_map[j_start:] = ct
    audio_map = linear_a + corr_map
    audio_map[:j_start] = linear_a[:j_start]

    wr = WarpResult(
        hop_s=hop_s,
        midi_times_s=midi_t,
        audio_times_s=audio_map,
        linear_offset=offset,
        linear_scale=scale,
        used_warp=True,
        warnings=warnings,
        countin_end_s=countin_end_s,
        warp_method="local",
        lag_points_total=n_total,
        lag_points_used=int(stats["accepted_fit"]),
        lag_points=lag_records,
        rejection_stats=stats,
    )
    partner_w = resample_partner_with_local_correction(
        partner_on[:n_midi], n_mp3, hop_s, scale, offset, wr
    )
    med, mx, n20, _ = compute_interval_residuals_ms(
        mp3_on[:n_mp3], partner_w[:n_mp3], hop_s, n_mp3 * hop_s
    )
    wr.residual_median_ms = med
    wr.residual_max_ms = mx
    wr.warp_n_over_20ms = n20

    pt_all = np.array([p["t_s"] for p in lag_records if p.get("confidence") is not None])
    pc_all = np.array(
        [p["confidence"] for p in lag_records if p.get("confidence") is not None]
    )
    iv_starts, iv_conf = _confidence_interval_curve(pt_all, pc_all, duration_s)
    _, lags_iv = compute_interval_lags_ms(
        mp3_on[:n_mp3], partner_w[:n_mp3], hop_s, duration_s
    )
    content_iv, time_iv = classify_mismatch_intervals(
        iv_starts, iv_conf, lags_iv, warp_cfg
    )
    wr.content_mismatch_intervals = content_iv
    wr.time_mismatch_intervals = time_iv

    eval_lags = [
        None if any(iv["start_s"] <= s + 5.0 < iv["end_s"] for iv in content_iv) else v
        for s, v in zip(iv_starts, lags_iv, strict=True)
    ]
    ev_med, ev_max, ev_n20 = residual_stats_from_interval_lags(eval_lags)
    wr.eval_residual_median_ms = ev_med
    wr.eval_residual_max_ms = ev_max
    wr.warp_n_over_20ms = ev_n20
    if ev_med > warp_cfg.ok_median_ms or ev_max > warp_cfg.ok_max_ms:
        warnings.append("warp_residual")
    return wr


def _segment_residual_lag_s(
    mp3_seg: np.ndarray,
    partner_seg: np.ndarray,
    hop_s: float,
    search_s: float,
) -> float | None:
    n = min(mp3_seg.size, partner_seg.size)
    if n < int(1.0 / hop_s):
        return None
    a, b = mp3_seg[:n], partner_seg[:n]
    if float(a.std()) <= 1e-12 or float(b.std()) <= 1e-12:
        return None
    dr_min = int(np.floor(-search_s / hop_s))
    dr_max = int(np.ceil(search_s / hop_s))
    best_lag, best = 0, -np.inf
    for dr in range(dr_min, dr_max + 1):
        if dr >= 0:
            x, y = a[dr:], b[: n - dr]
        else:
            d = -dr
            x, y = a[: n - d], b[d:]
        if x.size < 5 or float(x.std()) <= 1e-12 or float(y.std()) <= 1e-12:
            continue
        c = float(np.corrcoef(x, y)[0, 1])
        if c > best:
            best, best_lag = c, dr
    return float(best_lag) * hop_s


def compute_interval_residuals_ms(
    mp3_on: np.ndarray,
    partner_on_mp3: np.ndarray,
    hop_s: float,
    duration_s: float,
    *,
    interval_s: float = 10.0,
    search_s: float = 0.3,
) -> tuple[float, float, int, int]:
    n = min(mp3_on.size, partner_on_mp3.size)
    lags: list[float] = []
    t = 0.0
    while t < duration_s:
        i0 = max(0, int(np.floor(t / hop_s)))
        i1 = min(n, int(np.ceil((t + interval_s) / hop_s)))
        if i1 - i0 >= int(2.0 / hop_s):
            lag = _segment_residual_lag_s(
                mp3_on[i0:i1], partner_on_mp3[i0:i1], hop_s, search_s
            )
            if lag is not None:
                lags.append(lag)
        t += interval_s
    if not lags:
        return 0.0, 0.0, 0, 0
    arr = np.abs(np.array(lags, dtype=np.float64)) * 1000.0
    return float(np.median(arr)), float(np.max(arr)), int(np.sum(arr > 20.0)), len(lags)


def partner_on_on_mp3_axis_from_warp(
    partner_on: np.ndarray,
    n_mp3: int,
    hop_s: float,
    warp: WarpResult,
) -> np.ndarray:
    """mp3 フレーム i の時刻 t に対し、warp の逆写像で partner onset を載せる（直線評価と同じ軸）。"""
    return resample_partner_via_warp(partner_on, n_mp3, hop_s, warp)


def resample_partner_via_warp(
    partner_on: np.ndarray,
    n_mp3: int,
    hop_s: float,
    warp: WarpResult,
) -> np.ndarray:
    out = np.zeros(n_mp3, dtype=np.float64)
    if n_mp3 <= 0 or partner_on.size == 0:
        return out
    t_mp3 = np.arange(n_mp3, dtype=np.float64) * hop_s
    t_midi = np.interp(
        t_mp3,
        warp.audio_times_s,
        warp.midi_times_s,
        left=warp.midi_times_s[0],
        right=warp.midi_times_s[-1],
    )
    for i in range(n_mp3):
        j = t_midi[i] / hop_s
        if j < 0 or j >= partner_on.size - 1:
            continue
        j0 = int(np.floor(j))
        frac = j - j0
        out[i] = (1.0 - frac) * partner_on[j0] + frac * partner_on[j0 + 1]
    return out


def resample_partner_with_local_correction(
    partner_on: np.ndarray,
    n_mp3: int,
    hop_s: float,
    scale: float,
    offset: float,
    warp: WarpResult,
) -> np.ndarray:
    """直線配置＋local 補正（mp3 軸）で partner onset を載せる。"""
    linear = resample_partner_linear(partner_on, n_mp3, hop_s, scale, offset)
    if not warp.used_warp or warp.midi_times_s.size == 0:
        return linear
    corr = warp.audio_times_s - (warp.linear_scale * warp.midi_times_s + warp.linear_offset)
    n_m = corr.size
    js = np.arange(n_m, dtype=np.float64)
    xi = np.arange(n_mp3, dtype=np.float64)
    out = np.zeros(n_mp3, dtype=np.float64)
    for i in range(n_mp3):
        t = i * hop_s
        j = (t - offset) / (scale * hop_s) if abs(scale) > 1e-12 else 0.0
        c = float(np.interp(j, js, corr, left=float(corr[0]), right=float(corr[-1])))
        i_src = i + c / hop_s
        if i_src <= 0:
            out[i] = linear[0]
        elif i_src >= n_mp3 - 1:
            out[i] = linear[-1]
        else:
            i0 = int(np.floor(i_src))
            frac = i_src - i0
            out[i] = (1.0 - frac) * linear[i0] + frac * linear[i0 + 1]
    return out


def resample_partner_linear(
    partner_on: np.ndarray,
    n_mp3: int,
    hop_s: float,
    scale: float,
    offset: float,
) -> np.ndarray:
    out = np.zeros(n_mp3, dtype=np.float64)
    for i in range(n_mp3):
        t = i * hop_s
        r = (t - offset) / scale if abs(scale) > 1e-12 else 0.0
        j = r / hop_s
        if j < 0 or j >= partner_on.size - 1:
            continue
        j0 = int(np.floor(j))
        frac = j - j0
        out[i] = (1.0 - frac) * partner_on[j0] + frac * partner_on[j0 + 1]
    return out


def _compute_warp_dtw(
    mp3_on: np.ndarray,
    mp3_ch: np.ndarray,
    partner_on: np.ndarray,
    partner_ch: np.ndarray,
    align: AlignResult,
    align_cfg: AlignConfig,
    warp_cfg: WarpConfig,
    *,
    countin_end_s: float = 0.0,
) -> WarpResult:
    hop_s = hop_seconds(align_cfg)
    mp3_feat = build_warp_feature_matrix(mp3_on, mp3_ch)
    partner_feat = build_warp_feature_matrix(partner_on, partner_ch)
    n_mp3 = mp3_feat.shape[0]
    n_midi = partner_feat.shape[0]
    scale = align.scale
    offset = align.offset

    i_start = int(np.floor((scale * countin_end_s + offset) / hop_s))
    i_start = min(n_mp3 - 1, max(0, i_start))
    j_start = min(n_midi - 1, max(0, int(np.floor(countin_end_s / hop_s))))

    path, stuck = banded_dtw(
        mp3_feat,
        partner_feat,
        hop_s,
        scale,
        offset,
        warp_cfg,
        i_start=i_start,
        j_start=j_start,
    )

    warnings: list[str] = []
    if stuck or len(path) < 10:
        warnings.append("warp_band_stuck")
        wr = linear_warp(n_midi, hop_s, scale, offset)
        wr.warnings = warnings
        wr.countin_end_s = countin_end_s
        return wr

    midi_t = np.arange(n_midi, dtype=np.float64) * hop_s
    linear_a = scale * midi_t + offset
    audio_map = path_to_midi_audio_map(path, hop_s, n_midi)
    nan_mask = np.isnan(audio_map)
    audio_map[nan_mask] = linear_a[nan_mask]
    audio_map[:j_start] = linear_a[:j_start]

    w_med = max(1, int(round(warp_cfg.median_s / hop_s)))
    tail = audio_map[j_start:].copy()
    tail = _median_filter_1d(tail, w_med)
    tail = _enforce_monotonic(tail)
    tail = _limit_slope(tail, hop_s, warp_cfg.slope_min, warp_cfg.slope_max)
    audio_map[j_start:] = tail

    wr = WarpResult(
        hop_s=hop_s,
        midi_times_s=midi_t,
        audio_times_s=audio_map,
        linear_offset=offset,
        linear_scale=scale,
        used_warp=True,
        warnings=warnings,
        countin_end_s=countin_end_s,
    )

    partner_w = resample_partner_via_warp(partner_on, n_mp3, hop_s, wr)
    med, mx, n20, _ = compute_interval_residuals_ms(
        mp3_on[:n_mp3], partner_w[:n_mp3], hop_s, n_mp3 * hop_s
    )
    wr.residual_median_ms = med
    wr.residual_max_ms = mx
    wr.warp_n_over_20ms = n20
    if med > warp_cfg.ok_median_ms or mx > warp_cfg.ok_max_ms:
        warnings.append("warp_residual")
    wr.warp_method = "dtw"
    return wr


def compute_warp(
    mp3_on: np.ndarray,
    mp3_ch: np.ndarray,
    partner_on: np.ndarray,
    partner_ch: np.ndarray,
    align: AlignResult,
    align_cfg: AlignConfig,
    warp_cfg: WarpConfig,
    *,
    countin_end_s: float = 0.0,
) -> WarpResult:
    if warp_cfg.method == "dtw":
        return _compute_warp_dtw(
            mp3_on,
            mp3_ch,
            partner_on,
            partner_ch,
            align,
            align_cfg,
            warp_cfg,
            countin_end_s=countin_end_s,
        )
    return warp_from_local_lags(
        mp3_on,
        mp3_ch,
        partner_on,
        partner_ch,
        align,
        align_cfg,
        warp_cfg,
        countin_end_s=countin_end_s,
    )


def compute_warp_from_midi_features(
    mp3_on: np.ndarray,
    mp3_ch: np.ndarray,
    midi,
    align: AlignResult,
    align_cfg: AlignConfig,
    warp_cfg: WarpConfig,
    *,
    countin_end_s: float = 0.0,
) -> WarpResult:
    n_mp3 = mp3_on.size
    midi_on, midi_ch, _, _ = build_midi_feature_streams(midi, n_mp3, align_cfg)
    n = min(n_mp3, midi_on.size, midi_ch.shape[1])
    return compute_warp(
        mp3_on[:n],
        mp3_ch[:, :n],
        midi_on[:n],
        midi_ch[:, :n],
        align,
        align_cfg,
        warp_cfg,
        countin_end_s=countin_end_s,
    )


def write_warp_json(path: Path, warp: WarpResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(warp.to_json_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_warp_json(path: Path) -> WarpResult:
    return WarpResult.from_json_dict(json.loads(path.read_text(encoding="utf-8")))


def warp_eval_notes(
    midi,
    warp: WarpResult,
    *,
    exclude_track_names: frozenset[str] | None = None,
):
    from a2m.align import EXPORT_ROLES
    from a2m.evaluate import EvalNote

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
                start_s=warp.audio_at_midi(n.start_s),
                end_s=warp.audio_at_midi(n.end_s),
                drum_class=n.drum_class,
            )
        )
    return out


def write_render_warped_wav(
    path: Path,
    render_y: np.ndarray,
    mp3_len: int,
    warp: WarpResult,
    partner_on: np.ndarray,
    hop_s: float,
    sr: int,
) -> None:
    """DTW パスに基づき render を mp3 タイムラインへリサンプル（1 アイテム用）。"""
    import soundfile as sf

    n_mp3 = int(mp3_len)
    out = np.zeros(n_mp3, dtype=np.float64)
    n_midi = min(partner_on.size, warp.midi_times_s.size)
    for j in range(n_midi):
        t0 = warp.audio_times_s[j]
        t1 = (
            warp.audio_times_s[j + 1]
            if j + 1 < n_midi
            else warp.audio_times_s[j] + hop_s
        )
        i0 = max(0, int(np.floor(t0 * sr)))
        i1 = min(n_mp3, int(np.ceil(t1 * sr)))
        r0 = int(np.floor(j * hop_s * sr))
        r1 = min(render_y.size, int(np.ceil((j + 1) * hop_s * sr)))
        if i1 <= i0 or r1 <= r0:
            continue
        seg = render_y[r0:r1]
        if seg.size == 0:
            continue
        dst_len = i1 - i0
        src_x = np.linspace(0.0, 1.0, seg.size, dtype=np.float64)
        dst_x = np.linspace(0.0, 1.0, dst_len, dtype=np.float64)
        out[i0:i1] += np.interp(dst_x, src_x, seg)
    peak = float(np.max(np.abs(out)))
    if peak > 1.0:
        out /= peak
    sf.write(path, out, sr, subtype="PCM_16")
