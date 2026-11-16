"""CARD_P1-44: 打ち込まれたノート（見本）から楽器の音の形を学び、残りの候補を点数付けする。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import librosa
import numpy as np
import yaml

RepresentationMode = Literal["STFT", "CQT"]
LearningMethod = Literal["average", "nmf", "lgbm"]


@dataclass
class SampleSignatureConfig:
    min_duration_s: float = 0.05
    onset_offset_s: float = 0.150
    window_margin_s: float = 0.080
    peak_cents_tol: float = 50.0
    harmonic_numbers: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 5, 6])
    stft_n_fft: int = 4096
    stft_hop_length: int = 441
    cqt_fmin: float = 32.7  # C1
    cqt_n_bins: int = 84
    cqt_bins_per_octave: int = 12
    nmf_components: int = 2
    lgbm_n_estimators: int = 30
    lgbm_max_depth: int = 3
    lgbm_learning_rate: float = 0.1

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SampleSignatureConfig:
        sub = d.get("sample_signature", {})
        stft_cfg = sub.get("stft", {})
        cqt_cfg = sub.get("cqt", {})
        return cls(
            min_duration_s=float(sub.get("min_duration_s", 0.05)),
            onset_offset_s=float(sub.get("onset_offset_s", 0.150)),
            window_margin_s=float(sub.get("window_margin_s", 0.080)),
            peak_cents_tol=float(sub.get("peak_cents_tol", 50.0)),
            harmonic_numbers=list(sub.get("harmonic_numbers", [1, 2, 3, 4, 5, 6])),
            stft_n_fft=int(stft_cfg.get("n_fft", 4096)),
            stft_hop_length=int(stft_cfg.get("hop_length", 441)),
            cqt_fmin=float(cqt_cfg.get("fmin", 32.7)),
            cqt_n_bins=int(cqt_cfg.get("n_bins", 84)),
            cqt_bins_per_octave=int(cqt_cfg.get("bins_per_octave", 12)),
            nmf_components=int(sub.get("nmf_components", 2)),
            lgbm_n_estimators=int(sub.get("lgbm_n_estimators", 30)),
            lgbm_max_depth=int(sub.get("lgbm_max_depth", 3)),
            lgbm_learning_rate=float(sub.get("lgbm_learning_rate", 0.1)),
        )


def pitch_to_freq(pitch: int) -> float:
    """MIDI音高番号から期待される基本周波数を計算する。"""
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0))


class AudioSpectrograms:
    """1曲の波形から STFT および CQT の対数/振幅スペクトログラムを事前計算して保持する。"""

    def __init__(self, y: np.ndarray, sr: int = 44100, cfg: SampleSignatureConfig | None = None) -> None:
        self.sr = sr
        self.cfg = cfg or SampleSignatureConfig()

        if y.ndim > 1:
            y = librosa.to_mono(y.T if y.shape[0] < y.shape[1] else y)
        self.y = y.astype(np.float32)
        self.duration_s = float(len(y) / sr)

        # 1. STFT 計算
        stft_complex = librosa.stft(
            self.y,
            n_fft=self.cfg.stft_n_fft,
            hop_length=self.cfg.stft_hop_length,
        )
        self.stft_mag = np.abs(stft_complex).astype(np.float32)
        self.stft_freqs = librosa.fft_frequencies(sr=self.sr, n_fft=self.cfg.stft_n_fft)
        self.stft_times = librosa.frames_to_time(
            np.arange(self.stft_mag.shape[1]),
            sr=self.sr,
            hop_length=self.cfg.stft_hop_length,
        )

        # 2. CQT 計算
        try:
            cqt_complex = librosa.cqt(
                self.y,
                sr=self.sr,
                hop_length=self.cfg.stft_hop_length,
                fmin=self.cfg.cqt_fmin,
                n_bins=self.cfg.cqt_n_bins,
                bins_per_octave=self.cfg.cqt_bins_per_octave,
            )
            self.cqt_mag = np.abs(cqt_complex).astype(np.float32)
            self.cqt_freqs = librosa.cqt_frequencies(
                n_bins=self.cfg.cqt_n_bins,
                fmin=self.cfg.cqt_fmin,
                bins_per_octave=self.cfg.cqt_bins_per_octave,
            )
            self.cqt_times = librosa.frames_to_time(
                np.arange(self.cqt_mag.shape[1]),
                sr=self.sr,
                hop_length=self.cfg.stft_hop_length,
            )
        except Exception:
            # 非常に短い音源などで CQT が失敗した際のフォールバック
            self.cqt_mag = np.zeros((self.cfg.cqt_n_bins, len(self.stft_times)), dtype=np.float32)
            self.cqt_freqs = np.zeros(self.cfg.cqt_n_bins, dtype=np.float32)
            self.cqt_times = self.stft_times


def find_actual_f0_stft(
    spectrum: np.ndarray,
    freqs: np.ndarray,
    expected_f0: float,
    cents_tol: float = 50.0,
) -> float:
    """STFTスペクトルから、期待F0の前後 ±50 セント以内で最大のピーク周波数を探す。"""
    ratio = 2.0 ** (cents_tol / 1200.0)
    f_min = expected_f0 / ratio
    f_max = expected_f0 * ratio

    idx_min = int(np.searchsorted(freqs, f_min))
    idx_max = int(np.searchsorted(freqs, f_max))
    if idx_max <= idx_min:
        idx_max = min(len(freqs), idx_min + 1)
        idx_min = max(0, idx_max - 1)

    sub_spec = spectrum[idx_min:idx_max]
    if len(sub_spec) == 0:
        return expected_f0

    peak_idx = idx_min + int(np.argmax(sub_spec))
    return float(freqs[peak_idx])


def extract_stft_harmonics(
    spectrum: np.ndarray,
    freqs: np.ndarray,
    f_actual: float,
    harmonics: list[int],
    cents_tol: float = 50.0,
) -> np.ndarray:
    """基音および指定された倍音（2〜6倍音など）の位置からスペクトルの形を取り出す。"""
    vec = []
    ratio = 2.0 ** (cents_tol / 1200.0)
    for k in harmonics:
        fk = k * f_actual
        f_min = fk / ratio
        f_max = fk * ratio
        i_min = int(np.searchsorted(freqs, f_min))
        i_max = int(np.searchsorted(freqs, f_max))
        if i_max <= i_min:
            i_max = min(len(freqs), i_min + 1)
            i_min = max(0, i_max - 1)
        sub = spectrum[i_min:i_max]
        val = float(np.max(sub)) if len(sub) > 0 else 0.0
        vec.append(val)
    arr = np.asarray(vec, dtype=np.float32)
    # 対数振幅（log1p）
    log_arr = np.log1p(arr * 100.0)
    norm = np.linalg.norm(log_arr)
    if norm > 1e-6:
        log_arr /= norm
    return log_arr


def extract_cqt_harmonics(
    spectrum: np.ndarray,
    cqt_freqs: np.ndarray,
    f_actual: float,
    bins_per_octave: int = 12,
    span_semitones: int = 36,
) -> np.ndarray:
    """CQTスペクトルから、基音ビンを基準とした相対的なスペクトルの形を取り出す。"""
    # 基音に最も近いビンを探す
    diffs = np.abs(cqt_freqs - f_actual)
    b_actual = int(np.argmin(diffs))

    span_bins = int(span_semitones * (bins_per_octave / 12.0))
    # 基音から span_bins 分のスライス（音高が変わっても平行移動するだけ）
    sub = np.zeros(span_bins, dtype=np.float32)
    avail_len = min(span_bins, len(spectrum) - b_actual)
    if avail_len > 0 and b_actual < len(spectrum):
        sub[:avail_len] = spectrum[b_actual : b_actual + avail_len]

    # 対数振幅
    log_sub = np.log1p(sub * 100.0)
    norm = np.linalg.norm(log_sub)
    if norm > 1e-6:
        log_sub /= norm
    return log_sub


def extract_note_feature(
    spec: AudioSpectrograms,
    start_s: float,
    end_s: float,
    pitch: int,
    mode: RepresentationMode = "STFT",
    drum_class: str | None = None,
    cfg: SampleSignatureConfig | None = None,
) -> np.ndarray | None:
    """
    1つのノートから時間窓 [start_s + 150ms - 80ms, start_s + 150ms + 80ms] のスペクトル形状を取り出す。
    50ms未満のノートは None を返す。
    """
    cfg = cfg or spec.cfg
    dur = end_s - start_s
    if dur < cfg.min_duration_s:
        return None

    # 時間窓: [start_s + 0.070, start_s + 0.230]
    t_center = start_s + cfg.onset_offset_s
    t_min = max(0.0, t_center - cfg.window_margin_s)
    t_max = min(spec.duration_s, t_center + cfg.window_margin_s)
    if t_max <= t_min:
        return None

    if mode == "STFT":
        times = spec.stft_times
        mag = spec.stft_mag
        freqs = spec.stft_freqs
    else:
        times = spec.cqt_times
        mag = spec.cqt_mag
        freqs = spec.cqt_freqs

    t_idx_min = int(np.searchsorted(times, t_min))
    t_idx_max = int(np.searchsorted(times, t_max))
    if t_idx_max <= t_idx_min:
        t_idx_max = min(mag.shape[1], t_idx_min + 1)
        t_idx_min = max(0, t_idx_max - 1)

    time_slice = mag[:, t_idx_min:t_idx_max]
    if time_slice.shape[1] == 0:
        return None
    mean_spec = np.mean(time_slice, axis=1)

    # ドラムの場合（無音高）
    if pitch <= 0 or drum_class is not None:
        if mode == "STFT":
            # 6帯域（サブバス、キック、ローミッド、ミッド、ハイミッド、ハイ）にプーリングして 6 次元に統一
            n = len(mean_spec)
            step = max(1, n // 6)
            pooled = [float(np.mean(mean_spec[i : min(n, i + step)])) for i in range(0, min(n, step * 6), step)]
            while len(pooled) < 6:
                pooled.append(0.0)
            arr = np.log1p(np.asarray(pooled[:6], dtype=np.float32) * 100.0)
            norm = np.linalg.norm(arr)
            return arr / norm if norm > 1e-6 else arr
        else:
            span_bins = int(36 * (cfg.cqt_bins_per_octave / 12.0))
            sub = np.zeros(span_bins, dtype=np.float32)
            avail = min(span_bins, len(mean_spec))
            sub[:avail] = mean_spec[:avail]
            arr = np.log1p(sub * 100.0)
            norm = np.linalg.norm(arr)
            return arr / norm if norm > 1e-6 else arr

    # 有音高楽器:
    f0 = pitch_to_freq(pitch)
    if mode == "STFT":
        f_actual = find_actual_f0_stft(mean_spec, freqs, f0, cents_tol=cfg.peak_cents_tol)
        return extract_stft_harmonics(mean_spec, freqs, f_actual, cfg.harmonic_numbers, cents_tol=cfg.peak_cents_tol)
    else:
        # CQT
        ratio = 2.0 ** (cfg.peak_cents_tol / 1200.0)
        f_min = f0 / ratio
        f_max = f0 * ratio
        i_min = int(np.searchsorted(freqs, f_min))
        i_max = int(np.searchsorted(freqs, f_max))
        if i_max <= i_min:
            i_max = min(len(freqs), i_min + 1)
            i_min = max(0, i_max - 1)
        sub = mean_spec[i_min:i_max]
        peak_idx = i_min + int(np.argmax(sub)) if len(sub) > 0 else int(np.argmin(np.abs(freqs - f0)))
        f_actual = float(freqs[peak_idx]) if peak_idx < len(freqs) else f0
        return extract_cqt_harmonics(mean_spec, freqs, f_actual, bins_per_octave=cfg.cqt_bins_per_octave)


class SignatureModel:
    """第2部：見本から学び、候補をスコアリングするモデル（①平均の型、②NMF、③判別器）。"""

    def __init__(
        self,
        method: LearningMethod = "average",
        cfg: SampleSignatureConfig | None = None,
    ) -> None:
        self.method = method
        self.cfg = cfg or SampleSignatureConfig()
        self.template: np.ndarray | None = None
        self.nmf_bases: np.ndarray | None = None
        self.lgbm_model: Any = None
        self.fitted = False

    def fit(
        self,
        pos_features: list[np.ndarray],
        neg_features: list[np.ndarray] | None = None,
    ) -> None:
        """見本特徴量から学習する。"""
        if not pos_features:
            self.fitted = False
            return

        target_dim = len(pos_features[0])
        valid_pos = [f for f in pos_features if len(f) == target_dim]
        if not valid_pos:
            self.fitted = False
            return
        X_pos = np.stack(valid_pos, axis=0)  # (N, D)
        D = X_pos.shape[1]

        if self.method == "average":
            # ① 平均の型
            mean_vec = np.mean(X_pos, axis=0)
            norm = np.linalg.norm(mean_vec)
            self.template = mean_vec / norm if norm > 1e-6 else mean_vec
            self.fitted = True

        elif self.method == "nmf":
            # ② NMF
            from sklearn.decomposition import NMF

            n_components = min(self.cfg.nmf_components, X_pos.shape[0], D)
            n_components = max(1, n_components)
            # 非負確認
            X_nonneg = np.maximum(0.0, X_pos)
            nmf = NMF(
                n_components=n_components,
                init="nndsvda",
                random_state=42,
                max_iter=200,
            )
            try:
                nmf.fit(X_nonneg)
                self.nmf_bases = nmf.components_  # (n_comp, D)
                self.fitted = True
            except Exception:
                # フォールバックとして平均
                mean_vec = np.mean(X_pos, axis=0)
                norm = np.linalg.norm(mean_vec)
                self.template = mean_vec / norm if norm > 1e-6 else mean_vec
                self.fitted = True

        elif self.method == "lgbm":
            # ③ 判別器
            import lightgbm as lgb

            y_pos = np.ones(len(X_pos), dtype=np.int32)
            if neg_features and len(neg_features) > 0:
                X_neg = np.stack(neg_features, axis=0)
                y_neg = np.zeros(len(X_neg), dtype=np.int32)
                X = np.concatenate([X_pos, X_neg], axis=0)
                y = np.concatenate([y_pos, y_neg], axis=0)
            else:
                # 負例が無い場合はランダムノイズと低振幅の人工負例
                rng = np.random.RandomState(42)
                X_neg = rng.uniform(0.0, 0.5, size=(len(X_pos), D)).astype(np.float32)
                y_neg = np.zeros(len(X_neg), dtype=np.int32)
                X = np.concatenate([X_pos, X_neg], axis=0)
                y = np.concatenate([y_pos, y_neg], axis=0)

            clf = lgb.LGBMClassifier(
                n_estimators=self.cfg.lgbm_n_estimators,
                max_depth=self.cfg.lgbm_max_depth,
                learning_rate=self.cfg.lgbm_learning_rate,
                random_state=42,
                verbose=-1,
                min_child_samples=max(1, len(X) // 5),
            )
            clf.fit(X, y)
            self.lgbm_model = clf
            self.fitted = True

    def score(self, feat: np.ndarray | None) -> float:
        """1つの候補特徴量に対して「その楽器らしさ」の点数 [0.0, 1.0] を返す。"""
        if not self.fitted or feat is None:
            return 0.5

        if self.template is not None:
            if len(feat) != len(self.template):
                return 0.5
            norm = np.linalg.norm(feat)
            if norm < 1e-6:
                return 0.0
            cos = float(np.dot(feat / norm, self.template))
            return max(0.0, min(1.0, cos))

        elif self.nmf_bases is not None:
            if len(feat) != self.nmf_bases.shape[1]:
                return 0.5
            from scipy.optimize import nnls

            feat_nonneg = np.maximum(0.0, feat)
            # min || W^T h - x ||_2
            W_T = self.nmf_bases.T
            h, _r = nnls(W_T, feat_nonneg)
            recon = self.nmf_bases.T @ h
            norm_orig = np.linalg.norm(feat_nonneg)
            norm_recon = np.linalg.norm(recon)
            if norm_orig < 1e-6 or norm_recon < 1e-6:
                return 0.0
            cos = float(np.dot(feat_nonneg / norm_orig, recon / norm_recon))
            return max(0.0, min(1.0, cos))

        elif self.lgbm_model is not None:
            if hasattr(self.lgbm_model, "n_features_in_") and len(feat) != self.lgbm_model.n_features_in_:
                return 0.5
            proba = self.lgbm_model.predict_proba(feat.reshape(1, -1))[0]
            # クラス1の確率
            return float(proba[1]) if len(proba) > 1 else float(proba[0])

        return 0.5
