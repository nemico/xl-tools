"""M4 の学習・評価データ：納品MIDIから、パートごとの正解ノート（音源の秒）を作る。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from a2m.karaoke_midi import load_karaoke_midi
from a2m.m4_tokens import DrumHit, PitchedNote, stack_drum_hits


def refine_offset(offset_s: float, drum_times: np.ndarray, onset_times: np.ndarray, window_s: float) -> float:
    """位置合わせのオフセットを、ドラムの立ち上がりで微調整する（立ち上がり − 納品の打点 の中央値を足す）。"""
    if len(drum_times) == 0 or len(onset_times) == 0:
        return offset_s
    t = np.asarray(drum_times) + offset_s
    idx = np.clip(np.searchsorted(onset_times, t), 1, len(onset_times) - 1)
    d = np.where(np.abs(onset_times[idx - 1] - t) < np.abs(onset_times[idx] - t), onset_times[idx - 1] - t, onset_times[idx] - t)
    d = d[np.abs(d) <= window_s]
    return offset_s + (float(np.median(d)) if len(d) >= 10 else 0.0)


def extra_bass_notes(all_notes: list, main: list, cfg: dict[str, Any]) -> list:
    """ベース本体（ch2）以外のトラックのうち、名前がベースのもののノートを足す。

    本体が鳴っている所と重なるノート（重ね用）は入れない。本体が休んでいる所を弾いているものだけ入れる。
    """
    extra = [n for n in all_notes if n.role == "other" and any(k in n.track_name.lower() for k in cfg["name_keys"])]
    if not extra:
        return []
    if not main:
        return extra
    starts = np.array([n.start_s for n in main])
    ends = np.array([n.end_s for n in main])
    gap = cfg["gap_s"]
    return [n for n in extra if not np.any((starts - gap <= n.start_s) & (n.start_s < ends + gap))]


def song_targets(
    midi_path: Path, offset_s: float, m4: dict[str, Any], program_of: dict[str, int], onset_times: np.ndarray | None = None,
    detail: bool = False,
) -> tuple[dict[str, tuple[list[PitchedNote], list[DrumHit]]], float]:
    """パートごとの (音程のあるノート, ドラムの打点) と、使ったオフセット。時刻は音源の秒。

    ドラムは、detail=False ならクラスの代表音高（採点用）。detail=True なら、ハイハットの開閉やタムの高さなど
    納品の音高を残す（学習用。`m4.drum_detail` のクラスで、音高が GM の範囲にあるもの）。
    """
    midi = load_karaoke_midi(midi_path)
    drum_roles = set(m4["parts"]["drums"]["roles"])
    keep = m4["drum_detail"]

    def drum_key(n) -> str:
        if detail and n.drum_class in keep["classes"] and keep["min"] <= n.pitch <= keep["max"]:
            return str(n.pitch)
        return n.drum_class

    raw_drums = [(n.start_s, drum_key(n)) for n in midi.notes if n.role in drum_roles and n.drum_class in m4["drum_pitch"]]
    if onset_times is not None:
        offset_s = refine_offset(offset_s, np.array([t for t, _ in raw_drums]), onset_times, m4["offset_window_s"])
    out: dict[str, tuple[list[PitchedNote], list[DrumHit]]] = {}
    for part, spec in m4["parts"].items():
        if spec["instrument"] == "drums":
            hits = stack_drum_hits(raw_drums, m4["drum_stack_s"])
            out[part] = ([], [DrumHit(t + offset_s, int(c) if c.isdigit() else m4["drum_pitch"][c]) for t, c in hits])
            continue
        shift = m4["melody_shift"] if part == "vocals" else 0
        source = [n for n in midi.notes if n.role in spec["roles"]]
        if part == "bass":
            source = source + extra_bass_notes(midi.notes, source, m4["bass_extra"])
        notes = [
            PitchedNote(n.start_s + offset_s, n.end_s + offset_s, n.pitch + shift, program_of[part])
            for n in source
            if 0 <= n.pitch + shift <= 127
        ]
        out[part] = (sorted(notes, key=lambda n: (n.onset_s, n.pitch)), [])
    return out, offset_s


def part_onsets(notes: list[PitchedNote], drums: list[DrumHit]) -> list[tuple[float, int]]:
    """採点用の (時刻, 音高)。ドラムはクラスの代表音高。"""
    return [(n.onset_s, n.pitch) for n in notes] + [(d.time_s, d.pitch) for d in drums]


def model_tokenizer():
    """MuScriptor のモデルが使っているのと同じ設定のトークナイザ（transcription_model.py と合わせる）。"""
    from muscriptor.tokenizer.mt3 import MT3Tokenizer

    return MT3Tokenizer(instrument_vocabulary="MT3_FULL_PLUS", max_shift_steps=1001)


def part_programs(tok, m4: dict[str, Any]) -> dict[str, int]:
    """パートごとの、モデルが出す楽器番号（その楽器グループの代表）。"""
    from muscriptor.tokenizer.mt3 import MT3_FULL_PLUS_GROUP_NAMES

    return {p: tok.group_program_map[MT3_FULL_PLUS_GROUP_NAMES[s["instrument"]]][0]
            for p, s in m4["parts"].items() if s["instrument"] != "drums"}


def notes_array(notes: list[PitchedNote], drums: list[DrumHit]) -> np.ndarray:
    """保存用の表（行: 始まり, 終わり, 音高, 楽器番号。ドラムは楽器番号 -1）。"""
    rows = [[n.onset_s, n.offset_s, n.pitch, n.program] for n in notes] + [[d.time_s, d.time_s, d.pitch, -1] for d in drums]
    return np.asarray(rows, dtype=np.float64).reshape(-1, 4)


def notes_in_window(table: np.ndarray, start_s: float, end_s: float) -> tuple[list[PitchedNote], list[DrumHit]]:
    """表から、[start_s, end_s) にかかるノートだけ取り出す。"""
    sel = table[(table[:, 0] < end_s) & (table[:, 1] >= start_s)]
    notes = [PitchedNote(float(r[0]), float(r[1]), int(r[2]), int(r[3])) for r in sel if r[3] >= 0]
    drums = [DrumHit(float(r[0]), int(r[2])) for r in sel if r[3] < 0]
    return notes, drums


def speed_ratio(semitones: int) -> float:
    """再生の速さを変える倍率（速くすると音は semitones 半音上がり、テンポも同じ倍率で上がる）。"""
    return float(2.0 ** (semitones / 12.0))


def speed_audio(wav: np.ndarray, start: int, length: int, ratio: float) -> np.ndarray:
    """wav の start からを ratio 倍の速さで再生した length サンプル（線形補間。足りないところは 0）。"""
    n_src = int(np.ceil(length * ratio)) + 2
    src = np.asarray(wav[start : start + n_src], dtype=np.float32)
    if len(src) < n_src:
        src = np.pad(src, (0, n_src - len(src)))
    return np.interp(np.arange(length) * ratio, np.arange(n_src), src).astype(np.float32)


def speed_table(table: np.ndarray, seek_s: float, ratio: float, semitones: int) -> np.ndarray:
    """notes_array の表を、seek_s から ratio 倍の速さで再生したときの時刻・音高にする。
    時刻は seek_s + (t − seek_s) / ratio。音程のあるノートは semitones 半音動かす（0〜127 を出るものは捨てる）。ドラムの音高はそのまま。"""
    t = table.copy()
    t[:, 0] = seek_s + (t[:, 0] - seek_s) / ratio
    t[:, 1] = seek_s + (t[:, 1] - seek_s) / ratio
    pitched = t[:, 3] >= 0
    t[pitched, 2] = t[pitched, 2] + semitones
    return t[(t[:, 2] >= 0) & (t[:, 2] <= 127)]
