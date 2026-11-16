"""曲頭4小節のノート書き出し（仕様 第2.5節・P1-2d）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mido

from a2m.karaoke_midi import KaraokeMidiData, KaraokeNote, drum_class, load_karaoke_midi
from a2m.midi_time import (
    m2_start_tick,
    measure_length_ticks,
    second_to_tick,
    tempo_to_bpm,
    time_signature_at_tick,
)


@dataclass(frozen=True)
class IntroNoteRow:
    measure_num: int
    beat_tick: str
    track_name: str
    role: str
    channel: int
    note_name: str
    length_beats: float
    velocity: int
    start_tick: int
    track_index: int


def measure_start_tick(measure_num: int, signatures: list, ticks_per_beat: int) -> int:
    """1始まりの小節番号 M1 の tick = 0。"""
    pos = 0
    for m in range(1, measure_num):
        ts = time_signature_at_tick(pos, signatures)
        pos += measure_length_ticks(ts, ticks_per_beat)
    return pos


def tick_to_measure_number(tick: int, signatures: list, ticks_per_beat: int) -> int:
    pos = 0
    m = 1
    while True:
        ts = time_signature_at_tick(pos, signatures)
        span = measure_length_ticks(ts, ticks_per_beat)
        if tick < pos + span:
            return m
        pos += span
        m += 1


def format_beat_tick(relative_tick_in_measure: int, ticks_per_beat: int) -> str:
    beat = relative_tick_in_measure // ticks_per_beat + 1
    intra = relative_tick_in_measure % ticks_per_beat
    return f"{beat}.{intra:03d}"


_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def midi_pitch_name(pitch: int) -> str:
    return f"{_NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


def format_note_name(note: KaraokeNote) -> str:
    if note.role == "drums":
        cls = note.drum_class or drum_class(note.pitch)
        return f"{note.pitch} {cls}"
    return midi_pitch_name(int(note.pitch))


def _track_name_to_index(mid: mido.MidiFile) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, track in enumerate(mid.tracks):
        for msg in track:
            if msg.type == "track_name":
                out[msg.name] = i
                break
    return out


def first_note_measure_from_m2(data: KaraokeMidiData) -> int:
    tpb = data.ticks_per_beat
    sigs = data.time_signatures
    m2 = m2_start_tick(sigs, tpb)
    ticks = [
        second_to_tick(n.start_s, data.tempos, tpb)
        for n in data.notes
        if second_to_tick(n.start_s, data.tempos, tpb) >= m2
    ]
    if not ticks:
        return 2
    return tick_to_measure_number(min(ticks), sigs, tpb)


def _note_rows_in_measures(
    data: KaraokeMidiData,
    mid: mido.MidiFile,
    first_measure: int,
    num_measures: int = 4,
) -> list[IntroNoteRow]:
    tpb = data.ticks_per_beat
    sigs = data.time_signatures
    last_m = first_measure + num_measures - 1
    win_lo = measure_start_tick(first_measure, sigs, tpb)
    win_hi = measure_start_tick(last_m + 1, sigs, tpb)
    name_idx = _track_name_to_index(mid)

    rows: list[IntroNoteRow] = []
    for note in data.notes:
        start_tick = second_to_tick(note.start_s, data.tempos, tpb)
        end_tick = second_to_tick(note.end_s, data.tempos, tpb)
        if start_tick < win_lo or start_tick >= win_hi:
            continue
        mnum = tick_to_measure_number(start_tick, sigs, tpb)
        mstart = measure_start_tick(mnum, sigs, tpb)
        rel = start_tick - mstart
        length_beats = max(0.0, (end_tick - start_tick) / tpb)
        rows.append(
            IntroNoteRow(
                measure_num=mnum,
                beat_tick=format_beat_tick(rel, tpb),
                track_name=note.track_name,
                role=note.role,
                channel=note.channel,
                note_name=format_note_name(note),
                length_beats=length_beats,
                velocity=note.velocity,
                start_tick=start_tick,
                track_index=name_idx.get(note.track_name, 10_000),
            )
        )
    rows.sort(key=lambda r: (r.measure_num, r.start_tick, r.track_index, r.note_name))
    return rows


def measure_role_counts(rows: list[IntroNoteRow]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for r in rows:
        out.setdefault(r.measure_num, {})
        out[r.measure_num][r.role] = out[r.measure_num].get(r.role, 0) + 1
    return out


def format_measure_counts(counts: dict[int, dict[str, int]]) -> str:
    parts: list[str] = []
    for m in sorted(counts):
        roles = counts[m]
        role_str = " / ".join(f"{role} {roles[role]}" for role in sorted(roles))
        parts.append(f"M{m} {role_str}")
    return " ｜ ".join(parts)


def format_song_intro_markdown(data: KaraokeMidiData, song_label: str) -> str:
    mid = mido.MidiFile(data.path)
    first_m = first_note_measure_from_m2(data)
    rows = _note_rows_in_measures(data, mid, first_m, 4)
    counts = measure_role_counts(rows)
    bpm = int(round(data.master_tempo_bpm))
    lines = [
        f"## {song_label}　（拍子 {data.time_signature}、テンポ {bpm}、"
        f"最初のノートの小節 M{first_m}）",
        "",
        "| 小節 | 拍.tick | トラック名 | 役割 | ch | 音名 | 長さ(拍) | vel |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| M{r.measure_num} | {r.beat_tick} | {r.track_name} | {r.role} | "
            f"{r.channel} | {r.note_name} | {r.length_beats:.2f} | {r.velocity} |"
        )
    lines.append("")
    lines.append(f"小節ごとの数：{format_measure_counts(counts)}")
    lines.append("")
    return "\n".join(lines)


def build_intro_dump_md(song_dirs: list[Path]) -> str:
    blocks: list[str] = ["# intro dump（M2以降・最初のノート小節から4小節）", ""]
    for song_dir in song_dirs:
        midi_path = song_dir / "final.mid"
        if not midi_path.is_file():
            continue
        data = load_karaoke_midi(midi_path)
        blocks.append(format_song_intro_markdown(data, song_dir.name))
    return "\n".join(blocks)


def song_summary_row(data: KaraokeMidiData, song_label: str) -> tuple[str, int, str]:
    first_m = first_note_measure_from_m2(data)
    mid = mido.MidiFile(data.path)
    rows = _note_rows_in_measures(data, mid, first_m, 4)
    counts_line = format_measure_counts(measure_role_counts(rows))
    return song_label, first_m, counts_line
