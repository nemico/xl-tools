"""コード認識：lv-chordia（ISMIR 2019、大語彙。セブンス・テンション・転回形まで）に、分かっているベースを入れる。

lv-chordia は、フレームごとに「三和音」「ベース音」「7th」「9th」「11th」「13th」の確率を出し、
コード辞書の中から、それらの積が一番大きく、つながりの良い並びを選ぶ（HMM）。
ここでは「ベース音」の確率を、MIDI のベース（人が入れたもの、またはAIの下書き）で差し替えられるようにする。
"""

from __future__ import annotations

import importlib.resources
import os
from pathlib import Path

import numpy as np

from a2m.chords import NAMES

INTERVAL_NAMES = {0: "1", 1: "b2", 2: "2", 3: "b3", 4: "3", 5: "4", 6: "b5", 7: "5", 8: "b6", 9: "6", 10: "b7", 11: "7"}
# 正解を付けるときのコードの型（mir_eval の書き方）と、根音からの音程
QUALITIES = {
    "maj": (0, 4, 7), "min": (0, 3, 7), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "min7": (0, 3, 7, 10),
    "dim": (0, 3, 6), "aug": (0, 4, 8), "sus4": (0, 5, 7), "sus2": (0, 2, 7), "hdim7": (0, 3, 6, 10), "dim7": (0, 3, 6, 9),
    "maj6": (0, 4, 7, 9), "min6": (0, 3, 7, 9),
}


def frame_rate() -> tuple[int, int]:
    from lv_chordia.settings import DEFAULT_HOP_LENGTH, DEFAULT_SR

    return DEFAULT_SR, DEFAULT_HOP_LENGTH


def chord_probs(audio_path: Path) -> list[np.ndarray]:
    """5つのモデルの平均の確率（三和音、ベース、7th、9th、11th、13th）。フレームは hop/sr 秒ごと。"""
    from lv_chordia.chord_recognition import MODEL_NAMES
    from lv_chordia.chordnet_ismir_naive import ChordNet
    from lv_chordia.extractors.cqt import CQTV2
    from lv_chordia.mir import DataEntry, io
    from lv_chordia.mir.nn.train import NetworkInterface

    sr, hop = frame_rate()
    entry = DataEntry()
    entry.prop.set("sr", sr)
    entry.prop.set("hop_length", hop)
    entry.append_file(os.path.abspath(audio_path), io.MusicIO, "music")
    entry.append_extractor(CQTV2, "cqt")
    probs = [NetworkInterface(ChordNet(None), name, load_checkpoint=False).inference(entry.cqt) for name in MODEL_NAMES]
    return [np.mean([p[i] for p in probs], axis=0) for i in range(len(probs[0]))]


def bass_track(n_frames: int, notes: list[tuple[float, float, int]], frame_s: float) -> np.ndarray:
    """フレームごとに鳴っているベースの音名（無ければ -1）。重なったら低いほう。"""
    out = np.full(n_frames, -1, dtype=int)
    low = np.full(n_frames, 999, dtype=int)
    for s, e, p in notes:
        a, z = max(0, int(round(s / frame_s))), min(n_frames, int(round(e / frame_s)))
        sel = slice(a, z)
        better = p < low[sel]
        low[sel] = np.where(better, p, low[sel])
    has = low < 999
    out[has] = low[has] % 12
    return out


def inject_bass(probs: list[np.ndarray], bass_pc: np.ndarray, weight: float) -> list[np.ndarray]:
    """ベース音の確率を、分かっているベースで差し替える（weight=1 で完全に差し替え、0 で何もしない）。

    ベースが鳴っていないフレームは、モデルの確率のまま。
    """
    pb = probs[1].copy()
    has = bass_pc >= 0
    if has.any() and weight > 0:
        hot = np.zeros((int(has.sum()), pb.shape[1]))
        hot[np.arange(hot.shape[0]), bass_pc[has] + 1] = 1.0  # 0番は「ベース無し」
        mixed = (1 - weight) * pb[has] + weight * hot
        floor = 1e-4  # log(0) を避ける
        pb[has] = (mixed + floor) / (mixed + floor).sum(axis=1, keepdims=True)
    return [probs[0], pb, *probs[2:]]


