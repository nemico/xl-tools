"""コピペで済む小節を見つける。

正解（納品MIDI）：パートの全トラックについて、小節の中の音（トラック、小節内の位置、音高）が、
  前のどこかの小節とまったく同じなら「コピペできる」。
推定（分離音）：小節を拍の4分の1ずつに区切った音の特徴を並べたものを、小節どうしで比べる（相関）。
  同じ距離（何小節前か）で、相関が閾値を超える小節が min_run 小節以上続いたら、その範囲を「コピペ候補」とする。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CopyRegion:
    start_bar: int  # この小節から
    end_bar: int  # この小節まで（含む）
    lag: int  # 何小節前と同じか
    score: float  # 範囲の相関の平均


# ---------------------------------------------------------------------------
# 正解
# ---------------------------------------------------------------------------


def bar_keys(notes: list[tuple[int, int, int]], bar_ticks: np.ndarray) -> list[tuple | None]:
    """notes は (トラック番号, 始まりの tick, 音高)。小節ごとの中身（音が無い小節は None）。"""
    content: list[list[tuple[int, int, int]]] = [[] for _ in range(len(bar_ticks))]
    for track, tick, pitch in notes:
        b = int(np.searchsorted(bar_ticks, tick, side="right") - 1)
        if 0 <= b < len(bar_ticks):
            content[b].append((track, tick - int(bar_ticks[b]), pitch))
    return [tuple(sorted(c)) if c else None for c in content]


def same_bar(keys: list[tuple | None], a: int, b: int) -> bool:
    return 0 <= a < len(keys) and 0 <= b < len(keys) and keys[a] is not None and keys[a] == keys[b]


def truth_copy_bars(keys: list[tuple | None], min_run: int) -> set[int]:
    """前のどこかと同じ距離で、min_run 小節以上まとめてコピーできる小節。"""
    out: set[int] = set()
    n = len(keys)
    for lag in range(1, n):
        run: list[int] = []
        for b in range(lag, n + 1):
            if b < n and same_bar(keys, b - lag, b):
                run.append(b)
                continue
            if len(run) >= min_run:
                out.update(run)
            run = []
    return out


# ---------------------------------------------------------------------------
# 推定
# ---------------------------------------------------------------------------


def frame_features(y: np.ndarray, sr: int, kind: str, rp: dict) -> tuple[np.ndarray, np.ndarray]:
    """(特徴, フレームの時刻)。ドラムは対数メル、音程のあるパートは対数 CQT。"""
    import librosa

    hop = rp["hop"]
    if kind == "drums":
        s = librosa.feature.melspectrogram(y=y, sr=sr, n_fft=rp["n_fft"], hop_length=hop, n_mels=rp["n_mels"])
    else:
        s = np.abs(librosa.cqt(y, sr=sr, hop_length=hop, fmin=librosa.note_to_hz(rp["cqt_fmin"]), n_bins=rp["cqt_bins"])) ** 2
    f = np.log1p(rp["log_gain"] * s / (s.max() + 1e-12))
    return f, librosa.frames_to_time(np.arange(f.shape[1]), sr=sr, hop_length=hop)


def bar_energy_active(y: np.ndarray, sr: int, bar_times: np.ndarray, active_db: float) -> np.ndarray:
    """小節ごとに、パートが鳴っているか（一番大きい小節から active_db 以内）。"""
    energy = []
    for a, b in zip(bar_times[:-1], bar_times[1:]):
        i, j = int(a * sr), int(b * sr)
        energy.append(10 * np.log10(np.mean(y[i:max(j, i + 1)] ** 2) + 1e-12) if 0 <= i < len(y) else -120.0)
    e = np.array(energy)
    return e > e.max() - active_db


def bar_features(frames: np.ndarray, frame_times: np.ndarray, bar_times: np.ndarray, bar_beats: np.ndarray,
                 slots_per_beat: int) -> list[np.ndarray | None]:
    """frames は (特徴の次元, フレーム)。小節を拍の 1/slots_per_beat に区切り、区切りごとの平均を並べる。"""
    out: list[np.ndarray | None] = []
    for b in range(len(bar_times) - 1):
        n_slots = int(round(bar_beats[b] * slots_per_beat))
        edges = np.linspace(bar_times[b], bar_times[b + 1], n_slots + 1)
        idx = np.searchsorted(frame_times, edges)
        if idx[-1] <= idx[0] or n_slots == 0:
            out.append(None)
            continue
        cols = []
        for k in range(n_slots):
            a, z = idx[k], max(idx[k + 1], idx[k] + 1)
            cols.append(frames[:, a:z].mean(axis=1))
        out.append(np.stack(cols, axis=1))
    return out


def similarity(feats: list[np.ndarray | None], active: np.ndarray) -> np.ndarray:
    """小節どうしの相関（同じ長さの小節どうしだけ。鳴っていない小節は -1）。"""
    n = len(feats)
    flat = []
    for f in feats:
        if f is None:
            flat.append(None)
            continue
        v = f.ravel().astype(np.float64)
        v = v - v.mean()
        norm = np.linalg.norm(v)
        flat.append(v / norm if norm > 0 else None)
    sim = np.full((n, n), -1.0)
    for i in range(n):
        if flat[i] is None or not active[i]:
            continue
        for j in range(i):
            if flat[j] is not None and active[j] and flat[j].shape == flat[i].shape:
                sim[i, j] = sim[j, i] = float(flat[i] @ flat[j])
    return sim


def find_copy_regions(sim: np.ndarray, threshold: float, min_run: int, max_lag: int | None = None) -> list[CopyRegion]:
    """同じ距離で相関が threshold 以上の小節が min_run 以上続く範囲。重なったら相関の高いほうを残す。"""
    n = sim.shape[0]
    cands: list[CopyRegion] = []
    for lag in range(1, min(n, (max_lag or n) + 1)):
        run: list[int] = []
        for b in range(lag, n + 1):
            if b < n and sim[b, b - lag] >= threshold:
                run.append(b)
                continue
            if len(run) >= min_run:
                cands.append(CopyRegion(run[0], run[-1], lag, float(np.mean([sim[x, x - lag] for x in run]))))
            run = []
    taken = np.zeros(n, dtype=bool)
    out: list[CopyRegion] = []
    for r in sorted(cands, key=lambda r: (-(r.end_bar - r.start_bar + 1) * r.score, r.lag)):
        free = [b for b in range(r.start_bar, r.end_bar + 1) if not taken[b]]
        # 重なりで削れた残りが短すぎたら捨てる。残りは連続した範囲に切り直す
        for seg in _runs(free):
            if len(seg) >= min_run:
                taken[seg[0] : seg[-1] + 1] = True
                out.append(CopyRegion(seg[0], seg[-1], r.lag, float(np.mean([sim[x, x - r.lag] for x in seg]))))
    return sorted(out, key=lambda r: r.start_bar)


def _runs(bars: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for b in bars:
        if out and b == out[-1][-1] + 1:
            out[-1].append(b)
        else:
            out.append([b])
    return out


def score_regions(regions: list[CopyRegion], keys: list[tuple | None], truth: set[int], notes_per_bar: list[int]) -> dict[str, float]:
    """推定した範囲の当たり具合。小節の数と、そこに入っている音の数で数える。"""
    pred_ok = pred_bad = 0
    notes_ok = notes_bad = 0
    saved = 0
    hit: set[int] = set()
    for r in regions:
        for b in range(r.start_bar, r.end_bar + 1):
            # コピペした後に直す音（足りない音と余分な音）と、ゼロから打つ音の数の差
            src = set(keys[b - r.lag] or ())
            dst = set(keys[b] or ())
            saved += len(dst) - len(src ^ dst)
            if same_bar(keys, b - r.lag, b):
                pred_ok += 1
                notes_ok += notes_per_bar[b]
                hit.add(b)
            else:
                pred_bad += 1
                notes_bad += notes_per_bar[b]
    return {
        "pred_ok": pred_ok, "pred_bad": pred_bad, "truth": len(truth), "truth_hit": len(truth & hit),
        "notes_ok": notes_ok, "notes_bad": notes_bad, "notes_total": int(sum(notes_per_bar)), "saved": saved,
    }
