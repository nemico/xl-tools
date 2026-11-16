"""JOYSOUND 納品形式 MIDI の読み込み。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import mido

from a2m.midi_time import (
    TempoEvent,
    TimeSignatureEvent,
    format_time_signature,
    m2_start_tick,
    merge_tempo_events,
    merge_time_signature_events,
    tempo_to_bpm,
    tick_to_second,
)

GM_FAMILIES: tuple[str, ...] = (
    "piano",
    "chromatic_percussion",
    "organ",
    "guitar",
    "bass",
    "strings",
    "ensemble",
    "brass",
    "reed",
    "pipe",
    "synth_lead",
    "synth_pad",
    "synth_fx",
    "ethnic",
    "percussive",
    "sound_effects",
)

KEYOFF_TRACK_PREFIX_RE = re.compile(r"^A\d{1,2}R(\s|$)")
CHORUS_NAME_RE = re.compile(r"Cho|Chorus|コーラス|ハモ", re.IGNORECASE)
MELODY2_NAME_RE = re.compile(r"2nd Vocal|Vocal", re.IGNORECASE)
SUB_DRUM_NAME_PREFIX = "A09R Sub Drum"

# 制作要項 + X-LEBEN ガイドメロディ専用音色（SC-88Pro 5 + X-LEBEN 5）
MELODY2_VOICE_BANKS: frozenset[tuple[int, int, int]] = frozenset(
    {
        (0, 1, 21),  # Harmonica PC#22
        (1, 3, 72),  # Flute PC#73
        (0, 3, 67),  # Oboe PC#68
        (0, 1, 63),  # Soprano Sax PC#64
        (0, 1, 10),  # Vibraphone PC#11
        (80, 1, 21),
        (81, 3, 72),
        (80, 3, 67),
        (80, 1, 63),
        (80, 1, 10),
    }
)
# バンク未設定の旧データ向け（PC のみ）
MELODY2_VOICE_PROGRAMS: frozenset[int] = frozenset({10, 21, 63, 67, 72, 73})
MELODY2_CHANNEL = 5

SE_PROGRAM_MIN = 120  # SC-88 PC 121〜128（1始まり）= 0始まり 120〜127
BASS_PROGRAM_MIN = 32  # GM bass PC 33〜40（1始まり）= 0始まり 32〜39
BASS_PROGRAM_MAX = 39
DEFAULT_RHYTHM_CHANNEL = 10

DRUM_CLASS_BY_NOTE: dict[int, str] = {}
for _n in (35, 36):
    DRUM_CLASS_BY_NOTE[_n] = "kick"
for _n in (38, 40):
    DRUM_CLASS_BY_NOTE[_n] = "snare"
DRUM_CLASS_BY_NOTE[37] = "side_stick"
DRUM_CLASS_BY_NOTE[39] = "clap"
for _n in (42, 44, 46):
    DRUM_CLASS_BY_NOTE[_n] = "hihat"
for _n in (41, 43, 45, 47, 48, 50):
    DRUM_CLASS_BY_NOTE[_n] = "tom"
for _n in (49, 52, 55, 57):
    DRUM_CLASS_BY_NOTE[_n] = "crash"
for _n in (51, 53, 59):
    DRUM_CLASS_BY_NOTE[_n] = "ride"


@dataclass
class KaraokeNote:
    role: str
    track_name: str
    channel: int
    pitch: int
    start_s: float
    end_s: float
    velocity: int
    program: int
    bank_msb: int
    bank_lsb: int
    gm_family: str
    drum_class: str | None = None


@dataclass
class TrackSummary:
    index: int
    name: str
    role: str
    note_count: int
    sysex_count: int


@dataclass
class KaraokeMidiData:
    path: Path
    ticks_per_beat: int
    tempos: list[TempoEvent]
    time_signatures: list[TimeSignatureEvent]
    master_tempo_bpm: float
    tempo_change_count: int
    time_signature: str
    m2_start_s: float
    duration_s: float
    notes: list[KaraokeNote] = field(default_factory=list)
    tracks: list[TrackSummary] = field(default_factory=list)
    sysex_total: int = 0
    sysex_by_track: dict[str, int] = field(default_factory=dict)
    se_track_names: list[str] = field(default_factory=list)
    chorus_track_names: list[str] = field(default_factory=list)
    unknown_track_names: list[str] = field(default_factory=list)
    rhythm_channels: list[int] = field(default_factory=list)
    gs_rhythm_sysex_read: bool = False
    melody2_name_mismatch_track_names: list[str] = field(default_factory=list)


def gm_family(program: int) -> str:
    return GM_FAMILIES[program // 8]


def drum_class(note_number: int) -> str:
    return DRUM_CLASS_BY_NOTE.get(note_number, "other")


def has_keyoff_track_prefix(track_name: str) -> bool:
    """番号直後の R（A10R / A09R 等）。楽器名末尾の _R は対象外。"""
    return bool(KEYOFF_TRACK_PREFIX_RE.match(track_name.strip()))


def _track_has_notes(track: mido.MidiTrack) -> bool:
    for msg in track:
        if msg.type == "note_on" and msg.velocity > 0:
            return True
    return False


def _track_sysex_count(track: mido.MidiTrack) -> int:
    return sum(1 for msg in track if msg.type == "sysex")


def _track_note_channels(track: mido.MidiTrack) -> set[int]:
    chs: set[int] = set()
    for msg in track:
        if msg.type == "note_on" and msg.velocity > 0:
            chs.add(msg.channel + 1)
    return chs


def _collect_sysex_payloads(mid: mido.MidiFile) -> list[bytes]:
    payloads: list[bytes] = []
    for track in mid.tracks:
        for msg in track:
            if msg.type == "sysex":
                payloads.append(bytes(msg.data))
    return payloads


def parse_gs_rhythm_part_channels(sysex_payloads: list[bytes]) -> tuple[set[int], bool]:
    """GS Rhythm Part（アドレス 40 1n 15、値 1 か 2）でリズムに設定された ch（1始まり）。"""
    extra: set[int] = set()
    found = False
    for payload in sysex_payloads:
        db = payload
        for i in range(len(db) - 3):
            if db[i] != 0x40 or db[i + 2] != 0x15:
                continue
            n_byte = db[i + 1]
            if not (0x10 <= n_byte <= 0x1F):
                continue
            val = db[i + 3]
            if val not in (1, 2):
                continue
            found = True
            extra.add(n_byte - 0x10)
    return extra, found


def rhythm_channels_for_midi(
    sysex_payloads: list[bytes],
) -> tuple[set[int], bool]:
    extra, gs_read = parse_gs_rhythm_part_channels(sysex_payloads)
    channels = {DEFAULT_RHYTHM_CHANNEL} | extra
    return channels, gs_read


def _is_sub_drum_fallback_track(
    track_name: str,
    note_channels: set[int],
    gs_rhythm_sysex_read: bool,
) -> bool:
    if gs_rhythm_sysex_read:
        return False
    if 9 not in note_channels:
        return False
    return track_name.strip().startswith(SUB_DRUM_NAME_PREFIX)


def _note_on_rhythm_channel(
    channel: int,
    track_name: str,
    note_channels: set[int],
    rhythm_channels: set[int],
    gs_rhythm_sysex_read: bool,
) -> bool:
    if channel in rhythm_channels:
        return True
    return channel == 9 and _is_sub_drum_fallback_track(
        track_name, note_channels, gs_rhythm_sysex_read
    )


def _track_uses_bass_program(track: mido.MidiTrack) -> bool:
    program = 0
    for msg in track:
        if msg.type == "program_change":
            program = msg.program
        elif msg.type == "note_on" and msg.velocity > 0:
            if BASS_PROGRAM_MIN <= program <= BASS_PROGRAM_MAX:
                return True
    return False


def _chorus_name_match(name: str) -> bool:
    return bool(CHORUS_NAME_RE.search(name))


def melody2_name_match(track_name: str) -> bool:
    return bool(MELODY2_NAME_RE.search(track_name))


def _note_intervals_on_channel(
    track: mido.MidiTrack, channel_1based: int
) -> list[tuple[int, int]]:
    ch = channel_1based - 1
    tick = 0
    active: dict[int, int] = {}
    intervals: list[tuple[int, int]] = []
    for msg in track:
        tick += msg.time
        if msg.type == "note_on" and msg.velocity > 0 and msg.channel == ch:
            active[msg.note] = tick
        elif msg.type == "note_off" or (
            msg.type == "note_on" and msg.velocity == 0 and msg.channel == ch
        ):
            if msg.note in active:
                intervals.append((active.pop(msg.note), tick))
    return intervals


def track_has_simultaneous_notes(intervals: list[tuple[int, int]]) -> bool:
    events: list[tuple[int, int]] = []
    for start, end in intervals:
        if end <= start:
            continue
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda x: (x[0], -x[1]))
    active = 0
    for _t, delta in events:
        active += delta
        if active >= 2:
            return True
    return False


def ch5_has_chords(track: mido.MidiTrack) -> bool:
    intervals = _note_intervals_on_channel(track, MELODY2_CHANNEL)
    return track_has_simultaneous_notes(intervals)


def ch5_velocity_mostly_100(track: mido.MidiTrack) -> bool:
    ch = MELODY2_CHANNEL - 1
    vels: list[int] = []
    for msg in track:
        if msg.type == "note_on" and msg.velocity > 0 and msg.channel == ch:
            vels.append(msg.velocity)
    if not vels:
        return False
    if not all(99 <= v <= 100 for v in vels):
        return False
    return max(vels) - min(vels) <= 1


def ch5_all_voice_programs(track: mido.MidiTrack) -> bool:
    ch = MELODY2_CHANNEL - 1
    program = 0
    msb = 0
    lsb = 0
    any_note = False
    for msg in track:
        if msg.type == "program_change" and msg.channel == ch:
            program = msg.program
        elif msg.type == "control_change" and msg.channel == ch:
            if msg.control == 0:
                msb = msg.value
            elif msg.control == 32:
                lsb = msg.value
        elif msg.type == "note_on" and msg.velocity > 0 and msg.channel == ch:
            any_note = True
            bank = (msb, lsb, program)
            if bank in MELODY2_VOICE_BANKS:
                continue
            if msb == 0 and lsb == 0 and program in MELODY2_VOICE_PROGRAMS:
                continue
            return False
    return any_note


def classify_ch5_track_role(
    track_name: str, track: mido.MidiTrack
) -> tuple[str, bool]:
    """ch5 トラックの役割と、補助判定による名前食い違いフラグ。"""
    if melody2_name_match(track_name):
        return "melody2", False
    if (
        ch5_all_voice_programs(track)
        and not ch5_has_chords(track)
        and ch5_velocity_mostly_100(track)
    ):
        return "melody2", True
    return "other", False


def ch5_track_diagnostics(
    track_name: str, track: mido.MidiTrack
) -> dict[str, object]:
    """10曲報告用：ch5 トラックの判定材料。"""
    program = 0
    ch = MELODY2_CHANNEL - 1
    programs_used: set[int] = set()
    vels: list[int] = []
    for msg in track:
        if msg.type == "program_change":
            program = msg.program
        elif msg.type == "note_on" and msg.velocity > 0 and msg.channel == ch:
            programs_used.add(program)
            vels.append(msg.velocity)
    role, mismatch = classify_ch5_track_role(track_name, track)
    vel_s = "-"
    if vels:
        vel_s = str(min(vels)) if min(vels) == max(vels) else f"{min(vels)}-{max(vels)}"
    return {
        "track_name": track_name,
        "programs": sorted(programs_used),
        "has_chords": ch5_has_chords(track),
        "velocity": vel_s,
        "role": role,
        "name_mismatch": mismatch,
    }


def _classify_track_role(
    name: str,
    note_channels: set[int],
    has_notes: bool,
    sysex_count: int,
    any_chorus_name: bool,
    is_last_note_track: bool,
    rhythm_channels: set[int],
    gs_rhythm_sysex_read: bool,
    global_ch2_has_notes: bool,
    track_has_bass_program: bool,
) -> str:
    if not has_notes and sysex_count > 0:
        return "exclusive"
    if not has_notes:
        return "unknown"

    if any(
        _note_on_rhythm_channel(
            ch, name, note_channels, rhythm_channels, gs_rhythm_sysex_read
        )
        for ch in note_channels
    ):
        return "drums"

    if 4 in note_channels:
        return "melody"

    if _chorus_name_match(name):
        return "chorus"
    if not any_chorus_name and is_last_note_track:
        return "chorus"

    keyoff = has_keyoff_track_prefix(name)
    if keyoff:
        return "se"

    if 2 in note_channels:
        return "bass"
    if not global_ch2_has_notes and track_has_bass_program:
        return "bass"
    return "other"


def _note_role(
    track_role: str,
    track_name: str,
    note_channels: set[int],
    channel: int,
    velocity: int,
    program: int,
    rhythm_channels: set[int],
    gs_rhythm_sysex_read: bool,
) -> str:
    if track_role in ("exclusive", "chorus", "se"):
        return track_role
    if _note_on_rhythm_channel(
        channel, track_name, note_channels, rhythm_channels, gs_rhythm_sysex_read
    ):
        return "drums"
    if channel == 4:
        if velocity == 1:
            return "melody_unpitched"
        return "melody"
    if channel == 5:
        return "melody2" if track_role == "melody2" else "other"
    if track_role == "bass" or channel == 2:
        return "bass"
    if track_role == "melody":
        if velocity == 1:
            return "melody_unpitched"
        return "melody"
    if track_role == "melody2":
        return "melody2"
    if SE_PROGRAM_MIN <= program <= 127:
        return "se"
    if track_role == "unknown":
        return "unknown"
    return track_role


def _correct_pitch(role: str, pitch: int) -> int:
    if role in ("melody", "melody_unpitched", "melody2"):
        return pitch - 12
    return pitch


def _parse_track_notes(
    track: mido.MidiTrack,
    track_name: str,
    track_role: str,
    note_channels: set[int],
    tempos: list[TempoEvent],
    ticks_per_beat: int,
    rhythm_channels: set[int],
    gs_rhythm_sysex_read: bool,
) -> list[KaraokeNote]:
    notes: list[KaraokeNote] = []
    tick = 0
    program = 0
    bank_msb = 0
    bank_lsb = 0
    active: dict[tuple[int, int], tuple[int, int, int]] = {}

    for msg in track:
        tick += msg.time
        if msg.type == "program_change":
            program = msg.program
        elif msg.type == "control_change":
            if msg.control == 0:
                bank_msb = msg.value
            elif msg.control == 32:
                bank_lsb = msg.value
        elif msg.type == "note_on" and msg.velocity > 0:
            ch = msg.channel + 1
            active[(ch, msg.note)] = (tick, msg.velocity, program)
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            ch = msg.channel + 1
            key = (ch, msg.note)
            if key not in active:
                continue
            start_tick, velocity, note_program = active.pop(key)
            role = _note_role(
                track_role,
                track_name,
                note_channels,
                ch,
                velocity,
                note_program,
                rhythm_channels,
                gs_rhythm_sysex_read,
            )
            pitch = _correct_pitch(role, msg.note)
            start_s = tick_to_second(start_tick, tempos, ticks_per_beat)
            end_s = tick_to_second(tick, tempos, ticks_per_beat)
            if role == "drums":
                from a2m.sc_map import drum_class_for_kit_note

                drum_cls = drum_class_for_kit_note(
                    bank_lsb, program, pitch
                )
            else:
                drum_cls = None
            notes.append(
                KaraokeNote(
                    role=role,
                    track_name=track_name,
                    channel=ch,
                    pitch=pitch,
                    start_s=start_s,
                    end_s=end_s,
                    velocity=velocity,
                    program=note_program,
                    bank_msb=bank_msb,
                    bank_lsb=bank_lsb,
                    gm_family=gm_family(note_program),
                    drum_class=drum_cls,
                )
            )
    return notes


def load_karaoke_midi(path: Path | str) -> KaraokeMidiData:
    path = Path(path)
    mid = mido.MidiFile(path)
    tempos = merge_tempo_events(mid.tracks)
    time_sigs = merge_time_signature_events(mid.tracks)
    tpb = mid.ticks_per_beat

    track_names: list[str] = []
    has_notes_list: list[bool] = []
    for track in mid.tracks:
        name = ""
        for msg in track:
            if msg.type == "track_name":
                name = msg.name
                break
        track_names.append(name)
        has_notes_list.append(_track_has_notes(track))

    note_track_indices = [i for i, hn in enumerate(has_notes_list) if hn]
    last_note_track_index = note_track_indices[-1] if note_track_indices else -1
    any_chorus_name = any(
        _chorus_name_match(track_names[i]) for i in note_track_indices
    )

    sysex_payloads = _collect_sysex_payloads(mid)
    rhythm_channels, gs_rhythm_sysex_read = rhythm_channels_for_midi(sysex_payloads)
    global_ch2_has_notes = any(
        2 in _track_note_channels(mid.tracks[i]) for i in note_track_indices
    )

    track_summaries: list[TrackSummary] = []
    all_notes: list[KaraokeNote] = []
    sysex_total = 0
    sysex_by_track: dict[str, int] = {}
    se_names: list[str] = []
    chorus_names: list[str] = []
    unknown_names: list[str] = []
    melody2_mismatch_names: list[str] = []

    for i, track in enumerate(mid.tracks):
        name = track_names[i]
        has_notes = has_notes_list[i]
        sysex_count = _track_sysex_count(track)
        sysex_total += sysex_count
        if sysex_count:
            sysex_by_track[name or f"track_{i}"] = sysex_count

        note_channels = _track_note_channels(track) if has_notes else set()
        if has_notes and MELODY2_CHANNEL in note_channels:
            role, name_mismatch = classify_ch5_track_role(name, track)
            if name_mismatch:
                melody2_mismatch_names.append(name)
        else:
            role = _classify_track_role(
                name,
                note_channels,
                has_notes,
                sysex_count,
                any_chorus_name,
                i == last_note_track_index,
                rhythm_channels,
                gs_rhythm_sysex_read,
                global_ch2_has_notes,
                _track_uses_bass_program(track) if has_notes else False,
            )
        note_count = 0
        if has_notes:
            parsed = _parse_track_notes(
                track,
                name,
                role,
                note_channels,
                tempos,
                tpb,
                rhythm_channels,
                gs_rhythm_sysex_read,
            )
            note_count = len(parsed)
            all_notes.extend(parsed)
        track_summaries.append(
            TrackSummary(
                index=i,
                name=name,
                role=role,
                note_count=note_count,
                sysex_count=sysex_count,
            )
        )
        if role == "se":
            se_names.append(name)
        if role == "chorus":
            chorus_names.append(name)
        if role == "unknown" and has_notes:
            unknown_names.append(name)

    m2_tick = m2_start_tick(time_sigs, tpb)
    m2_start_s = tick_to_second(m2_tick, tempos, tpb)
    duration_s = max((n.end_s for n in all_notes), default=0.0)

    return KaraokeMidiData(
        path=path,
        ticks_per_beat=tpb,
        tempos=tempos,
        time_signatures=time_sigs,
        master_tempo_bpm=tempo_to_bpm(tempos[0].tempo),
        tempo_change_count=max(0, len(tempos) - 1),
        time_signature=format_time_signature(time_sigs[0]),
        m2_start_s=m2_start_s,
        duration_s=duration_s,
        notes=all_notes,
        tracks=track_summaries,
        sysex_total=sysex_total,
        sysex_by_track=sysex_by_track,
        se_track_names=se_names,
        chorus_track_names=chorus_names,
        unknown_track_names=unknown_names,
        rhythm_channels=sorted(rhythm_channels),
        gs_rhythm_sysex_read=gs_rhythm_sysex_read,
        melody2_name_mismatch_track_names=melody2_mismatch_names,
    )


def role_note_counts(data: KaraokeMidiData) -> dict[str, int]:
    counts: dict[str, int] = {}
    for note in data.notes:
        counts[note.role] = counts.get(note.role, 0) + 1
    return counts


def used_gm_families(data: KaraokeMidiData) -> list[str]:
    fam = {n.gm_family for n in data.notes}
    return sorted(fam)


def track_program_first_appearances(
    data: KaraokeMidiData,
) -> list[tuple[int, str, list[tuple[int, float]]]]:
    """トラックごと (index, name, [(program, first_start_s), ...])。"""
    by_track: dict[str, list[KaraokeNote]] = {}
    track_index: dict[str, int] = {}
    for tr in data.tracks:
        if tr.note_count > 0:
            track_index[tr.name] = tr.index
    for n in data.notes:
        by_track.setdefault(n.track_name, []).append(n)

    rows: list[tuple[int, str, list[tuple[int, float]]]] = []
    for name, notes in sorted(by_track.items(), key=lambda x: track_index.get(x[0], 999)):
        first: dict[int, float] = {}
        for n in sorted(notes, key=lambda x: x.start_s):
            if n.program not in first:
                first[n.program] = n.start_s
        pcs = sorted(first.items(), key=lambda x: x[1])
        rows.append((track_index.get(name, -1), name, pcs))
    return rows


def count_tracks_with_program_change(data: KaraokeMidiData) -> int:
    n = 0
    for _idx, _name, pcs in track_program_first_appearances(data):
        if len(pcs) > 1:
            n += 1
    return n


def format_track_programs(pcs: list[tuple[int, float]]) -> str:
    return ";".join(f"{pc}@{start:.4f}" for pc, start in pcs)
