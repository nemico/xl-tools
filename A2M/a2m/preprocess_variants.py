"""CARD_P1-25: 前処理バリアント（M15〜M20）の音響処理モジュール。

- M15: 立ち上がりの強調（HPSS: 打撃成分 + 持続成分×0.5）
- M16: 残差の音量そろえ（原曲 - drums - bass - vocals, 1.5s窓 RMS平坦化, 上限+20dB）
- M17: 時間の引き伸ばし（time_stretch 0.5倍速, 時刻復元精度 5ms以内）
- M18: 周波数帯分割（4帯域: ~250Hz, 250~1kHz, 1k~4kHz, 4kHz~, 1/3オクターブ重ね）
- M19 / M20: 繰り返し分離（REPET: 伴奏 M19, 歌・アドリブ M20）
"""

from __future__ import annotations

from typing import Sequence

import librosa
import numpy as np
import scipy.signal


def make_m15_waveform(
    y: np.ndarray,
    margin: tuple[float, float] = (1.0, 3.0),
    h_weight: float = 0.5,
) -> np.ndarray:
    """第1部: 打撃成分 ＋ 持続成分×0.5（立ち上がり強調）。"""
    y_harm, y_perc = librosa.effects.hpss(y, margin=margin)
    return y_perc + y_harm * h_weight


