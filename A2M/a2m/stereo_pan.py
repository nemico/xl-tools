"""CARD_P1-23: ステレオ・パンニング解析および分離音響処理モジュール。"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import scipy.signal
import soundfile as sf


def analyze_stereo_degrees(
    y: np.ndarray,
    sr: int = 44100,
    corr_threshold: float = 0.98,
    sm_db_threshold: float = -20.0,
) -> dict[str, float | bool]:
    """第0部: L/R相関、Side/Mid比、帯域別Side/Mid比、ほぼモノラル判定。
    y: shape (T, 2)
    """
    if y.ndim != 2 or y.shape[1] < 2:
        return {
            "corr": 1.0,
            "sm_db": -100.0,
            "low_db": -100.0,
            "mid_db": -100.0,
            "high_db": -100.0,
            "vhigh_db": -100.0,
            "is_mono": True,
        }

    L = y[:, 0].astype(np.float64)
    R = y[:, 1].astype(np.float64)

    # 1. 相関係数
    corr = float(np.corrcoef(L, R)[0, 1])

    # 2. Side / Mid
    M = (L + R) / 2.0
    S = (L - R) / 2.0
    em = float(np.sum(M**2))
    es = float(np.sum(S**2))
    sm_db = 10.0 * np.log10(max(es, 1e-12) / (em + 1e-12))

    # 3. 帯域別 STFT
    nperseg = 2048
    noverlap = 1024
    f, _, Z_M = scipy.signal.stft(M, fs=sr, nperseg=nperseg, noverlap=noverlap)
    _, _, Z_S = scipy.signal.stft(S, fs=sr, nperseg=nperseg, noverlap=noverlap)
    P_M = np.abs(Z_M) ** 2
    P_S = np.abs(Z_S) ** 2

    def band_db(f_low: float, f_high: float) -> float:
        mask = (f >= f_low) & (f < f_high)
        e_m = float(np.sum(P_M[mask, :]))
        e_s = float(np.sum(P_S[mask, :]))
        return 10.0 * np.log10(max(e_s, 1e-12) / (e_m + 1e-12))

    low_db = band_db(0, 200)
    mid_db = band_db(200, 2000)
    high_db = band_db(2000, 8000)
    vhigh_db = band_db(8000, sr / 2.0)

    is_mono = bool((corr >= corr_threshold) or (sm_db <= sm_db_threshold))

    return {
        "corr": corr,
        "sm_db": sm_db,
        "low_db": low_db,
        "mid_db": mid_db,
        "high_db": high_db,
        "vhigh_db": vhigh_db,
        "is_mono": is_mono,
    }


def extract_side(y: np.ndarray) -> np.ndarray:
    """第1部: S = (L - R) / 2。左右に振られたものを残す。"""
    if y.ndim != 2 or y.shape[1] < 2:
        return np.zeros(len(y), dtype=np.float32)
    return ((y[:, 0] - y[:, 1]) / 2.0).astype(np.float32)


def extract_center(
    y: np.ndarray,
    sr: int = 44100,
    n_fft: int = 2048,
    hop_length: int = 512,
) -> np.ndarray:
    """第1部: センター抽出。
    STFTの各点で panness = |S| / (|M| + |S| + 1e-9) を求め、
    M に (1 - panness) を掛けて逆変換。
    """
    if y.ndim != 2 or y.shape[1] < 2:
        return y.flatten().astype(np.float32)

    L = y[:, 0].astype(np.float32)
    R = y[:, 1].astype(np.float32)

    # scipy stft
    _, _, Z_L = scipy.signal.stft(L, fs=sr, nperseg=n_fft, noverlap=n_fft - hop_length)
    _, _, Z_R = scipy.signal.stft(R, fs=sr, nperseg=n_fft, noverlap=n_fft - hop_length)

    Z_M = (Z_L + Z_R) / 2.0
    Z_S = (Z_L - Z_R) / 2.0

    mag_M = np.abs(Z_M)
    mag_S = np.abs(Z_S)
    panness = mag_S / (mag_M + mag_S + 1e-9)

    Z_center = Z_M * (1.0 - panness)

    _, center_y = scipy.signal.istft(Z_center, fs=sr, nperseg=n_fft, noverlap=n_fft - hop_length)
    # 長さを元音声に合わせる
    if len(center_y) > len(L):
        center_y = center_y[: len(L)]
    elif len(center_y) < len(L):
        center_y = np.pad(center_y, (0, len(L) - len(center_y)))

    return center_y.astype(np.float32)


PAN_BINS_7 = [
    (-1.0, -0.6),
    (-0.6, -0.3),
    (-0.3, -0.1),
    (-0.1, 0.1),
    (0.1, 0.3),
    (0.3, 0.6),
    (0.6, 1.0),
]


def extract_pan_slices(
    y: np.ndarray,
    sr: int = 44100,
    bins: list[tuple[float, float]] = PAN_BINS_7,
    overlap: float = 0.05,
    n_fft: int = 2048,
    hop_length: int = 512,
) -> list[np.ndarray]:
    """第2部: 定位で切る。
    pan = (|R| - |L|) / (|L| + |R| + 1e-9) ([-1=左, 0=中央, +1=右])
    各区間に重なり overlap を持たせてマスクを作り、ISTFT。
    """
    if y.ndim != 2 or y.shape[1] < 2:
        return [y.flatten().astype(np.float32)] * len(bins)

    L = y[:, 0].astype(np.float32)
    R = y[:, 1].astype(np.float32)

    _, _, Z_L = scipy.signal.stft(L, fs=sr, nperseg=n_fft, noverlap=n_fft - hop_length)
    _, _, Z_R = scipy.signal.stft(R, fs=sr, nperseg=n_fft, noverlap=n_fft - hop_length)

    Z_M = (Z_L + Z_R) / 2.0
    mag_L = np.abs(Z_L)
    mag_R = np.abs(Z_R)

    # pan in [-1, +1]: -1=左, 0=中央, +1=右
    pan = (mag_R - mag_L) / (mag_L + mag_R + 1e-9)

    sliced_signals: list[np.ndarray] = []
    orig_len = len(L)

    for b_idx, (b_low, b_high) in enumerate(bins):
        low_bound = b_low - overlap if b_idx > 0 else b_low
        high_bound = b_high + overlap if b_idx < len(bins) - 1 else b_high

        mask = (pan >= low_bound) & (pan <= high_bound)
        Z_slice = Z_M * mask

        _, s_y = scipy.signal.istft(Z_slice, fs=sr, nperseg=n_fft, noverlap=n_fft - hop_length)
        if len(s_y) > orig_len:
            s_y = s_y[:orig_len]
        elif len(s_y) < orig_len:
            s_y = np.pad(s_y, (0, orig_len - len(s_y)))

        sliced_signals.append(s_y.astype(np.float32))

    return sliced_signals


def calculate_spectrogram_pan_and_energy(
    y: np.ndarray,
    sr: int = 44100,
    n_fft: int = 2048,
    hop_length: int = 512,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """STFT を計算し、周波数配列、時刻配列、pan 行列、energy 行列を返す。"""
    L = y[:, 0].astype(np.float32)
    R = y[:, 1].astype(np.float32)

    f, t, Z_L = scipy.signal.stft(L, fs=sr, nperseg=n_fft, noverlap=n_fft - hop_length)
    _, _, Z_R = scipy.signal.stft(R, fs=sr, nperseg=n_fft, noverlap=n_fft - hop_length)

    mag_L = np.abs(Z_L)
    mag_R = np.abs(Z_R)
    pan = (mag_R - mag_L) / (mag_L + mag_R + 1e-9)
    energy = mag_L**2 + mag_R**2
    return f, t, pan, energy


def weighted_median_and_iqr(values: np.ndarray, weights: np.ndarray) -> tuple[float, float]:
    """エネルギー重み付き中央値と四分位範囲 (IQR) を計算する。"""
    if len(values) == 0 or np.sum(weights) <= 0:
        return 0.0, 0.0

    sorter = np.argsort(values)
    v_sorted = values[sorter]
    w_sorted = weights[sorter]
    cum_w = np.cumsum(w_sorted)
    tot_w = cum_w[-1]
    if tot_w <= 0:
        return 0.0, 0.0
    cum_rel = cum_w / tot_w

    def interp_percentile(p: float) -> float:
        idx = np.searchsorted(cum_rel, p)
        if idx >= len(v_sorted):
            return float(v_sorted[-1])
        return float(v_sorted[idx])

    q25 = interp_percentile(0.25)
    med = interp_percentile(0.50)
    q75 = interp_percentile(0.75)
    iqr = max(0.0, q75 - q25)
    return med, iqr


def estimate_note_panning(
    start_s: float,
    end_s: float,
    pitch: int,
    f_bins: np.ndarray,
    t_frames: np.ndarray,
    pan_matrix: np.ndarray,
    energy_matrix: np.ndarray,
    harmonics: Sequence[int] = (1, 2, 3, 4, 5, 6),
) -> tuple[float, float]:
    """第3部: 候補ノート（時刻 t、音高 p）の 1〜6倍音における pan のエネルギー重み付き中央値と IQR。"""
    f0 = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
    # ノート持続区間（フレーム範囲）
    # 発音開始から最大 0.3s、またはノート末尾まで
    t_end_eval = min(end_s, start_s + 0.3)
    t_mask = (t_frames >= start_s) & (t_frames <= t_end_eval)
    if not np.any(t_mask):
        # 最近傍の1フレーム
        nearest_idx = int(np.argmin(np.abs(t_frames - start_s)))
        t_mask = np.zeros(len(t_frames), dtype=bool)
        t_mask[nearest_idx] = True

    h_pans: list[float] = []
    h_weights: list[float] = []

    for h in harmonics:
        fh = h * f0
        if fh > f_bins[-1]:
            continue
        # 最近傍の周波数ビン
        f_idx = int(np.argmin(np.abs(f_bins - fh)))

        # 区間内のエネルギー重み付き pan
        e_frames = energy_matrix[f_idx, t_mask]
        p_frames = pan_matrix[f_idx, t_mask]

        tot_e = float(np.sum(e_frames))
        if tot_e > 1e-12:
            avg_p = float(np.sum(p_frames * e_frames) / tot_e)
            h_pans.append(avg_p)
            h_weights.append(tot_e)

    if not h_pans:
        return 0.0, 0.0

    return weighted_median_and_iqr(np.array(h_pans), np.array(h_weights))


def plot_pan_map(
    f: np.ndarray,
    pan: np.ndarray,
    energy: np.ndarray,
    out_png_path: Path,
    song_name: str,
    sabi_t0: float | None = None,
    sabi_t1: float | None = None,
    t_frames: np.ndarray | None = None,
) -> None:
    """第4部: 定位×周波数のマップ（曲全体 / サビ）を書き出す。"""
    out_png_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharey=True)

    # 周波数マスク: 20Hz 〜 20kHz
    f_mask = (f >= 20.0) & (f <= 20000.0)
    f_sub = f[f_mask]

    pan_bins = np.linspace(-1.0, 1.0, 101)
    f_edges = np.geomspace(20.0, 20000.0, 101)

    def compute_2d_hist(mask_t: np.ndarray) -> np.ndarray:
        p_flat = pan[f_mask, :][:, mask_t].flatten()
        f_flat = np.repeat(f_sub[:, np.newaxis], np.sum(mask_t), axis=1).flatten()
        w_flat = energy[f_mask, :][:, mask_t].flatten()

        hist, _, _ = np.histogram2d(p_flat, f_flat, bins=[pan_bins, f_edges], weights=w_flat)
        return hist.T  # shape: (len(f_edges)-1, len(pan_bins)-1)

    # 1. 全体
    all_t = np.ones(pan.shape[1], dtype=bool)
    h_all = compute_2d_hist(all_t)
    h_all_log = np.log10(h_all + 1e-6)

    # 2. サビ (または中央4小節)
    if sabi_t0 is not None and sabi_t1 is not None and t_frames is not None:
        sabi_t = (t_frames >= sabi_t0) & (t_frames <= sabi_t1)
        if not np.any(sabi_t):
            sabi_t = all_t
    else:
        # 中央10秒
        if t_frames is not None:
            t_mid = (t_frames[0] + t_frames[-1]) / 2.0
            sabi_t = (t_frames >= t_mid - 5.0) & (t_frames <= t_mid + 5.0)
        else:
            sabi_t = all_t

    h_sabi = compute_2d_hist(sabi_t)
    h_sabi_log = np.log10(h_sabi + 1e-6)

    # プロット 1: 全体
    pcm0 = axes[0].pcolormesh(
        pan_bins,
        f_edges,
        h_all_log,
        cmap="magma",
        shading="auto",
    )
    axes[0].set_yscale("log")
    axes[0].set_ylim(20, 20000)
    song_id = song_name.split("-")[0] if "-" in song_name else song_name
    axes[0].set_title(f"Full Song ({song_id})")
    axes[0].set_xlabel("Panning (-1=Left, 0=Center, +1=Right)")
    axes[0].set_ylabel("Frequency (Hz, log)")
    axes[0].axvline(0, color="cyan", linestyle="--", alpha=0.5)
    fig.colorbar(pcm0, ax=axes[0], label="log10(Energy)")

    # プロット 2: サビ
    pcm1 = axes[1].pcolormesh(
        pan_bins,
        f_edges,
        h_sabi_log,
        cmap="magma",
        shading="auto",
    )
    axes[1].set_yscale("log")
    axes[1].set_ylim(20, 20000)
    dur_label = f"{sabi_t0:.1f}s - {sabi_t1:.1f}s" if (sabi_t0 and sabi_t1) else "Chorus"
    axes[1].set_title(f"Chorus / Highlight ({dur_label})")
    axes[1].set_xlabel("Panning (-1=Left, 0=Center, +1=Right)")
    axes[1].axvline(0, color="cyan", linestyle="--", alpha=0.5)
    fig.colorbar(pcm1, ax=axes[1], label="log10(Energy)")

    plt.tight_layout()
    plt.savefig(str(out_png_path), dpi=150)
    plt.close(fig)
