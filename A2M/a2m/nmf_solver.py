"""Score-informed NMF solver for estimating harmonic ratios and audio separation.

CARD_P1-22:
- V_hat[f, t] = sum_s sum_{p} g[s, p, t] * sum_k a[s, k] * B(f, k * f0(p)) + R[f, t]
- B(f, freq): triangular window +/- 50 cents around freq
- Multiplicative updates (KL divergence) for a, g (H), and R
- Residual R absorbs drums and noise
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import torch


@dataclass
class NMFResult:
    """NMF 求解結果."""

    harmonic_ratios: np.ndarray  # (n_sources, K) 各音源の倍音比 (合計1に正規化)
    W: np.ndarray  # (F, J) 各コンポーネントのスペクトル基底
    H: np.ndarray  # (J, T) 各コンポーネントのアクティベーション (音量 g)
    R: np.ndarray  # (F, T) 残差スペクトログラム
    V_hat: np.ndarray  # (F, T) 再構成スペクトログラム
    losses: list[float]  # 各反復における KL ダイバージェンス損失履歴
    component_sources: np.ndarray  # (J,) 各コンポーネントが属する音源インデックス


def triangular_basis(
    freq: float,
    freq_bins: np.ndarray,
    half_width_cents: float = 50.0,
) -> np.ndarray:
    """中心周波数 freq、幅 +/- half_width_cents の三角窓基底ベクトル (F,) を計算する。

    離散ビン抜け対策として、半値幅が周波数ビンの解像度 delta_nu より小さくならないように下限を設ける。
    """
    delta_nu = freq_bins[1] - freq_bins[0]
    hw_hz = freq * (2.0 ** (half_width_cents / 1200.0) - 1.0)
    hw_hz = max(hw_hz, delta_nu * 0.75)

    basis = np.maximum(0.0, 1.0 - np.abs(freq_bins - freq) / hw_hz)
    total = np.sum(basis)
    if total > 0.0:
        basis /= total
    return basis


def precompute_pitch_templates(
    pitches: Sequence[int],
    n_fft: int = 4096,
    sr: int = 44100,
    K: int = 12,
) -> dict[int, np.ndarray]:
    """音高 p ごとに、倍音 k=1..K の三角窓基底行列 T_p (F, K) を事前計算する。"""
    freq_bins = np.fft.rfftfreq(n_fft, 1.0 / sr)
    templates: dict[int, np.ndarray] = {}
    for p in pitches:
        f0 = 440.0 * (2.0 ** ((p - 69) / 12.0))
        T_p = np.zeros((len(freq_bins), K), dtype=np.float32)
        for k in range(1, K + 1):
            h_freq = k * f0
            if h_freq < sr / 2.0:
                T_p[:, k - 1] = triangular_basis(h_freq, freq_bins, half_width_cents=50.0)
        templates[p] = T_p
    return templates


def fit_score_informed_nmf(
    V: np.ndarray,
    components: list[tuple[int, int, np.ndarray]],
    n_sources: int,
    n_fft: int = 4096,
    sr: int = 44100,
    K: int = 12,
    n_iter: int = 100,
    seed: int = 42,
    device: str | None = None,
) -> NMFResult:
    """楽譜情報付き NMF (Score-informed NMF) を解く。

    Parameters
    ----------
    V : (F, T) 観測振幅スペクトログラム
    components : 各コンポーネント (source_idx, pitch, active_mask_1d) のリスト。
                 active_mask_1d は length T の bool 配列。
    n_sources : 音源数 S
    n_fft : STFT 窓長 (default: 4096)
    sr : サンプリングレート (default: 44100)
    K : 倍音次数 (default: 12)
    n_iter : 反復回数 (default: 100)
    seed : 乱数シード (default: 42)
    device : 'cuda' または 'cpu' (None の場合は利用可能なら cuda)

    Returns
    -------
    NMFResult
    """
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    dev = torch.device(device)

    F, T = V.shape
    J = len(components)

    if J == 0:
        raise ValueError("components list cannot be empty")

    # ユニークな音高の基底テンプレートを事前計算
    unique_pitches = sorted(list({p for _, p, _ in components}))
    templates = precompute_pitch_templates(unique_pitches, n_fft=n_fft, sr=sr, K=K)

    # テンソルの準備
    # T_all: (J, F, K)
    T_all = np.zeros((J, F, K), dtype=np.float32)
    active_mask = np.zeros((J, T), dtype=np.float32)
    comp_s = np.zeros(J, dtype=np.int64)

    for j, (s, p, mask) in enumerate(components):
        T_all[j] = templates[p]
        active_mask[j] = mask.astype(np.float32)
        comp_s[j] = s

    # PyTorch テンソルに変換して指定デバイスに転送
    t_T_all = torch.from_numpy(T_all).to(dev)  # (J, F, K)
    t_mask = torch.from_numpy(active_mask).to(dev)  # (J, T)
    t_comp_s = torch.from_numpy(comp_s).to(dev)  # (J,)
    t_V = torch.from_numpy(V.astype(np.float32)).to(dev)  # (F, T)

    # 初期値の設定
    # a[s, k] = 1/k, 合計1に正規化
    inv_k = np.array([1.0 / k for k in range(1, K + 1)], dtype=np.float32)
    inv_k /= inv_k.sum()
    a_init = np.tile(inv_k, (n_sources, 1))
    t_a = torch.from_numpy(a_init).to(dev)  # (S, K)

    # g (H) の初期値 = 1.0 (マスク外は 0.0)
    t_H = torch.ones((J, T), dtype=torch.float32, device=dev) * t_mask

    # R の初期値 = 小さい乱数 (種を固定)
    torch.manual_seed(seed)
    np_rng = np.random.default_rng(seed)
    r_init = np_rng.exponential(scale=1e-3, size=(F, T)).astype(np.float32)
    t_R = torch.from_numpy(r_init).to(dev)

    eps = 1e-7
    losses: list[float] = []

    # 各音源に属するコンポーネントインデックスのリスト
    source_j_indices = [
        torch.where(t_comp_s == s)[0] for s in range(n_sources)
    ]

    # 反復更新ループ
    for it in range(n_iter):
        # 1. W の計算: W[:, j] = T_all[j] @ a[comp_s[j]]
        # t_a[t_comp_s]: (J, K)
        # einsum: (J, F, K) * (J, K) -> (F, J)
        t_W = torch.einsum("jfk,jk->fj", t_T_all, t_a[t_comp_s])

        # 2. V_hat の計算
        t_V_hat = torch.clamp(t_W @ t_H + t_R, min=eps)

        # 損失の記録 (初回、10反復ごと、最終回)
        if it == 0 or (it + 1) % 10 == 0 or it == n_iter - 1:
            kl = float(
                torch.sum(
                    t_V * torch.log(torch.clamp(t_V, min=eps) / t_V_hat) - t_V + t_V_hat
                ).item()
            )
            losses.append(kl)

        # 比率行列 X
        t_X = t_V / t_V_hat

        # 3. R の更新: R <- R * X
        t_R = torch.clamp(t_R * t_X, min=eps)

        # 4. H の更新
        # num_H = W^T @ X (J, T)
        # denom_H = sum_f W[f, j] (J, 1)
        num_H = t_W.T @ t_X
        denom_H = torch.clamp(t_W.sum(dim=0, keepdim=True).T, min=eps)
        t_H = t_H * (num_H / denom_H)
        t_H = t_H * t_mask  # 制約: 非発声区間は厳密に 0

        # 5. X の再計算
        t_V_hat = torch.clamp(t_W @ t_H + t_R, min=eps)
        t_X = t_V / t_V_hat

        # 6. a の更新
        # Y = X @ H^T (F, J)
        t_Y = t_X @ t_H.T
        for s in range(n_sources):
            j_idx = source_j_indices[s]
            if len(j_idx) == 0:
                continue
            # 分子: sum_{j in s} T_j^T @ Y[:, j] -> (K,)
            # t_T_all[j_idx]: (N_j, F, K), t_Y[:, j_idx].T: (N_j, F)
            # einsum: (N_j, F, K) * (N_j, F) -> (K,)
            sub_T = t_T_all[j_idx]
            sub_Y = t_Y[:, j_idx].T
            num_a = torch.einsum("nfk,nf->k", sub_T, sub_Y)

            # 分母: sum_{j in s} (sum_f T_j[f, k]) * (sum_t H[j, t])
            # sum_T_f: (N_j, K), sum_H_t: (N_j,)
            sum_T_f = sub_T.sum(dim=1)  # (N_j, K)
            sum_H_t = t_H[j_idx].sum(dim=1)  # (N_j,)
            denom_a = torch.einsum("nk,n->k", sum_T_f, sum_H_t)
            denom_a = torch.clamp(denom_a, min=eps)

            t_a[s] = t_a[s] * (num_a / denom_a)
            # 合計1に正規化
            sum_a = t_a[s].sum()
            if sum_a > 0:
                t_a[s] = t_a[s] / sum_a
            else:
                t_a[s] = torch.from_numpy(inv_k).to(dev)

    # 最終的な W, V_hat を計算
    t_W = torch.einsum("jfk,jk->fj", t_T_all, t_a[t_comp_s])
    t_V_hat = torch.clamp(t_W @ t_H + t_R, min=eps)

    return NMFResult(
        harmonic_ratios=t_a.cpu().numpy(),
        W=t_W.cpu().numpy(),
        H=t_H.cpu().numpy(),
        R=t_R.cpu().numpy(),
        V_hat=t_V_hat.cpu().numpy(),
        losses=losses,
        component_sources=comp_s,
    )


def cosine_similarity(u: np.ndarray, v: np.ndarray) -> float:
    """2つのベクトルのコサイン類似度を計算する。"""
    nu = np.linalg.norm(u)
    nv = np.linalg.norm(v)
    if nu == 0.0 or nv == 0.0:
        return 0.0
    return float(np.dot(u, v) / (nu * nv))
