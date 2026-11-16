"""対応が取れた曲を、data/karaoke と同じ配置にする。元の音源と MIDI 以外は NA へ移す。"""

from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path

KEEP_NAMES = frozenset({"audio.mp3", "final.mid", "meta.yaml", "NA"})
_FOLDER_DATE = re.compile(r"^(\d{6})")


def delivered_from_folder_name(folder_name: str) -> date | None:
    """フォルダ名の先頭6桁（YYMMDD）を納品日にする。"""
    match = _FOLDER_DATE.match(folder_name)
    if not match:
        return None
    digits = match.group(1)
    try:
        return date(2000 + int(digits[0:2]), int(digits[2:4]), int(digits[4:6]))
    except ValueError:
        return None


def should_arrange(matched: int, limit: int) -> bool:
    return matched > limit


def _place(src: Path, dest: Path) -> None:
    if src.resolve() == dest.resolve():
        return
    if dest.exists():
        raise FileExistsError(dest)
    shutil.move(src, dest)


def arrange_song(song_dir: Path, audio: Path, midi: Path) -> date:
    """音源を audio.mp3、MIDI を final.mid にし、納品日を meta.yaml に書く。残りは NA へ移す。"""
    delivered = delivered_from_folder_name(song_dir.name)
    if delivered is None:
        raise ValueError(f"フォルダ名の先頭6桁を日付にできません: {song_dir.name}")
    if not audio.is_file() or not midi.is_file():
        raise FileNotFoundError(audio if not audio.is_file() else midi)
    dest_audio = song_dir / "audio.mp3"
    dest_midi = song_dir / "final.mid"
    na = song_dir / "NA"
    movers = []
    for child in song_dir.iterdir():
        if child.name in KEEP_NAMES:
            continue
        if child.resolve() in {audio.resolve(), midi.resolve()}:
            continue
        target = na / child.name
        if target.exists():
            raise FileExistsError(target)
        movers.append((child, target))
    _place(audio, dest_audio)
    _place(midi, dest_midi)
    (song_dir / "meta.yaml").write_text(f"delivered: {delivered.isoformat()}\n", encoding="utf-8")
    na.mkdir(exist_ok=True)
    for child, target in movers:
        if child.exists():
            shutil.move(child, target)
    return delivered