def make_m16_waveform(
    y: np.ndarray,
    drums_y: np.ndarray,
    bass_y: np.ndarray,
    vocals_y: np.ndarray,
    sr: int = 44100,
    win_s: float = 1.5,
    overlap_s: float = 0.5,
    max_gain_db: float = 20.0,
) -> np.ndarray:
    """第2部: 原曲 - (drums + bass + vocals) の残差に対して 1.5s 窓 RMS 平坦化。"""
    min_len = min(len(y), len(drums_y), len(bass_y), len(vocals_y))
    res = y[:min_len] - (drums_y[:min_len] + bass_y[:min_len] + vocals_y[:min_len])

    win_samples = max(256, int(win_s * sr))
    hop_samples = max(128, int((win_s - overlap_s) * sr))
    max_gain = 10.0 ** (max_gain_db / 20.0)

    # フレーム分割で RMS を測定
    n_frames = max(1, (len(res) - win_samples) // hop_samples + 1)
    frame_centers = np.arange(n_frames) * hop_samples + win_samples // 2
    rms_list = []
    for i in range(n_frames):
        st = i * hop_samples
        ed = st + win_samples
        chunk = res[st:ed]
        rms_val = np.sqrt(np.mean(chunk**2)) if len(chunk) > 0 else 0.0
        rms_list.append(rms_val)

    rms_arr = np.array(rms_list, dtype=float)
    active_rms = rms_arr[rms_arr > 1e-4]
    target_rms = float(np.median(active_rms)) if len(active_rms) > 0 else 0.05

    gains = []
    for r in rms_arr:
        if r <= 1e-7:
            g = max_gain
        else:
            g = target_rms / r
            g = min(max_gain, max(0.1, g))
        gains.append(g)

    # ゲイン曲線を全サンプルに補間（スムーズ化）
    if len(frame_centers) > 1:
        gain_curve = np.interp(
            np.arange(len(res)),
            frame_centers,
            gains,
            left=gains[0],
            right=gains[-1],
        )
    else:
        gain_curve = np.full(len(res), gains[0] if gains else 1.0)

    out = res * gain_curve
    # クリッピング防止のソフトリミット
    peak = np.max(np.abs(out))
    if peak > 0.99:
        out = out / peak * 0.99
    return out


def time_stretch_aligned(
    sig: np.ndarray,
    sr: int = 44100,
    rate: float = 0.5,
) -> np.ndarray:
    """第3部: 音高を変えずに時間を引き伸ばし、位相ボコーダ遅延を補正（5ms以内）。"""
    y_str = librosa.effects.time_stretch(y=sig, rate=rate)
    step = int(round(1.0 / rate))
    env1 = np.abs(sig)
    env2 = np.abs(y_str)[::step][: len(env1)]
    xcorr = scipy.signal.correlate(env2, env1, mode="same")
    lags = scipy.signal.correlation_lags(len(env2), len(env1), mode="same")
    lag_orig = lags[np.argmax(xcorr)]
    lag_str = int(round(lag_orig / rate))
    if 0 < lag_str < len(y_str):
        y_aligned = np.zeros_like(y_str)
        y_aligned[:-lag_str] = y_str[lag_str:]
        return y_aligned
    elif lag_str < 0 and -lag_str < len(y_str):
        y_aligned = np.zeros_like(y_str)
        y_aligned[-lag_str:] = y_str[:lag_str]
        return y_aligned
    return y_str


def make_m18_band_waveforms(
    y: np.ndarray,
    sr: int = 44100,
    cutoffs: tuple[float, float, float] = (250.0, 1000.0, 4000.0),
    overlap_octaves: float = 1.0 / 3.0,
    n_fft: int = 2048,
    hop_length: int = 512,
) -> list[tuple[str, np.ndarray]]:
    """第4部: 4帯域分割（〜250Hz, 250〜1kHz, 1k〜4kHz, 4kHz〜, 1/3オクターブ重なり）。"""
    D = librosa.stft(y, n_fft=n_fft, hop_length=hop_length)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    r = 2.0**overlap_octaves

    band_names = [
        "sub_low_0-250Hz",
        "low_mid_250-1kHz",
        "mid_high_1k-4kHz",
        "high_4kHz-",
    ]

    masks: list[np.ndarray] = []
    for b_idx in range(4):
        mask = np.ones_like(freqs, dtype=float)
        if b_idx > 0:
            f_low = cutoffs[b_idx - 1]
            f_start = f_low / r
            f_end = f_low * r
            idx_fade = (freqs >= f_start) & (freqs <= f_end)
            idx_zero = freqs < f_start
            mask[idx_zero] = 0.0
            if np.any(idx_fade):
                fade = 0.5 * (
                    1.0
                    - np.cos(
                        np.pi
                        * (np.log2(freqs[idx_fade]) - np.log2(f_start))
                        / (np.log2(f_end) - np.log2(f_start))
                    )
                )
                mask[idx_fade] *= fade
        if b_idx < 3:
            f_high = cutoffs[b_idx]
            f_start = f_high / r
            f_end = f_high * r
            idx_fade = (freqs >= f_start) & (freqs <= f_end)
            idx_zero = freqs > f_end
            mask[idx_zero] = 0.0
            if np.any(idx_fade):
                fade = 0.5 * (
                    1.0
                    + np.cos(
                        np.pi
                        * (np.log2(freqs[idx_fade]) - np.log2(f_start))
                        / (np.log2(f_end) - np.log2(f_start))
                    )
                )
                mask[idx_fade] *= fade
        masks.append(mask)

    band_waves: list[tuple[str, np.ndarray]] = []
    for b_idx, (name, mask) in enumerate(zip(band_names, masks)):
        D_b = D * mask[:, None]
        y_b = librosa.istft(D_b, hop_length=hop_length, length=len(y))
        band_waves.append((name, y_b))

    return band_waves


def estimate_repetition_period(
    y: np.ndarray,
    sr: int,
    bar_times: np.ndarray,
    candidate_periods_bars: Sequence[int] = (4, 8, 12, 16, 24, 32, 48, 64),
    hop_length: int = 512,
) -> int:
    """第5部: クロマ + MFCC の小節同期自己相似性から最適な繰り返し周期（小節）を推定。"""
    n_bars = len(bar_times) - 1
    if n_bars < 4:
        return 4

    chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=hop_length)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=hop_length, n_mfcc=13)
    mfcc_norm = mfcc / (np.linalg.norm(mfcc, axis=0, keepdims=True) + 1e-6)
    feat = np.vstack([chroma, mfcc_norm])
    t_frames = librosa.frames_to_time(np.arange(feat.shape[1]), sr=sr, hop_length=hop_length)

    bar_feats = []
    for b in range(n_bars):
        t0, t1 = bar_times[b], bar_times[b + 1]
        mask = (t_frames >= t0) & (t_frames < t1)
        if np.any(mask):
            bar_feats.append(np.mean(feat[:, mask], axis=1))
        else:
            bar_feats.append(np.zeros(feat.shape[0]))
    bar_feats_arr = np.array(bar_feats)
    norms = np.linalg.norm(bar_feats_arr, axis=1, keepdims=True) + 1e-6
    bar_feats_norm = bar_feats_arr / norms

    S = bar_feats_norm @ bar_feats_norm.T

    scores: dict[int, float] = {}
    for L in candidate_periods_bars:
        if L < n_bars - 2:
            diag = np.diagonal(S, offset=L)
            scores[L] = float(np.mean(diag))

    if not scores:
        return 8

    # 4小節と8小節以上がある場合、ポップスの節構造（8小節以上）のピークを優先判定
    # 8小節以上のスコアが4小節スコアの 97% 以上あれば、大構造（8/16等）を採用
    best_cand = max(scores, key=scores.get)
    sub_scores = {k: v for k, v in scores.items() if k >= 8}
    if sub_scores:
        best_struct = max(sub_scores, key=sub_scores.get)
        if sub_scores[best_struct] >= scores.get(4, 0.0) * 0.97:
            return best_struct

    return best_cand


