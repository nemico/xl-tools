"""トラックを1本だけ鳴らす MIDI を作り、PhoenixMAX で wav にする（M3）。

音量を測るための書き出しなので、Volume・Expression・Pan・エフェクトの送りは決めた値にそろえる。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import mido

CC_VOLUME, CC_PAN, CC_EXPRESSION, CC_REVERB, CC_CHORUS, CC_DELAY = 7, 10, 11, 91, 93, 94


def neutral_track(track: mido.MidiTrack, values: dict[int, int]) -> mido.MidiTrack:
    """values にあるコントローラーを全部取り除き、最初のノートの直前に決めた値を1つずつ入れる。"""
    out = mido.MidiTrack()
    carry = 0
    channel = None
    inserted = False
    for msg in track:
        if msg.type == "control_change" and msg.control in values:
            carry += msg.time
            continue
        if not inserted and msg.type == "note_on" and msg.velocity > 0:
            channel = msg.channel
            first = True
            for control, value in values.items():
                out.append(mido.Message("control_change", channel=channel, control=control, value=value,
                                        time=(msg.time + carry) if first else 0))
                first = False
            out.append(msg.copy(time=0))
            carry = 0
            inserted = True
            continue
        out.append(msg.copy(time=msg.time + carry))
        carry = 0
    return out


def make_solo_midi(src: Path, track_indices: list[int], dst: Path, values: dict[int, int]) -> None:
    """指定したトラックだけを残した MIDI を書く。ノートの無いトラック（テンポ、SysEx）はそのまま残す。"""
    mid = mido.MidiFile(src)
    out = mido.MidiFile(type=mid.type, ticks_per_beat=mid.ticks_per_beat)
    for i, track in enumerate(mid.tracks):
        has_notes = any(m.type == "note_on" and m.velocity > 0 for m in track)
        if not has_notes:
            out.tracks.append(track)
        elif i in track_indices:
            out.tracks.append(neutral_track(track, values))
    out.save(dst)


def render_midi(mid: Path, wav: Path, tool: dict[str, Any], timeout_s: float) -> None:
    """PhoenixMAX で MIDI を wav にする。"""
    cmd = [str(Path(tool["exe"])), "-d", str(Path(tool["base_dir"])), *str(tool["extra_args"]).split(), str(mid), str(wav)]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout_s)
    if proc.returncode != 0 or not wav.is_file():
        raise RuntimeError(f"書き出しに失敗: {(proc.stderr or proc.stdout)[-300:]}")
