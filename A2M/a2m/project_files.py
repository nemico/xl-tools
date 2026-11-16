"""プロジェクトのフォルダの読み書き（M1・M4）。

- project.json：テンポマップと、音源アイテムの並び。下書きを入れ直すときに、プロジェクトを作り直すのに使う。
- 人が Reaper で保存した .rpp は上書きしない。こちらが書いたときの中身の指紋（sha1）を .a2m_written.json に残し、
  指紋が合う（＝人が手を入れていない）ファイルだけ書き直す。合わなければ別名（<名前>_AI.rpp、_AI2.rpp …）に書く。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from a2m.project_layout import ProjectTimeline, timeline_from_dict, timeline_to_dict
from a2m.rpp_project import AudioItem

STATE = "project.json"
WRITTEN = ".a2m_written.json"


def save_project_state(out_dir: Path, timeline: ProjectTimeline, audio_items: list[AudioItem]) -> None:
    state = {"timeline": timeline_to_dict(timeline), "audio_items": [asdict(a) for a in audio_items]}
    (out_dir / STATE).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def load_project_state(out_dir: Path) -> tuple[ProjectTimeline, list[AudioItem]]:
    state = json.loads((out_dir / STATE).read_text(encoding="utf-8"))
    return timeline_from_dict(state["timeline"]), [AudioItem(**a) for a in state["audio_items"]]


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _written(out_dir: Path) -> dict[str, str]:
    f = out_dir / WRITTEN
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


def choose_rpp_path(out_dir: Path, name: str) -> Path:
    """書いてよい .rpp のパス。まだ無いか、こちらが書いたままのものを、<名前>.rpp → <名前>_AI.rpp → … の順に探す。"""
    written = _written(out_dir)
    k = 0
    while True:
        path = out_dir / (f"{name}.rpp" if k == 0 else f"{name}_AI.rpp" if k == 1 else f"{name}_AI{k}.rpp")
        if not path.exists() or written.get(path.name) == _sha1(path.read_bytes()):
            return path
        k += 1


def write_rpp(path: Path, text: str) -> None:
    """.rpp を書き、指紋を残す。"""
    data = text.encode("utf-8")
    path.write_bytes(data)
    written = _written(path.parent)
    written[path.name] = _sha1(data)
    (path.parent / WRITTEN).write_text(json.dumps(written, ensure_ascii=False, indent=1), encoding="utf-8")
