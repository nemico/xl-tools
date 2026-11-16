"""プロジェクトのフォルダにある材料（project.json、draft.json、marks.json）から .rpp を作り直す。

draft.json：下書きの MIDI アイテム（m4_draft.py が書く）。
marks.json：コードのリージョン（annotate_project.py が書く）。
chord_track.json：コードの構成音を塗りつぶした MIDI（annotate_project.py が書く）。17トラックの外に、ミュートして入れる。
どれかを入れ直すたびに、テンプレートから全部を作り直す。人が Reaper で保存した .rpp は上書きしない（project_files.py）。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from a2m.project_files import choose_rpp_path, load_project_state, write_rpp
from a2m.rpp_project import MidiItem, MidiNote, Region, render_project

DRAFT = "draft.json"
MARKS = "marks.json"
CHORD_TRACK = "chord_track.json"


def save_draft(proj: Path, items: list[MidiItem]) -> None:
    (proj / DRAFT).write_text(json.dumps([asdict(i) for i in items], ensure_ascii=False), encoding="utf-8")


def load_draft(proj: Path) -> list[MidiItem]:
    f = proj / DRAFT
    if not f.is_file():
        return []
    out = []
    for d in json.loads(f.read_text(encoding="utf-8")):
        out.append(MidiItem(d["track_name"], d["item_name"], d["channel"], [MidiNote(**n) for n in d["notes"]], d["length_s"]))
    return out


def save_marks(proj: Path, marks: list[Region]) -> None:
    (proj / MARKS).write_text(json.dumps([asdict(m) for m in marks], ensure_ascii=False), encoding="utf-8")


def load_marks(proj: Path) -> list[Region]:
    f = proj / MARKS
    if not f.is_file():
        return []
    return [Region(d["start_s"], d["end_s"], d["name"], tuple(d["color"])) for d in json.loads(f.read_text(encoding="utf-8"))]


def save_chord_track(proj: Path, name: str, before: str, item: MidiItem) -> None:
    data = {"name": name, "before": before, "item": asdict(item)}
    (proj / CHORD_TRACK).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_chord_track(proj: Path) -> tuple[str, str, MidiItem] | None:
    f = proj / CHORD_TRACK
    if not f.is_file():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    i = d["item"]
    return d["name"], d["before"], MidiItem(i["track_name"], i["item_name"], i["channel"], [MidiNote(**n) for n in i["notes"]], i["length_s"])


def build_project(proj: Path, name: str, template_text: str, m1: dict, ppq: int, off_velocity: int) -> Path:
    """材料をそろえて .rpp を書く。書いたパスを返す。"""
    timeline, audio_items = load_project_state(proj)
    text = render_project(template_text, timeline, audio_items, media_dir=m1["media_dir"], first_iid=m1["first_iid"],
                          midi_items=load_draft(proj), ppq=ppq, off_velocity=off_velocity, regions=load_marks(proj),
                          extra_track=load_chord_track(proj))
    rpp = choose_rpp_path(proj, name)
    write_rpp(rpp, text)
    return rpp
