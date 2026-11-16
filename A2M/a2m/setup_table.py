"""納品MIDIから「セットアップ表」を作る（M0）。

1トラック1行：音色、実際の音量、パン、リバーブ・コーラス・ディレイ、EFX、音域など。
1曲1行：テンポ、システムエフェクト、EFX の種類。
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import mido

from a2m.xl_sysex import ADDR_EFX_TYPE, SYSTEM_BLOCK, efx_part_channel, parse_sysex

CC_VOLUME, CC_PAN, CC_EXPRESSION = 7, 10, 11
CC_REVERB, CC_CHORUS, CC_DELAY = 91, 93, 94
CC_BANK_MSB, CC_BANK_LSB = 0, 32
_PREFIX = re.compile(r"^A\d{1,2}R?\s*")
_KEYOFF = re.compile(r"^A\d{1,2}R\b")


@dataclass
class TrackSetup:
    song: str
    track_index: int
    name: str
    channel: int  # 1始まり
    role: str
    bank_msb: int | None
    bank_lsb: int | None
    program: int | None  # 1始まり
    program_changes: int
    n_notes: int
    pitch_min: int
    pitch_max: int
    poly: bool
    velocity_median: float
    volume: int | None
    expression_median: float | None
    level_db: float | None
    pan: int | None
    reverb: int | None
    chorus: int | None
    delay: int | None
    efx_on: bool


@dataclass
class SongSetup:
    song: str
    master_bpm: int
    tempo_changes: int
    efx_type: str
    efx_channels: list[int] = field(default_factory=list)
    system: dict[str, int] = field(default_factory=dict)  # "33" -> 値（アドレス3バイト目の16進）


def strip_track_prefix(name: str) -> str:
    return _PREFIX.sub("", name).strip()


def level_db(volume: float, expression: float, velocity: float) -> float:
    """GS の音量カーブ（各値の2乗）での、最大に対する音量（dB）。"""
    x = (volume / 127.0) * (expression / 127.0) * (velocity / 127.0)
    return 40.0 * math.log10(x) if x > 0 else float("-inf")


def classify_role(name: str, channel: int, is_last: bool, rules: dict[str, Any], m0: dict[str, Any]) -> str:
    ch = rules["channels"]
    low = strip_track_prefix(name).lower()
    if channel == ch["drums"]:
        return "drums"
    if channel == ch["bass"]:
        return "bass"
    if channel == ch["vocal"]:
        return "vocal"
    if channel == ch["vocal2"] and any(k in low for k in m0["vocal2_name_keys"]):
        return "vocal2"
    if is_last and any(k in low for k in m0["chorus_name_keys"]):
        return "chorus"
    if channel == ch["sub_drums"] and _KEYOFF.match(name):
        return "sub_drums"
    if _KEYOFF.match(name):
        return "se"
    return "other"


def _max_polyphony(intervals: list[tuple[int, int]]) -> int:
    events = sorted([(s, 1) for s, _ in intervals] + [(e, -1) for _, e in intervals], key=lambda x: (x[0], x[1]))
    cur = best = 0
    for _, d in events:
        cur += d
        best = max(best, cur)
    return best


def _read_track(track: mido.MidiTrack) -> dict[str, Any]:
    tick = 0
    r: dict[str, Any] = dict(name="", cc={}, expr=[], prog=None, n_pc=0, intervals=[], pitches=[], vels=[],
                             channel=None, tempos=[], sysex=[])
    open_notes: dict[int, int] = {}
    for msg in track:
        tick += msg.time
        if msg.type == "track_name":
            r["name"] = msg.name
        elif msg.type == "set_tempo":
            r["tempos"].append((tick, round(mido.tempo2bpm(msg.tempo))))
        elif msg.type == "sysex":
            p = parse_sysex(msg.data)
            if p is not None:
                r["sysex"].append(p)
        elif msg.type == "control_change":
            r["cc"].setdefault(msg.control, msg.value)
            if msg.control == CC_EXPRESSION:
                r["expr"].append(msg.value)
        elif msg.type == "program_change":
            r["n_pc"] += 1
            if r["prog"] is None:
                r["prog"] = msg.program + 1
        elif msg.type == "note_on" and msg.velocity > 0:
            r["channel"] = msg.channel + 1
            open_notes[msg.note] = tick
            r["pitches"].append(msg.note)
            r["vels"].append(msg.velocity)
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            start = open_notes.pop(msg.note, None)
            if start is not None:
                r["intervals"].append((start, tick))
    return r


def load_song_setup(path: Path, song: str, rules: dict[str, Any], m0: dict[str, Any]) -> tuple[SongSetup, list[TrackSetup]]:
    mid = mido.MidiFile(path)
    raw = [dict(_read_track(t), ti=i) for i, t in enumerate(mid.tracks)]
    tempos = sorted(x for r in raw for x in r["tempos"])
    efx_type = ""
    efx_channels: set[int] = set()
    system: dict[str, int] = {}
    for r in raw:
        for p in r["sysex"]:
            if p.address == ADDR_EFX_TYPE and len(p.data) >= 2:
                efx_type = efx_type or f"{p.data[0]:02X} {p.data[1]:02X}"
            elif p.address[:2] == SYSTEM_BLOCK and p.data:
                system.setdefault(f"{p.address[2]:02X}", p.data[0])
            else:
                ch = efx_part_channel(p)
                if ch is not None:
                    efx_channels.add(ch)
    noted = [r for r in raw if r["pitches"]]
    last_ti = max((r["ti"] for r in noted), default=-1)
    tracks: list[TrackSetup] = []
    for r in noted:
        cc = r["cc"]
        vol = cc.get(CC_VOLUME)
        expr_med = statistics.median(r["expr"]) if r["expr"] else None
        vel_med = statistics.median(r["vels"])
        lvl = level_db(vol, expr_med, vel_med) if vol is not None and expr_med is not None else None
        tracks.append(TrackSetup(
            song=song, track_index=r["ti"], name=r["name"], channel=r["channel"],
            role=classify_role(r["name"], r["channel"], r["ti"] == last_ti, rules, m0),
            bank_msb=cc.get(CC_BANK_MSB), bank_lsb=cc.get(CC_BANK_LSB), program=r["prog"],
            program_changes=r["n_pc"], n_notes=len(r["pitches"]),
            pitch_min=min(r["pitches"]), pitch_max=max(r["pitches"]),
            poly=_max_polyphony(r["intervals"]) > 1, velocity_median=vel_med,
            volume=vol, expression_median=expr_med, level_db=lvl,
            pan=cc.get(CC_PAN), reverb=cc.get(CC_REVERB), chorus=cc.get(CC_CHORUS), delay=cc.get(CC_DELAY),
            efx_on=r["channel"] in efx_channels,
        ))
    changes = sum(1 for i in range(1, len(tempos)) if tempos[i][1] != tempos[i - 1][1])
    song_setup = SongSetup(song=song, master_bpm=tempos[0][1] if tempos else 0, tempo_changes=changes,
                           efx_type=efx_type, efx_channels=sorted(efx_channels), system=system)
    return song_setup, tracks


def track_rows(tracks: list[TrackSetup]) -> list[dict[str, Any]]:
    return [asdict(t) for t in tracks]
