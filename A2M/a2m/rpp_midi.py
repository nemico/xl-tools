"""第6.1c節: RPP 埋め込み MIDI の納品 tick（×2）。"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from a2m.countin import COUNTIN_ROLE, _note_key
from a2m.karaoke_midi import KaraokeMidiData, KaraokeNote
from a2m.midi_time import second_to_tick
RppNoteTick = tuple[int, int, int]  # on_rpp, off_rpp, pitch

EXPORT_ROLES = ("melody", "melody2", "bass", "drums", "other")
E_LINE_RE = re.compile(
    r"^\s*E\s+(\d+)\s+([0-9a-fA-F]{2})\s+([0-9a-fA-F]{2})\s+([0-9a-fA-F]{2})\s*$"
)


@dataclass(frozen=True)
class RppMidiLayout:
    by_role: dict[str, list[RppNoteTick]]
    countin: list[RppNoteTick]
    end_rpp_tick: int
    item_position_s: float


def delivery_to_rpp_tick(midi: KaraokeMidiData, midi_tick: int, *, ppq: int) -> int:
    scale = ppq // midi.ticks_per_beat
    return int(midi_tick * scale)


def note_rpp_ticks(
    n: KaraokeNote,
    midi: KaraokeMidiData,
    *,
    ppq: int,
) -> RppNoteTick:
    scale = ppq // midi.ticks_per_beat
    on = second_to_tick(n.start_s, midi.tempos, midi.ticks_per_beat) * scale
    off = second_to_tick(n.end_s, midi.tempos, midi.ticks_per_beat) * scale
    off = max(on + 1, off)
    return (on, off, int(n.pitch))


def build_rpp_midi_layout(
    delivery_midi: KaraokeMidiData,
    *,
    ppq: int,
    countin_keys: frozenset[tuple] | None = None,
    item_position_s: float = 0.0,
    exclude_track_names: frozenset[str] | None = None,
) -> RppMidiLayout:
    by_role: dict[str, list[RppNoteTick]] = defaultdict(list)
    countin: list[RppNoteTick] = []
    keys = countin_keys or frozenset()
    excluded = exclude_track_names or frozenset()
    for n in delivery_midi.notes:
        if n.track_name in excluded:
            continue
        key = _note_key(n)
        tick = note_rpp_ticks(n, delivery_midi, ppq=ppq)
        if key in keys:
            countin.append(tick)
            continue
        if n.role not in EXPORT_ROLES or n.role == COUNTIN_ROLE:
            continue
        by_role[n.role].append(tick)
    end_tick = (
        second_to_tick(
            delivery_midi.duration_s,
            delivery_midi.tempos,
            delivery_midi.ticks_per_beat,
        )
        * (ppq // delivery_midi.ticks_per_beat)
    )
    return RppMidiLayout(
        by_role=dict(by_role),
        countin=countin,
        end_rpp_tick=int(end_tick),
        item_position_s=float(item_position_s),
    )


def role_rpp_events_from_ticks(
    note_ticks: list[RppNoteTick],
    end_rpp_tick: int,
) -> list[tuple[int, tuple[int, int, int]]]:
    events: list[tuple[int, tuple[int, int, int]]] = []
    for on_t, off_t, pitch in note_ticks:
        pitch = max(0, min(127, pitch))
        events.append((off_t, (0x80, pitch, 0x00)))
        events.append((on_t, (0x90, pitch, 0x64)))
    events.sort(key=lambda x: (x[0], x[1][0]))
    events.append((end_rpp_tick, (0xB0, 0x7B, 0x00)))
    return events


def parse_rpp_track_note_on_ticks(rpp_text: str, track_name: str) -> list[tuple[int, int]]:
    lines = rpp_text.splitlines()
    in_track = False
    in_source = False
    prev = 0
    out: list[tuple[int, int]] = []
    for line in lines:
        if line.strip().startswith("NAME ") and f'"{track_name}"' in line:
            in_track = True
            in_source = False
            prev = 0
            continue
        if in_track and line.strip() == ">":
            if in_source:
                break
            in_track = False
        if in_track and "<SOURCE MIDI" in line:
            in_source = True
            prev = 0
            continue
        if in_source and line.strip().startswith("E "):
            m = E_LINE_RE.match(line)
            if not m:
                continue
            delta = int(m.group(1))
            prev += delta
            if m.group(2).lower() == "90":
                out.append((prev, int(m.group(3), 16)))
    return out


def expected_delivery_note_on_rpp_ticks(
    delivery_midi: KaraokeMidiData,
    *,
    role: str,
    ppq: int,
    countin_keys: frozenset[tuple] | None = None,
    countin_track: bool = False,
    exclude_track_names: frozenset[str] | None = None,
) -> list[tuple[int, int]]:
    keys = countin_keys or frozenset()
    excluded = exclude_track_names or frozenset()
    out: list[tuple[int, int]] = []
    for n in delivery_midi.notes:
        if n.track_name in excluded:
            continue
        key = _note_key(n)
        if countin_track:
            if key not in keys:
                continue
        else:
            if key in keys or n.role != role:
                continue
        on, _, pitch = note_rpp_ticks(n, delivery_midi, ppq=ppq)
        out.append((on, pitch))
    out.sort()
    return out


def verify_rpp_delivery_note_ticks(
    rpp_path,
    delivery_midi: KaraokeMidiData,
    *,
    ppq: int,
    countin_keys: frozenset[tuple] | None = None,
    exclude_track_names: frozenset[str] | None = None,
) -> None:
    from pathlib import Path

    text = Path(rpp_path).read_text(encoding="utf-8")
    keys = countin_keys or frozenset()
    for role in EXPORT_ROLES:
        excluded = exclude_track_names or frozenset()
        if not any(
            n.role == role
            and _note_key(n) not in keys
            and n.track_name not in excluded
            for n in delivery_midi.notes
        ):
            continue
        exp = expected_delivery_note_on_rpp_ticks(
            delivery_midi,
            role=role,
            ppq=ppq,
            countin_keys=keys,
            countin_track=False,
            exclude_track_names=excluded,
        )
        got = sorted(parse_rpp_track_note_on_ticks(text, f"gt_{role}"))
        exp = sorted(exp)
        if got != exp:
            raise ValueError(
                f"RPP gt_{role} tick 不一致: 先頭5 expected={exp[:5]} got={got[:5]}"
            )
    if keys:
        exp_c = expected_delivery_note_on_rpp_ticks(
            delivery_midi,
            role="",
            ppq=ppq,
            countin_keys=keys,
            countin_track=True,
            exclude_track_names=exclude_track_names,
        )
        got_c = sorted(parse_rpp_track_note_on_ticks(text, "gt_countin"))
        exp_c = sorted(exp_c)
        if got_c != exp_c:
            raise ValueError(
                f"RPP gt_countin tick 不一致: expected={len(exp_c)} got={len(got_c)}"
            )
