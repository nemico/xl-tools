"""テンプレートの .rpp に、テンポマップと音源アイテムを入れる（M1）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from a2m.project_layout import ProjectTimeline, TempoPoint

_TRACK_OPEN = "  <TRACK"
_BLOCK_CLOSE = "  >"


@dataclass(frozen=True)
class AudioItem:
    track_index: int  # テンプレートのトラックの並び（0始まり）
    track_name: str | None  # None ならトラック名を変えない
    file_name: str  # "Media file" フォルダの中のファイル名
    length_s: float


@dataclass(frozen=True)
class MidiNote:
    start_tick: int
    end_tick: int
    pitch: int
    velocity: int


@dataclass(frozen=True)
class MidiItem:
    """テンプレートの、名前が track_name のトラックに入れる MIDI アイテム（プロジェクトの先頭から、拍単位）。"""

    track_name: str
    item_name: str
    channel: int  # 1始まり
    notes: list[MidiNote]
    length_s: float


@dataclass(frozen=True)
class Region:
    """Reaper のリージョン（上の欄に出る、名前の付いた範囲）。color は (R, G, B)。end_s が None ならマーカー（点）。"""

    start_s: float
    end_s: float | None
    name: str
    color: tuple[int, int, int]


def region_lines(regions: list[Region], first_index: int) -> list[str]:
    lines = []
    for k, r in enumerate(regions):
        idx = first_index + k
        rgb = r.color[0] | (r.color[1] << 8) | (r.color[2] << 16) | 0x1000000
        name = r.name.replace('"', "'")
        if r.end_s is None:
            lines.append(f'  MARKER {idx} {r.start_s:.12f} "{name}" 0 {rgb} 1 R {_guid()} 0')
            continue
        lines.append(f'  MARKER {idx} {r.start_s:.12f} "{name}" 1 {rgb} 1 R {_guid()} 0')
        lines.append(f'  MARKER {idx} {r.end_s:.12f} "" 1')
    return lines


def tempo_pt_lines(points: list[TempoPoint]) -> list[str]:
    lines = []
    for p in points:
        base = f"    PT {p.time_s:.12f} {float(p.bpm):.10f} 1"
        if p.numerator is not None:
            base += f" {65536 * 4 + p.numerator} 0 1"
        lines.append(base)
    return lines


def _guid() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


def _item_block(item: AudioItem, position_s: float, offset_s: float, media_dir: str, iid: int) -> list[str]:
    kind = {"mp3": "MP3", "flac": "FLAC"}.get(item.file_name.rsplit(".", 1)[-1].lower(), "WAVE")
    return [
        "    <ITEM",
        f"      POSITION {position_s:.14f}",
        "      SNAPOFFS 0",
        f"      LENGTH {max(item.length_s - offset_s, 0.0):.14f}",
        "      LOOP 0",
        "      ALLTAKES 0",
        "      FADEIN 1 0 0 1 0 0 0",
        "      FADEOUT 1 0 0 1 0 0 0",
        "      MUTE 0 0",
        "      SEL 0",
        f"      IGUID {_guid()}",
        f"      IID {iid}",
        f'      NAME "{item.file_name}"',
        "      VOLPAN 1 0 1 -1",
        f"      SOFFS {offset_s:.14f}",
        "      PLAYRATE 1 1 0 -1 0 0.0025",
        "      CHANMODE 0",
        f"      GUID {_guid()}",
        f"      <SOURCE {kind}",
        f'        FILE "{media_dir}\\{item.file_name}"',
        "      >",
        "    >",
    ]


def _midi_item_block(item: MidiItem, iid: int, ppq: int, off_velocity: int) -> list[str]:
    """Reaper の MIDI アイテム（HASDATA ... QN）。プロジェクトのテンポに乗るので、テンポが変わっても位置がずれない。"""
    events = []
    for n in item.notes:
        events.append((n.start_tick, 1, 0x90 | (item.channel - 1), n.pitch, n.velocity))
        events.append((n.end_tick, 0, 0x80 | (item.channel - 1), n.pitch, off_velocity))
    events.sort(key=lambda e: (e[0], e[1]))
    lines = [
        "    <ITEM",
        "      POSITION 0",
        "      SNAPOFFS 0",
        f"      LENGTH {item.length_s:.14f}",
        "      LOOP 0",
        "      ALLTAKES 0",
        "      FADEIN 1 0 0 1 0 0 0",
        "      FADEOUT 1 0 0 1 0 0 0",
        "      MUTE 0 0",
        "      SEL 0",
        f"      IGUID {_guid()}",
        f"      IID {iid}",
        f'      NAME "{item.item_name}"',
        "      VOLPAN 1 0 1 -1",
        "      SOFFS 0 0",
        "      PLAYRATE 1 1 0 -1 0 0.0025",
        "      CHANMODE 0",
        f"      GUID {_guid()}",
        "      <SOURCE MIDI",
        f"        HASDATA 1 {ppq} QN",
        "        CCINTERP 32",
        f"        POOLEDEVTS {_guid()}",
    ]
    last = 0
    for tick, _, status, d1, d2 in events:
        lines.append(f"        E {tick - last} {status:02x} {d1:02x} {d2:02x}")
        last = tick
    lines += [
        "        CCINTERP 32",
        f"        GUID {_guid()}",
        "        IGNTEMPO 0 120 4 4",
        "        SRCCOLOR 0",
        "        VELLANE -1 100 0",
        "      >",
        "    >",
    ]
    return lines


def _new_track_block(name: str, muted: bool, item_lines: list[str]) -> list[str]:
    guid = _guid()
    return [
        f"  <TRACK {guid}",
        f'    NAME "{name}"',
        "    PEAKCOL 16576",
        "    BEAT -1",
        "    AUTOMODE 0",
        "    VOLPAN 1 0 -1 -1 1",
        f"    MUTESOLO {1 if muted else 0} 0 0",
        "    IPHASE 0",
        "    PLAYOFFS 0 1",
        "    ISBUS 0 0",
        "    BUSCOMP 0 0 0 0 0",
        "    SHOWINMIX 1 0.6667 0.5 1 0.5 0 0 0 0",
        "    FIXEDLANES 9 0 0 0 0",
        "    SEL 0",
        "    REC 0 0 1 0 0 0 0 0",
        "    VU 2",
        "    TRACKHEIGHT 0 0 0 0 0 0 0",
        "    INQ 0 0 0 0.5 100 0 0 100",
        "    NCHAN 2",
        "    FX 1",
        f"    TRACKID {guid}",
        "    PERF 0",
        "    MIDIOUT -1",
        "    MAINSEND 1 0",
        *item_lines,
        "  >",
    ]


def _track_name_of(lines: list[str], start: int, end: int) -> str | None:
    for i in range(start, end):
        if lines[i].startswith("    NAME "):
            return lines[i][len("    NAME "):].strip().strip('"')
    return None


def _track_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """トラックごとの (開始行, 閉じる行) の一覧。"""
    ranges = []
    start = None
    for i, line in enumerate(lines):
        if line.startswith(_TRACK_OPEN):
            start = i
        elif start is not None and line.rstrip() == _BLOCK_CLOSE:
            ranges.append((start, i))
            start = None
    return ranges


def render_project(
    template_text: str,
    timeline: ProjectTimeline,
    items: list[AudioItem],
    *,
    media_dir: str,
    first_iid: int,
    midi_items: list[MidiItem] | None = None,
    ppq: int = 480,
    off_velocity: int = 64,
    regions: list[Region] | None = None,
    extra_track: tuple[str, str, MidiItem] | None = None,
) -> str:
    """extra_track: (新しいトラックの名前, その前に入れるトラックの名前, 入れる MIDI アイテム)。ミュートして入れる。"""
    lines = template_text.splitlines()
    if extra_track is not None:
        name, before, item = extra_track
        ranges = _track_ranges(lines)
        at = next((a for a, b in ranges if _track_name_of(lines, a, b) == before), None)
        if at is None:
            raise ValueError(f"テンプレートにトラック {before!r} が無い")
        lines[at:at] = _new_track_block(name, True, _midi_item_block(item, first_iid + 999, ppq, off_velocity))
    # リージョン（テンプレートのマーカーの後ろに足す。番号は重ならないようにする）
    if regions:
        marks = [i for i, l in enumerate(lines) if l.startswith("  MARKER ")]
        used = [int(lines[i].split()[1]) for i in marks]
        at = (marks[-1] + 1) if marks else next(i for i, l in enumerate(lines) if l.startswith("  <TRACK"))
        lines[at:at] = region_lines(regions, max(used, default=0) + 1)
    # MIDI アイテム（トラック名で探す。同じ名前が複数あれば最初のトラック）
    for n, item in enumerate(midi_items or []):
        ranges = _track_ranges(lines)
        target = next(((a, b) for a, b in ranges if _track_name_of(lines, a, b) == item.track_name), None)
        if target is None:
            raise ValueError(f"テンプレートにトラック {item.track_name!r} が無い")
        lines[target[1]:target[1]] = _midi_item_block(item, first_iid + 1000 + n, ppq, off_velocity)
    # 音源アイテム（後ろのトラックから入れる。行番号がずれないように）
    ranges = _track_ranges(lines)
    for n, item in sorted(enumerate(items), key=lambda x: -x[1].track_index):
        start, end = ranges[item.track_index]
        block = _item_block(item, timeline.item_position_s, timeline.item_offset_s, media_dir, first_iid + n)
        lines[end:end] = block
        if item.track_name is not None:
            for i in range(start, end):
                if lines[i].startswith("    NAME "):
                    lines[i] = f'    NAME "{item.track_name}"'
                    break
    # テンポ
    out: list[str] = []
    in_tempo = False
    for line in lines:
        if line.startswith("  TEMPO "):
            out.append(f"  TEMPO {timeline.master_bpm} 4 4 0")
        elif line.startswith("  <TEMPOENVEX"):
            in_tempo = True
            out.append(line)
        elif in_tempo and line.startswith("    PT "):
            continue
        elif in_tempo and line.rstrip() == _BLOCK_CLOSE:
            out.extend(tempo_pt_lines(timeline.points))
            out.append(line)
            in_tempo = False
        else:
            out.append(line)
    return "\n".join(out) + "\n"
