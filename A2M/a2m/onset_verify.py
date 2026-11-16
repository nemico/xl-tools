"""原曲と render の onset 突き合わせ（相互相関を使わない確認）。"""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

from a2m.align import AlignConfig, hop_seconds


@dataclass(frozen=True)
class OnsetMatchStats:
    median_ms: float
    q25_ms: float
    q75_ms: float
    n_pairs: int


def _top_onset_times(y: np.ndarray, cfg: AlignConfig, n: int) -> tuple[np.ndarray, np.ndarray]:
    hop = cfg.hop
    sr = cfg.sr
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    frames = librosa.onset.onset_detect(
        onset_envelope=oenv,
        sr=sr,
        hop_length=hop,
        units="frames",
        backtrack=False,
    )
    if frames.size == 0:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
    strengths = oenv[np.asarray(frames, dtype=int)]
    order = np.argsort(strengths)[::-1]
    frames = frames[order]
    strengths = strengths[order]
    seen: set[int] = set()
    times: list[float] = []
    strs: list[float] = []
    hop_s = hop_seconds(cfg)
    for fr, st in zip(frames, strengths, strict=False):
        if fr in seen:
            continue
        seen.add(int(fr))
        times.append(float(fr) * hop_s)
        strs.append(float(st))
        if len(times) >= n:
            break
    return np.asarray(times, dtype=np.float64), np.asarray(strs, dtype=np.float64)


def match_top_onsets(
    audio_y: np.ndarray,
    partner_y: np.ndarray,
    cfg: AlignConfig,
    *,
    n: int = 50,
    max_pair_s: float = 0.5,
) -> OnsetMatchStats:
    ta, _ = _top_onset_times(audio_y, cfg, n)
    tb, _ = _top_onset_times(partner_y, cfg, n)
    if ta.size == 0 or tb.size == 0:
        return OnsetMatchStats(0.0, 0.0, 0.0, 0)
    diffs_ms: list[float] = []
    used: set[int] = set()
    for t in np.sort(ta):
        dist = np.abs(tb - t)
        j = int(np.argmin(dist))
        if j in used or float(dist[j]) > max_pair_s:
            continue
        used.add(j)
        diffs_ms.append((float(tb[j]) - float(t)) * 1000.0)
    if not diffs_ms:
        return OnsetMatchStats(0.0, 0.0, 0.0, 0)
    arr = np.asarray(diffs_ms, dtype=np.float64)
    return OnsetMatchStats(
        median_ms=float(np.median(arr)),
        q25_ms=float(np.percentile(arr, 25)),
        q75_ms=float(np.percentile(arr, 75)),
        n_pairs=int(arr.size),
    )
