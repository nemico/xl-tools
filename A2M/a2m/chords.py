"""拍ごとのコード（根音と、メジャー/マイナー）を決める。

正解（納品MIDI）：伴奏の音の音名ごとの重み（鳴っている長さ）を、コードの型と比べる。根音の迷いはベースの音で決める。
推定（原曲）：歌とドラムを抜いた音の音名ごとの強さ（クロマ）を、同じ型と比べ、前後のつながりを見て決める（ビタビ）。
  ベースの音（分離音、AIの下書き、人が入れたもの）があれば、それを根音の手がかりに足す。
"""

from __future__ import annotations

import numpy as np

NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
QUALITY_INTERVALS = {"maj": (0, 4, 7), "min": (0, 3, 7)}
NO_CHORD = "N"


def chord_list(qualities: list[str]) -> list[tuple[int, str]]:
    """(根音, 型) の一覧。最後に「コード無し」(-1, 'N')。"""
    return [(r, q) for q in qualities for r in range(12)] + [(-1, NO_CHORD)]


def chord_name(chord: tuple[int, str]) -> str:
    root, q = chord
    if q == NO_CHORD:
        return NO_CHORD
    return NAMES[root] + ("m" if q == "min" else "")


def templates(chords: list[tuple[int, str]]) -> np.ndarray:
    """(コードの数, 12)。単位長さ。コード無しは平らな型。"""
    out = np.zeros((len(chords), 12))
    for i, (r, q) in enumerate(chords):
        if q == NO_CHORD:
            out[i] = 1.0
        else:
            for k in QUALITY_INTERVALS[q]:
                out[i, (r + k) % 12] = 1.0
    return out / np.linalg.norm(out, axis=1, keepdims=True)


def chord_scores(chroma: np.ndarray, chords: list[tuple[int, str]], bass: np.ndarray | None, bass_weight: float,
                 no_chord_level: float) -> np.ndarray:
    """拍ごと・コードごとの点（大きいほど合う）。chroma は (拍, 12)、bass は (拍, 12) の根音の手がかり（無ければ None）。"""
    norm = np.linalg.norm(chroma, axis=1, keepdims=True)
    c = np.where(norm > 0, chroma / np.maximum(norm, 1e-12), 0.0)
    s = c @ templates(chords).T
    no_chord = np.array([q == NO_CHORD for _, q in chords])
    s[:, no_chord] = no_chord_level
    silent = norm[:, 0] <= 0
    s[silent] = -1.0
    s[silent, no_chord] = 1.0
    if bass is not None and bass_weight > 0:
        bn = bass / np.maximum(bass.sum(axis=1, keepdims=True), 1e-12)
        roots = np.array([r for r, _ in chords])
        has = roots >= 0
        s[:, has] += bass_weight * bn[:, roots[has]]
    return s


def viterbi(scores: np.ndarray, switch_penalty: float) -> np.ndarray:
    """コードが変わるたびに switch_penalty を引いて、点の合計が最大になる並び。"""
    n, k = scores.shape
    total = scores[0].copy()
    back = np.zeros((n, k), dtype=int)
    for t in range(1, n):
        best = int(np.argmax(total))
        stay = total
        move = total[best] - switch_penalty
        back[t] = np.where(stay >= move, np.arange(k), best)
        total = np.maximum(stay, move) + scores[t]
    path = np.zeros(n, dtype=int)
    path[-1] = int(np.argmax(total))
    for t in range(n - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    return path


def label_from_notes(weights: np.ndarray, bass_pc: int | None, chords: list[tuple[int, str]], bass_bonus: float,
                     min_weight: float) -> int:
    """納品MIDIの音から正解のコードを決める（chords の番号）。音が少なければコード無し。"""
    if weights.sum() < min_weight:
        return len(chords) - 1
    s = (weights / np.linalg.norm(weights)) @ templates(chords).T
    s[-1] = -np.inf
    if bass_pc is not None:
        for i, (r, _) in enumerate(chords):
            if r == bass_pc:
                s[i] += bass_bonus
    return int(np.argmax(s))


def same_root(a: tuple[int, str], b: tuple[int, str]) -> bool:
    return a[1] != NO_CHORD and b[1] != NO_CHORD and a[0] == b[0]


def beat_chroma(y: np.ndarray, sr: int, beat_times: np.ndarray, cc: dict, bass: bool) -> np.ndarray:
    import librosa

    if bass:
        c = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=cc["hop"], fmin=librosa.note_to_hz("C1"), n_octaves=4)
    else:
        c = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=cc["hop"])  # 伴奏はドラムを抜いてあるので、打楽器成分の除去はしない
    t = librosa.frames_to_time(np.arange(c.shape[1]), sr=sr, hop_length=cc["hop"])
    rms = librosa.feature.rms(y=y, hop_length=cc["hop"])[0][: c.shape[1]]
    out = np.zeros((len(beat_times) - 1, 12))
    floor = rms.max() * 10 ** (-cc["silent_db"] / 20)
    for b in range(len(beat_times) - 1):
        sel = (t >= beat_times[b]) & (t < beat_times[b + 1])
        if sel.any() and rms[sel[: len(rms)]].mean() > floor:
            out[b] = c[:, sel].mean(axis=1)
    return out


def onehot_bass(notes: list[tuple[float, float, int]], beat_times: np.ndarray) -> np.ndarray:
    """拍ごとに、一番長く鳴っているベースの音名を 1 にする。"""
    out = np.zeros((len(beat_times) - 1, 12))
    for s, e, p in notes:
        a = max(0, int(np.searchsorted(beat_times, s, side="right")) - 1)
        z = min(len(out) - 1, int(np.searchsorted(beat_times, e, side="right")) - 1)
        for b in range(a, z + 1):
            ov = min(e, beat_times[b + 1]) - max(s, beat_times[b])
            if ov > 0:
                out[b, p % 12] += ov
    best = out.argmax(axis=1)
    hot = np.zeros_like(out)
    has = out.max(axis=1) > 0
    hot[np.arange(len(out))[has], best[has]] = 1.0
    return hot