def decode(probs: list[np.ndarray], dict_name: str) -> list[tuple[float, float, str]]:
    """確率から、(始まり, 終わり, コード名) の並び。コード名は mir_eval の書き方（例 "A:min7/b3"）。"""
    from lv_chordia.extractors.xhmm_ismir import XHMMDecoder

    sr, hop = frame_rate()
    with importlib.resources.path("lv_chordia.data", f"{dict_name}_chord_list.txt") as f:
        hmm = XHMMDecoder(template_file=str(f))

    class _Entry:
        class prop:  # noqa: N801
            pass

    _Entry.prop.hop_length = hop
    _Entry.prop.sr = sr
    return [(float(a), float(b), str(c)) for a, b, c in hmm.decode_to_chordlab(_Entry, probs, False)]


def label_from_notes(weights: np.ndarray, bass_pc: int | None, cfg: dict) -> str:
    """納品MIDIの音（音名ごとの重み）とベースから、正解のコード名を決める（mir_eval の書き方）。"""
    total = float(weights.sum())
    if total < cfg["min_weight"]:
        return "N"
    present = weights >= cfg["present_ratio"] * weights.max()
    best, best_score = "N", -np.inf
    for q, ivs in QUALITIES.items():
        for root in range(12):
            pcs = {(root + i) % 12 for i in ivs}
            inside = sum(weights[p] for p in pcs)
            outside = total - inside
            missing = sum(1 for p in pcs if not present[p])
            score = inside - cfg["outside_cost"] * outside - cfg["missing_cost"] * missing * total / len(pcs)
            score -= cfg["tone_cost"] * (len(pcs) - 3) * total
            if bass_pc is not None:
                if bass_pc == root:
                    score += cfg["bass_root_bonus"] * total
                elif bass_pc in pcs:
                    score += cfg["bass_tone_bonus"] * total
            if score > best_score:
                best_score = score
                label = f"{NAMES[root]}:{q}"
                if bass_pc is not None and bass_pc != root and bass_pc in pcs:
                    label += "/" + INTERVAL_NAMES[(bass_pc - root) % 12]
                best = label
    return best


def chord_pitch_classes(label: str) -> list[int]:
    """コード名（mir_eval の書き方）の構成音の音名。ベースが構成音の外（分数コード）なら、それも入れる。"""
    import mir_eval

    if label in ("N", "X"):
        return []
    root, bitmap, bass = mir_eval.chord.encode(label)
    pcs = {(root + i) % 12 for i in range(12) if bitmap[i]}
    pcs.add((root + bass) % 12)
    return sorted(pcs)


def display_name(label: str) -> str:
    """mir_eval の書き方を、普通のコード表記にする（例 "A:min7/b3" → "Am7/C"）。"""
    import mir_eval

    if label in ("N", "X"):
        return ""
    head, _, inv = label.partition("/")
    root, _, quality = head.partition(":")
    quality = quality or "maj"
    names = {"maj": "", "min": "m", "7": "7", "maj7": "M7", "min7": "m7", "dim": "dim", "aug": "aug", "sus4": "sus4",
             "sus2": "sus2", "hdim7": "m7-5", "dim7": "dim7", "maj6": "6", "min6": "m6", "9": "9", "maj9": "M9",
             "min9": "m9", "11": "11", "13": "13", "sus4(b7)": "7sus4", "minmaj7": "mM7"}
    text = root.replace("b", "♭") + names.get(quality, quality)
    if inv:
        r, _, _ = mir_eval.chord.encode(label)
        bass_pc = (r + mir_eval.chord.scale_degree_to_semitone(inv)) % 12
        text += "/" + NAMES[bass_pc].replace("b", "♭")
    return text
