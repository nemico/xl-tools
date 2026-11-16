"""鳴らして合わせる（M3）：1本ずつ鳴らした音を足し合わせて、原曲の分離音に一番近くなる音量を求める。"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.optimize import nnls


def band_envelope(y: np.ndarray, sr: int, cfg: dict[str, Any]) -> np.ndarray:
    """音の大きさを (帯域, 時間の区切り) の表にする（パワー）。"""
    import librosa

    spec = librosa.feature.melspectrogram(y=y, sr=sr, n_fft=cfg["n_fft"], hop_length=cfg["hop"], n_mels=cfg["n_mels"], power=2.0)
    k = cfg["block_frames"]
    n = spec.shape[1] // k
    return spec[:, : n * k].reshape(spec.shape[0], n, k).mean(axis=2)


def shift_blocks(env: np.ndarray, offset_s: float, sr: int, cfg: dict[str, Any], n_blocks: int) -> np.ndarray:
    """MIDI の時刻で作った表を、音源の時刻（offset_s だけ後ろ）に置き直し、長さを n_blocks にそろえる。"""
    block_s = cfg["hop"] * cfg["block_frames"] / sr
    k = int(round(offset_s / block_s))
    out = np.zeros((env.shape[0], n_blocks))
    src0, dst0 = max(0, -k), max(0, k)
    n = min(env.shape[1] - src0, n_blocks - dst0)
    if n > 0:
        out[:, dst0 : dst0 + n] = env[:, src0 : src0 + n]
    return out


def fit_gains(target: np.ndarray, bases: list[np.ndarray], floor_db: float) -> np.ndarray:
    """target ≈ Σ gain_i × bases_i となる gain（パワーの倍率、0以上）を求める。

    帯域ごとに target の平均で割ってから解く（低音だけで決まらないように）。
    鳴っていないトラックは nan。
    """
    w = 1.0 / (target.mean(axis=1, keepdims=True) + 1e-12)
    a = np.stack([(b * w).ravel() for b in bases], axis=1)
    ref = max(float(np.max(a)), 1e-30)
    active = a.max(axis=0) > ref * 10 ** (floor_db / 10)
    gains = np.full(len(bases), np.nan)
    if active.any():
        scale = a[:, active].max(axis=0)
        sol, _ = nnls(a[:, active] / scale, (target * w).ravel())
        gains[active] = sol / scale
    return gains


def gain_db(gains: np.ndarray, min_db: float) -> np.ndarray:
    """パワーの倍率を dB にする。0 は min_db にする。"""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(np.isnan(gains), np.nan, np.maximum(10 * np.log10(np.maximum(gains, 1e-30)), min_db))


def cc_from_db(db: float, base: int) -> int:
    """基準の値（base）から db だけ変えたときのコントローラーの値。GS の音量は値の2乗に比例する。"""
    return int(np.clip(round(base * 10 ** (db / 40.0)), 0, 127))


def db_from_cc(value: float, base: float) -> float:
    return 40.0 * np.log10(max(value, 1e-6) / base)