def separate_repet(
    y: np.ndarray,
    sr: int,
    bar_times: np.ndarray,
    period_bars: int,
    n_sub_frames: int = 32,
    n_fft: int = 2048,
    hop_length: int = 512,
) -> tuple[np.ndarray, np.ndarray]:
    """第5部: REPET（構造反復分離）による伴奏（M19: 繰り返し成分）と歌・アドリブ（M20: 非繰り返し成分）の分離。"""
    n_bars = len(bar_times) - 1
    if n_bars <= 1:
        return y.copy(), np.zeros_like(y)

    D = librosa.stft(y, n_fft=n_fft, hop_length=hop_length)
    mag = np.abs(D)
    phase = np.angle(D)
    n_frames = D.shape[1]
    t_frames = librosa.frames_to_time(np.arange(n_frames), sr=sr, hop_length=hop_length)

    bar_indices = np.searchsorted(bar_times, t_frames) - 1
    bar_indices = np.clip(bar_indices, 0, n_bars - 1)

    # 各小節のスペクトログラムを固定フレーム数 n_sub_frames に時間リサンプリング
    bar_mats = []
    for b in range(n_bars):
        idx = np.where(bar_indices == b)[0]
        if len(idx) > 0:
            m_b = mag[:, idx]
            x_orig = np.linspace(0, 1, len(idx))
            x_new = np.linspace(0, 1, n_sub_frames)
            m_interp = np.array([np.interp(x_new, x_orig, m_b[f]) for f in range(D.shape[0])])
            bar_mats.append(m_interp)
        else:
            bar_mats.append(np.zeros((D.shape[0], n_sub_frames)))

    bar_mats_arr = np.array(bar_mats)

    # 各小節 b について、周期 P だけ離れた小節群の中央値を計算
    rep_bar_mats = np.zeros_like(bar_mats_arr)
    for b in range(n_bars):
        rep_k = [b + k * period_bars for k in range(-(b // period_bars), (n_bars - 1 - b) // period_bars + 1)]
        if len(rep_k) > 1:
            rep_bar_mats[b] = np.median(bar_mats_arr[rep_k], axis=0)
        else:
            rep_bar_mats[b] = bar_mats_arr[b]

    # 元のフレーム長に逆リサンプリングして繰り返し振幅スペクトルを構築
    rep_mag = np.zeros_like(mag)
    for b in range(n_bars):
        idx = np.where(bar_indices == b)[0]
        if len(idx) > 0:
            x_new = np.linspace(0, 1, len(idx))
            x_sub = np.linspace(0, 1, n_sub_frames)
            rep_m_b = np.array([np.interp(x_new, x_sub, rep_bar_mats[b, f]) for f in range(D.shape[0])])
            rep_mag[:, idx] = np.minimum(mag[:, idx], rep_m_b)

    non_rep_mag = np.maximum(0.0, mag - rep_mag)

    D_rep = rep_mag * np.exp(1j * phase)
    D_non = non_rep_mag * np.exp(1j * phase)

    y_rep = librosa.istft(D_rep, hop_length=hop_length, length=len(y))
    y_non = librosa.istft(D_non, hop_length=hop_length, length=len(y))

    return y_rep, y_non
