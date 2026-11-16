"""採譜の5秒の区切りごとの時刻の癖を、テンポのグリッドを目安に取る（クオンタイズではない）。

MuScriptor は音を5秒ずつ区切って採譜し、区切りごとに音全体が前後に ±10〜30ms ずれることがある
（区切りの位置を2.5秒ずらして同じ音を採譜すると、同じ音の時刻が変わる）。
区切りごとに、16分の線に近い音（window_s 以内）の「線からの差」の中央値を取り、区切りの音全部から引く。
区切りの中の音どうしの位置関係（3連符、つっこみ・もたり）は変えない。
"""

from __future__ import annotations

import numpy as np


def subdivide(grid_times: np.ndarray, div: int) -> np.ndarray:
    """4分音符ごとのグリッド（秒）を div 等分した線（秒）。"""
    g = np.asarray(grid_times, dtype=float)
    if len(g) < 2:
        return g.copy()
    frac = np.arange(div) / div
    lines = (g[:-1, None] + np.diff(g)[:, None] * frac[None, :]).ravel()
    return np.append(lines, g[-1])


def nearest_diff(times: np.ndarray, lines: np.ndarray) -> np.ndarray:
    """times の各時刻から一番近い線までの差（時刻 − 線）。線が無ければ inf。"""
    times = np.asarray(times, dtype=float)
    if len(lines) == 0:
        return np.full(len(times), np.inf)
    i = np.clip(np.searchsorted(lines, times), 1, max(len(lines) - 1, 1))
    left = times - lines[np.maximum(i - 1, 0)]
    right = times - lines[np.minimum(i, len(lines) - 1)]
    return np.where(np.abs(left) < np.abs(right), left, right)


def chunk_offsets(starts: np.ndarray, lines: np.ndarray, chunk_s: float, window_s: float, min_matches: int) -> np.ndarray:
    """音ごとに、その音の区切りの「線からの差」の中央値（線に近い音が min_matches に満たない区切りは 0）。"""
    starts = np.asarray(starts, dtype=float)
    d = nearest_diff(starts, lines)
    chunk = np.floor(starts / chunk_s).astype(int)
    out = np.zeros(len(starts))
    for c in np.unique(chunk):
        sel = chunk == c
        ok = sel & (np.abs(d) <= window_s)
        if ok.sum() >= min_matches:
            out[sel] = np.median(d[ok])
    return out


def chunk_corrections(starts: np.ndarray, lines: np.ndarray, cfg: dict) -> np.ndarray:
    """音ごとに引く量（秒）。cfg["fit"] が "offset" なら区切りごとの中央値、"linear" なら区切りの中で
    「線からの差」を時刻の1次式で近似した値（線に近い音が cfg["linear_min_matches"] 以上ある区切りだけ。足りなければ中央値）。"""
    starts = np.asarray(starts, dtype=float)
    off = chunk_offsets(starts, lines, cfg["chunk_s"], cfg["window_s"], cfg["min_matches"])
    if cfg.get("fit", "offset") != "linear":
        return off
    d = nearest_diff(starts, lines)
    chunk = np.floor(starts / cfg["chunk_s"]).astype(int)
    out = off.copy()
    for c in np.unique(chunk):
        sel = chunk == c
        x = starts - (c + 0.5) * cfg["chunk_s"]
        ok = sel & (np.abs(d - off) <= cfg["window_s"])  # 中央値を引いたあとで線に近い音
        if ok.sum() < cfg["linear_min_matches"]:
            continue
        b, a = np.polyfit(x[ok], d[ok], 1)
        keep = ok & (np.abs(d - (a + b * x)) <= cfg["window_s"])  # 外れた音を除いてもう一度
        if keep.sum() >= cfg["linear_min_matches"]:
            b, a = np.polyfit(x[keep], d[keep], 1)
        out[sel] = a + b * x[sel]
    return out


def remove_chunk_offsets(notes: list[tuple[float, float, int]], grid_times: np.ndarray, cfg: dict) -> list[tuple[float, float, int]]:
    """(頭, 終わり, 音高)（秒。採譜に渡した音の頭が 0）から、区切りごとのずれを引く。長さは変えない。"""
    if not notes:
        return []
    lines = subdivide(grid_times, cfg["grid_div"])
    starts = np.array([n[0] for n in notes])
    off = chunk_corrections(starts, lines, cfg)
    return [(float(s - o), float(e - o), p) for o, (s, e, p) in zip(off, notes)]


def stagger_pads(chunk_s: float, n_pass: int) -> list[float]:
    """区切りの位置をずらして n_pass 回採譜するときの、頭に足す無音の長さ（秒）。"""
    return [k * chunk_s / n_pass for k in range(n_pass)]


def merge_staggered(passes: list[tuple[float, list[tuple[float, float, int]]]], chunk_s: float, keep_from_s: float,
                    dedupe_s: float) -> list[tuple[float, float, int]]:
    """区切りをずらした採譜（(足した無音の秒, 元の時刻に戻した音の一覧) の並び）を1つにする。
    それぞれの採譜からは、音の頭がその採譜の区切りの [keep_from_s, keep_from_s + chunk_s / 回数) にある音だけを取る
    （MuScriptor は区切りの前のほうが時刻が正確）。これで時刻の全部が、どれか1つの採譜にちょうど割り当たる。
    境目で同じ音高の音が dedupe_s 以内に2つ来たら、あとの方を捨てる。"""
    span = chunk_s / len(passes)
    kept = []
    for pad, notes in passes:
        for s, e, p in notes:
            if (np.mod(s + pad - keep_from_s, chunk_s)) < span:
                kept.append((s, e, p))
    kept.sort(key=lambda n: (n[2], n[0]))
    out: list[tuple[float, float, int]] = []
    for n in kept:
        if out and out[-1][2] == n[2] and n[0] - out[-1][0] < dedupe_s:
            continue
        out.append(n)
    return sorted(out)


def transcribe_staggered(transcribe, wav: np.ndarray, sr: int, cfg: dict) -> list[tuple[float, float, int]]:
    """区切りの位置を cfg["passes"] 通りにずらして採譜し、それぞれの区切りの前のほうの音だけを合わせる。
    transcribe(wav) は (頭, 終わり, 音高)（秒）の一覧を返す関数。passes が 1 ならそのまま1回。"""
    if cfg["passes"] <= 1:
        return transcribe(wav)
    runs = []
    for pad in stagger_pads(cfg["chunk_s"], cfg["passes"]):
        x = np.concatenate([np.zeros(int(round(pad * sr)), dtype=wav.dtype), wav])
        runs.append((pad, [(s - pad, e - pad, p) for s, e, p in transcribe(x)]))
    return merge_staggered(runs, cfg["chunk_s"], cfg["keep_from_s"], cfg["dedupe_s"])

