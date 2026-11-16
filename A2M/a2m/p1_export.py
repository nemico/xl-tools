"""段階1: 役割別 MIDI・聴取 WAV・Reaper プロジェクト（仕様 第6節）。"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mido
import numpy as np
import soundfile as sf

from a2m.evaluate import EvalNote
from a2m.methods.schema import MethodNote
from a2m.midi_time import (
    TempoEvent,
    merge_tempo_events,
    second_to_rpp_tick,
    second_to_tick,
    tick_to_second,
)
from a2m.rpp_midi import (
    RppMidiLayout,
    build_rpp_midi_layout,
    role_rpp_events_from_ticks,
)
from a2m.rpp_tempo_map import TempoMapBuild, tempo_envelope_block
from a2m.units import midi_to_hz

EXPORT_ROLES = ("melody", "melody2", "bass", "drums", "other")
TICKS_PER_BEAT = 480
KICK_NOTES = frozenset({35, 36})
FILE_LINE_RE = re.compile(r'^\s*FILE\s+"((?:[^"\\]|\\.)*)"\s*$', re.MULTILINE)
E_LINE_RE = re.compile(r"^\s*E\s+(\d+)\s+([0-9a-fA-F]{2})\s+([0-9a-fA-F]{2})\s+([0-9a-fA-F]{2})\s*$")


@dataclass(frozen=True)
class RenderConfig:
    pitched_dbfs: float
    drum_dbfs: float
    peak_dbfs: float
    rpp_ppq: int
    seed: int

    @classmethod
    def from_dict(cls, p1: dict[str, Any]) -> RenderConfig:
        r = p1["render"]
        return cls(
            pitched_dbfs=float(r["pitched_dbfs"]),
            drum_dbfs=float(r["drum_dbfs"]),
            peak_dbfs=float(r["peak_dbfs"]),
            rpp_ppq=int(r["rpp_ppq"]),
            seed=int(r.get("seed", 0)),
        )


def _constant_tempo_map(bpm: float) -> list[TempoEvent]:
    return [TempoEvent(tick=0, tempo=mido.bpm2tempo(bpm))]


def _dbfs_to_linear(dbfs: float) -> float:
    return float(10.0 ** (dbfs / 20.0))


def eval_note_from_method(n: MethodNote) -> EvalNote:
    return EvalNote(
        role=n.role,
        pitch=n.pitch,
        start_s=n.start_s,
        end_s=n.end_s,
        drum_class=n.drum_class,
    )


def method_notes_from_json(raw: list[dict]) -> list[EvalNote]:
    out: list[EvalNote] = []
    for item in raw:
        out.append(
            EvalNote(
                role=str(item["role"]),
                pitch=int(item["pitch"]),
                start_s=float(item["start_s"]),
                end_s=float(item["end_s"]),
                drum_class=item.get("drum_class"),
            )
        )
    return out


def audio_duration_seconds(audio_path: Path | str) -> float:
    return float(sf.info(str(audio_path)).duration)


def audio_source_kind(path: Path | str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".mp3":
        return "MP3"
    if ext == ".wav":
        return "WAVE"
    if ext == ".flac":
        return "FLAC"
    raise ValueError(f"未対応の音源拡張子: {ext}")


def _rpp_path(path: Path) -> str:
    return str(path.resolve())


def load_role_midi(path: Path | str) -> list[EvalNote]:
    path = Path(path)
    mid = mido.MidiFile(path)
    tempos = merge_tempo_events(mid.tracks)
    tpb = mid.ticks_per_beat

    notes: list[EvalNote] = []
    for track in mid.tracks:
        role = ""
        for msg in track:
            if msg.type == "track_name":
                role = msg.name.strip()
                break
        if not role:
            continue
        tick = 0
        active: dict[tuple[int, int], list[float]] = defaultdict(list)
        for msg in track:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                ch = msg.channel + 1
                active[(ch, msg.note)].append(tick_to_second(tick, tempos, tpb))
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                ch = msg.channel + 1
                key = (ch, msg.note)
                if not active[key]:
                    continue
                start_s = active[key].pop()
                pitch = msg.note
                drum_cls = None
                if role == "drums" or ch == 10:
                    from a2m.sc_map import drum_class_for_kit_note

                    drum_cls = drum_class_for_kit_note(3, 0, pitch)
                notes.append(
                    EvalNote(
                        role=role,
                        pitch=pitch,
                        start_s=start_s,
                        end_s=tick_to_second(tick, tempos, tpb),
                        drum_class=drum_cls,
                    )
                )
    return notes


def count_role_notes_in_midi(path: Path | str, role: str) -> int:
    mid = mido.MidiFile(str(path))
    for track in mid.tracks:
        track_role = ""
        for msg in track:
            if msg.type == "track_name":
                track_role = msg.name.strip()
                break
        if track_role != role:
            continue
        return sum(1 for msg in track if msg.type == "note_on" and msg.velocity > 0)
    return 0


def _append_note_events(
    events: list[tuple[int, mido.Message]],
    role_notes: list[EvalNote],
    tempo_map: list[TempoEvent],
    ticks_per_beat: int,
    ch0: int,
) -> None:
    for n in sorted(role_notes, key=lambda x: (x.start_s, x.pitch)):
        pitch = max(0, min(127, n.pitch))
        on_tick = second_to_tick(n.start_s, tempo_map, ticks_per_beat)
        off_tick = max(on_tick + 1, second_to_tick(n.end_s, tempo_map, ticks_per_beat))
        events.append(
            (
                on_tick,
                mido.Message("note_on", channel=ch0, note=pitch, velocity=80, time=0),
            )
        )
        events.append(
            (
                off_tick,
                mido.Message("note_off", channel=ch0, note=pitch, velocity=0, time=0),
            )
        )


def write_role_midi(
    notes: list[EvalNote],
    path: Path | str,
    tempo_bpm: float,
    *,
    ticks_per_beat: int = TICKS_PER_BEAT,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    by_role: dict[str, list[EvalNote]] = defaultdict(list)
    for n in notes:
        if n.role in EXPORT_ROLES:
            by_role[n.role].append(n)

    mid = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat)
    tempo_map = _constant_tempo_map(tempo_bpm)
    tempo_track = mido.MidiTrack()
    tempo_track.append(mido.MetaMessage("set_tempo", tempo=tempo_map[0].tempo, time=0))
    tempo_track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    mid.tracks.append(tempo_track)

    for role in EXPORT_ROLES:
        role_notes = by_role.get(role, [])
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=role, time=0))
        events: list[tuple[int, mido.Message]] = []
        _append_note_events(events, role_notes, tempo_map, ticks_per_beat, 0)
        events.sort(key=lambda x: (x[0], 0 if x[1].type == "note_off" else 1))
        prev = 0
        for abs_tick, msg in events:
            track.append(msg.copy(time=max(0, abs_tick - prev)))
            prev = abs_tick
        mid.tracks.append(track)
    mid.save(path)


def write_format0_role_midi(
    role_notes: list[EvalNote],
    path: Path | str,
    tempo_bpm: float,
    role: str,
    *,
    ticks_per_beat: int = TICKS_PER_BEAT,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tempo_map = _constant_tempo_map(tempo_bpm)
    mid = mido.MidiFile(type=0, ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("set_tempo", tempo=tempo_map[0].tempo, time=0))
    track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    events: list[tuple[int, mido.Message]] = []
    _append_note_events(events, role_notes, tempo_map, ticks_per_beat, 0)
    events.sort(key=lambda x: (x[0], 0 if x[1].type == "note_off" else 1))
    prev = 0
    for abs_tick, msg in events:
        track.append(msg.copy(time=max(0, abs_tick - prev)))
        prev = abs_tick
    mid.tracks.append(track)
    mid.save(path)


def write_rpp_gt_midis(
    notes: list[EvalNote],
    song_out: Path | str,
    tempo_bpm: float,
    *,
    ticks_per_beat: int = TICKS_PER_BEAT,
) -> dict[str, Path]:
    song_out = Path(song_out)
    rpp_dir = song_out / "rpp_midi"
    rpp_dir.mkdir(parents=True, exist_ok=True)
    by_role: dict[str, list[EvalNote]] = defaultdict(list)
    for n in notes:
        if n.role in EXPORT_ROLES:
            by_role[n.role].append(n)
    written: dict[str, Path] = {}
    for role in EXPORT_ROLES:
        role_notes = by_role.get(role, [])
        if not role_notes:
            continue
        out_path = rpp_dir / f"gt_{role}.mid"
        write_format0_role_midi(
            role_notes, out_path, tempo_bpm, role, ticks_per_beat=ticks_per_beat
        )
        written[role] = out_path.resolve()
    return written


def write_rpp_method_midis(
    notes: list[EvalNote],
    song_out: Path | str,
    method: str,
    tempo_bpm: float,
    *,
    ticks_per_beat: int = TICKS_PER_BEAT,
) -> dict[str, Path]:
    song_out = Path(song_out)
    rpp_dir = song_out / "rpp_midi"
    by_role: dict[str, list[EvalNote]] = defaultdict(list)
    for n in notes:
        if n.role in EXPORT_ROLES:
            by_role[n.role].append(n)
    written: dict[str, Path] = {}
    for role in EXPORT_ROLES:
        role_notes = by_role.get(role, [])
        if not role_notes:
            continue
        out_path = rpp_dir / f"{method}_{role}.mid"
        write_format0_role_midi(role_notes, out_path, tempo_bpm, role, ticks_per_beat=ticks_per_beat)
        written[role] = out_path.resolve()
    return written


def _pitched_envelope_segment(
    seg_len: int,
    sr: int,
    start_s: float,
    end_s: float,
    *,
    buf_i0: int,
) -> np.ndarray:
    """出力 y[buf_i0 : buf_i0 + seg_len] 用のエンベロープ（seg_len 分だけ）。"""
    env = np.zeros(seg_len, dtype=np.float64)
    rise = int(round(0.005 * sr))
    fall = int(round(0.030 * sr))
    note_i0 = int(round(start_s * sr))
    note_i1 = int(round(end_s * sr))
    rel0 = max(0, note_i0 - buf_i0)
    rel1 = min(seg_len, note_i1 - buf_i0)
    for j in range(rel0, rel1):
        t = j - rel0
        env[j] = 1.0 if t >= rise else (t / max(1, rise))
    for j in range(rel1, seg_len):
        t = j - rel1
        env[j] = max(0.0, 1.0 - t / max(1, fall))
    return env


def synthesize_gt_render(
    notes: list[EvalNote],
    duration_s: float,
    sr: int,
    render_cfg: RenderConfig,
) -> np.ndarray:
    n = int(round(duration_s * sr))
    y = np.zeros(n, dtype=np.float64)
    pitched_amp = _dbfs_to_linear(render_cfg.pitched_dbfs)
    drum_amp = _dbfs_to_linear(render_cfg.drum_dbfs)
    rng = np.random.default_rng(render_cfg.seed)
    fall_samp = int(round(0.030 * sr))

    for note in notes:
        if note.role in ("se", "chorus", "melody_unpitched", "exclusive", "unknown"):
            continue
        if note.role == "drums":
            i0 = int(round(note.start_s * sr))
            if i0 < 0 or i0 >= n:
                continue
            if note.pitch in KICK_NOTES:
                length = int(round(0.080 * sr))
                i1 = min(n, i0 + length)
                if i1 <= i0:
                    continue
                seg_len = i1 - i0
                seg_t = np.arange(seg_len, dtype=np.float64) / sr
                wave = np.sin(2 * np.pi * 60.0 * seg_t) * np.exp(-seg_t * 40.0)
                y[i0:i1] += drum_amp * wave
            else:
                length = int(round(0.040 * sr))
                i1 = min(n, i0 + length)
                if i1 <= i0:
                    continue
                seg_len = i1 - i0
                seg_t = np.arange(seg_len, dtype=np.float64) / sr
                wave = rng.standard_normal(seg_len) * np.exp(-seg_t * 50.0)
                y[i0:i1] += drum_amp * wave
        elif note.role in ("melody", "melody2", "bass", "other"):
            i0 = int(round(note.start_s * sr))
            i1 = min(n, int(round(note.end_s * sr)) + fall_samp)
            if i0 >= n or i1 <= i0:
                continue
            seg_len = i1 - i0
            seg_t = (np.arange(seg_len, dtype=np.float64) + i0) / sr
            freq = midi_to_hz(float(note.pitch))
            env = _pitched_envelope_segment(
                seg_len, sr, note.start_s, note.end_s, buf_i0=i0
            )
            y[i0:i1] += pitched_amp * env * np.sin(2 * np.pi * freq * seg_t)

    peak = float(np.max(np.abs(y))) if y.size else 0.0
    limit = _dbfs_to_linear(render_cfg.peak_dbfs)
    if peak > limit:
        y *= limit / peak
    return y


def write_gt_render_wav(
    path: Path | str,
    notes: list[EvalNote],
    duration_s: float,
    sr: int,
    render_cfg: RenderConfig,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    y = synthesize_gt_render(notes, duration_s, sr, render_cfg)
    sf.write(path, y, sr, subtype="PCM_16")


def write_check_lr_wav(
    path: Path | str,
    mp3_mono: np.ndarray,
    gt_render: np.ndarray,
    sr: int,
) -> None:
    path = Path(path)
    n = min(mp3_mono.size, gt_render.size)
    stereo = np.zeros((n, 2), dtype=np.float32)
    stereo[:, 0] = mp3_mono[:n].astype(np.float32)
    stereo[:, 1] = gt_render[:n].astype(np.float32)
    sf.write(path, stereo, sr, subtype="PCM_16")


def _role_rpp_events_legacy_seconds(
    role_notes: list[EvalNote],
    tempo_bpm: float,
    length_s: float,
    ppq: int,
) -> list[tuple[int, tuple[int, int, int]]]:
    """テンポ一定のみ（第6.1c 以前）。秒 → tick。"""
    note_ticks: list[tuple[int, int, int]] = []
    for n in role_notes:
        on_t = second_to_rpp_tick(n.start_s, tempo_bpm, ppq=ppq)
        off_t = max(on_t + 1, second_to_rpp_tick(n.end_s, tempo_bpm, ppq=ppq))
        note_ticks.append((on_t, off_t, n.pitch))
    end_tick = second_to_rpp_tick(length_s, tempo_bpm, ppq=ppq)
    return role_rpp_events_from_ticks(note_ticks, end_tick)


def _events_to_e_lines(events: list[tuple[int, tuple[int, int, int]]]) -> list[str]:
    lines: list[str] = []
    prev = 0
    for abs_tick, (b1, b2, b3) in events:
        delta = max(0, abs_tick - prev)
        lines.append(f"        E {delta} {b1:02x} {b2:02x} {b3:02x}")
        prev = abs_tick
    return lines


def count_embedded_note_ons_in_track(rpp_text: str, track_name: str) -> int:
    lines = rpp_text.splitlines()
    in_track = False
    in_source = False
    count = 0
    for line in lines:
        if line.strip().startswith("NAME ") and f'"{track_name}"' in line:
            in_track = True
            in_source = False
            continue
        if in_track and line.strip() == ">":
            if in_source:
                break
            in_track = False
        if in_track and "<SOURCE MIDI" in line:
            in_source = True
            continue
        if in_source and line.strip().startswith("E "):
            m = E_LINE_RE.match(line)
            if m and m.group(2).lower() == "90":
                count += 1
    return count


def parse_rpp_file_paths(rpp_text: str) -> list[Path]:
    return [Path(m.group(1)) for m in FILE_LINE_RE.finditer(rpp_text)]


def verify_rpp_file_paths(rpp_path: Path | str) -> list[Path]:
    path = Path(rpp_path)
    text = path.read_text(encoding="utf-8")
    if text.startswith("\ufeff"):
        raise ValueError(f"RPP に BOM があります: {path}")
    files = parse_rpp_file_paths(text)
    missing = [p for p in files if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            "RPP から参照したファイルが存在しません: "
            + ", ".join(str(p) for p in missing)
        )
    return files


def verify_rpp_embedded_note_counts(
    rpp_path: Path | str,
    gt_aligned_path: Path | str | None = None,
    *,
    role_notes: dict[str, list[EvalNote]] | None = None,
) -> None:
    text = Path(rpp_path).read_text(encoding="utf-8")
    for role in EXPORT_ROLES:
        if role_notes is not None:
            expected = len(role_notes.get(role, []))
        elif gt_aligned_path is not None:
            expected = count_role_notes_in_midi(Path(gt_aligned_path), role)
        else:
            continue
        if expected == 0:
            continue
        embedded = count_embedded_note_ons_in_track(text, f"gt_{role}")
        if embedded != expected:
            label = "role_notes" if role_notes is not None else "gt_aligned"
            raise ValueError(
                f"RPP gt_{role} ノート数 {embedded} != {label} {expected}"
            )


def write_check_rpp(
    path: Path | str,
    *,
    tempo_bpm: float,
    audio_path: Path,
    gt_render_path: Path,
    length_s: float,
    role_notes: dict[str, list[EvalNote]],
    render_cfg: RenderConfig,
    countin_notes: list[EvalNote] | None = None,
    render_aligned_path: Path | None = None,
    render_offset_r: float | None = None,
    tempo_map: TempoMapBuild | None = None,
    delivery_midi=None,
    countin_keys: frozenset[tuple] | None = None,
    midi_item_position_s: float | None = None,
    method_role_notes: dict[str, dict[str, list[EvalNote]]] | None = None,
    exclude_track_names: frozenset[str] | None = None,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    audio_path = audio_path.resolve()
    gt_render_path = gt_render_path.resolve()
    source_kind = audio_source_kind(audio_path)
    ppq = render_cfg.rpp_ppq
    rpp_layout: RppMidiLayout | None = None
    if delivery_midi is not None:
        pos = 0.0 if midi_item_position_s is None else float(midi_item_position_s)
        rpp_layout = build_rpp_midi_layout(
            delivery_midi,
            ppq=ppq,
            countin_keys=countin_keys,
            item_position_s=pos,
            exclude_track_names=exclude_track_names,
        )

    lines: list[str] = [
        '<REAPER_PROJECT 0.1 "7.0" 0',
        f"  TEMPO {tempo_bpm:.6f} 4 4",
    ]
    if tempo_map is not None:
        lines.extend(tempo_envelope_block(tempo_map.tempo_pt_lines))
        lines.extend(tempo_map.marker_lines)
    lines.extend(
        [
        "  <TRACK",
        '    NAME "audio"',
        "    <ITEM",
        "      POSITION 0",
        f"      LENGTH {length_s:.6f}",
        "      LOOP 0",
        f"      <SOURCE {source_kind}",
        f'        FILE "{_rpp_path(audio_path)}"',
        "      >",
        "    >",
        "  >",
        ]
    )
    if render_aligned_path is not None and render_offset_r is not None:
        from a2m.align import render_rpp_item_times

        render_aligned_path = render_aligned_path.resolve()
        pos, soffs, r_len = render_rpp_item_times(render_offset_r, length_s)
        lines.extend(
            [
                "  <TRACK",
                '    NAME "render_aligned"',
                "    <ITEM",
                f"      POSITION {pos:.6f}",
                f"      SOFFS {soffs:.6f}",
                f"      LENGTH {r_len:.6f}",
                "      LOOP 0",
                "      <SOURCE WAVE",
                f'        FILE "{_rpp_path(render_aligned_path)}"',
                "      >",
                "    >",
                "  >",
            ]
        )
    lines.extend(
        [
        "  <TRACK",
        '    NAME "gt_render"',
        "    <ITEM",
        "      POSITION 0",
        f"      LENGTH {length_s:.6f}",
        "      LOOP 0",
        "      <SOURCE WAVE",
        f'        FILE "{_rpp_path(gt_render_path)}"',
        "      >",
        "    >",
        "  >",
        ]
    )
    midi_pos = 0.0 if rpp_layout is None else rpp_layout.item_position_s
    for role in EXPORT_ROLES:
        notes = role_notes.get(role, [])
        if not notes:
            continue
        if rpp_layout is not None:
            ticks = rpp_layout.by_role.get(role, [])
            events = role_rpp_events_from_ticks(ticks, rpp_layout.end_rpp_tick)
        else:
            events = _role_rpp_events_legacy_seconds(
                notes, tempo_bpm, length_s, ppq
            )
        e_lines = _events_to_e_lines(events)
        lines.extend(
            [
                "  <TRACK",
                f'    NAME "gt_{role}"',
                "    <ITEM",
                f"      POSITION {midi_pos:.9f}",
                f"      LENGTH {length_s:.6f}",
                "      LOOP 0",
                "      <SOURCE MIDI",
                f"        HASDATA 1 {ppq} QN",
                *e_lines,
                "      >",
                "    >",
                "  >",
            ]
        )
    if countin_notes:
        if rpp_layout is not None:
            events = role_rpp_events_from_ticks(
                rpp_layout.countin, rpp_layout.end_rpp_tick
            )
        else:
            events = _role_rpp_events_legacy_seconds(
                countin_notes, tempo_bpm, length_s, ppq
            )
        e_lines = _events_to_e_lines(events)
        lines.extend(
            [
                "  <TRACK",
                '    NAME "gt_countin"',
                "    <ITEM",
                f"      POSITION {midi_pos:.9f}",
                f"      LENGTH {length_s:.6f}",
                "      LOOP 0",
                "      <SOURCE MIDI",
                f"        HASDATA 1 {ppq} QN",
                *e_lines,
                "      >",
                "    >",
                "  >",
            ]
        )
    if method_role_notes:
        for method in sorted(method_role_notes):
            by_role = method_role_notes[method]
            for role in EXPORT_ROLES:
                mnotes = by_role.get(role, [])
                if not mnotes:
                    continue
                events = _role_rpp_events_legacy_seconds(
                    mnotes, tempo_bpm, length_s, ppq
                )
                e_lines = _events_to_e_lines(events)
                lines.extend(
                    [
                        "  <TRACK",
                        f'    NAME "{method}_{role}"',
                        "    <ITEM",
                        "      POSITION 0",
                        f"      LENGTH {length_s:.6f}",
                        "      LOOP 0",
                        "      <SOURCE MIDI",
                        f"        HASDATA 1 {ppq} QN",
                        *e_lines,
                        "      >",
                        "    >",
                        "  >",
                    ]
                )
    lines.append(">")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    verify_rpp_file_paths(path)


def export_song_check_rpp(
    *,
    song_out: Path,
    audio_path: Path,
    mp3_y: np.ndarray,
    tempo_bpm: float,
    notes: list[EvalNote],
    sr: int,
    render_cfg: RenderConfig,
    countin_notes: list[EvalNote] | None = None,
    render_aligned_path: Path | None = None,
    render_offset_r: float | None = None,
    tempo_map: TempoMapBuild | None = None,
    delivery_midi=None,
    countin_keys: frozenset[tuple] | None = None,
    midi_item_position_s: float | None = None,
    method_role_notes: dict[str, dict[str, list[EvalNote]]] | None = None,
    exclude_track_names: frozenset[str] | None = None,
) -> Path:
    song_out = Path(song_out)
    length_s = audio_duration_seconds(audio_path)
    write_rpp_gt_midis(notes, song_out, tempo_bpm)
    if countin_notes:
        rpp_dir = song_out / "rpp_midi"
        rpp_dir.mkdir(parents=True, exist_ok=True)
        write_format0_role_midi(
            countin_notes,
            rpp_dir / "gt_countin.mid",
            tempo_bpm,
            "countin",
        )
    gt_render_path = song_out / "gt_render.wav"
    write_gt_render_wav(gt_render_path, notes, length_s, sr, render_cfg)
    gt_y = synthesize_gt_render(notes, length_s, sr, render_cfg)
    write_check_lr_wav(song_out / "check_LR.wav", mp3_y, gt_y, sr)

    by_role: dict[str, list[EvalNote]] = defaultdict(list)
    for n in notes:
        if n.role in EXPORT_ROLES:
            by_role[n.role].append(n)

    rpp_path = song_out / "check.rpp"
    write_check_rpp(
        rpp_path,
        tempo_bpm=tempo_bpm,
        audio_path=audio_path,
        gt_render_path=gt_render_path,
        length_s=length_s,
        role_notes=by_role,
        render_cfg=render_cfg,
        countin_notes=countin_notes,
        render_aligned_path=render_aligned_path,
        render_offset_r=render_offset_r,
        tempo_map=tempo_map,
        delivery_midi=delivery_midi,
        countin_keys=countin_keys,
        midi_item_position_s=midi_item_position_s,
        method_role_notes=method_role_notes,
        exclude_track_names=exclude_track_names,
    )
    verify_rpp_embedded_note_counts(rpp_path, role_notes=by_role)
    if delivery_midi is not None:
        from a2m.rpp_midi import verify_rpp_delivery_note_ticks

        verify_rpp_delivery_note_ticks(
            rpp_path,
            delivery_midi,
            ppq=render_cfg.rpp_ppq,
            countin_keys=countin_keys,
            exclude_track_names=exclude_track_names,
        )
    if tempo_map is not None:
        from a2m.rpp_tempo_map import verify_tempo_map_pt

        if not verify_tempo_map_pt(rpp_path, tempo_map.bar_heads):
            raise ValueError("TEMPOENVEX の PT が小節頭と ±1ms で一致しません")
    return rpp_path


def collect_check_rpp_midis(song_out: Path, methods: list[str]) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    for role in EXPORT_ROLES:
        p = song_out / "rpp_midi" / f"gt_{role}.mid"
        if p.is_file():
            items.append((f"gt_{role}", p.resolve()))
    for method in methods:
        for role in EXPORT_ROLES:
            p = song_out / "rpp_midi" / f"{method}_{role}.mid"
            if p.is_file():
                items.append((f"{method}_{role}", p.resolve()))
    return items
