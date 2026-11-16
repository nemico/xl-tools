"""曲フォルダ直下の VD+6桁 の音源と、同じ番号の MIDI が対応するかを見る。

対応が取れない曲は、人が選んだ音声と MIDI を、同じ場所へ VD+納品番号の名前でコピーする。元のファイルは消さない。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac"}
MIDI_SUFFIXES = {".mid", ".midi"}


@dataclass(frozen=True)
class DeliveryPair:
    status: str
    vd_id: str
    audio: Path | None
    midi: Path | None
    pdf_d: Path | None
    pdf_plain: Path | None
    pdf_w: Path | None
    pdfs: tuple[str, ...]
    mistakes: tuple[str, ...]


def _exact(folder: Path, pattern: re.Pattern[str]) -> list[Path]:
    if not folder.is_dir():
        return []
    return [p for p in folder.iterdir() if p.is_file() and pattern.match(p.name)]


def _one(paths: list[Path]) -> Path | None:
    return paths[0] if len(paths) == 1 else None


def _select_exact_audio(root_audios: list[Path], fix_audios: list[Path], audio_re: re.Pattern[str]) -> tuple[Path | None, str]:
    """VD+桁数だけの音源を1つ選ぶ。直下にあればそれを使い、無ければ Fix の中を使う。"""
    def ident(path: Path) -> str:
        match = audio_re.match(path.name)
        assert match is not None
        return match.group(1)

    ids = {ident(path) for path in root_audios + fix_audios}
    if not ids:
        return None, "音源が無い"
    if len(ids) > 1:
        return None, "音源が複数"
    vd_id = next(iter(ids))
    at_root = [path for path in root_audios if ident(path) == vd_id]
    in_fix = [path for path in fix_audios if ident(path) == vd_id]
    if len(at_root) == 1:
        return at_root[0], ""
    if len(at_root) > 1:
        return None, "音源が複数"
    if len(in_fix) == 1:
        return in_fix[0], ""
    return None, "音源が複数"


def classify_delivery(song_dir: Path, *, vd_digits: int) -> DeliveryPair:
    """音源は曲フォルダの直下か Fix の中。名前は VD と桁数だけ。それ以外の音声は音源にしない。"""
    if not song_dir.is_dir():
        return DeliveryPair("フォルダがありません", "", None, None, None, None, None, (), ())
    audio_re = re.compile(rf"^VD(\d{{{vd_digits}}})\.(mp3|wav)$", re.IGNORECASE)
    midi_re = re.compile(rf"^VD(\d{{{vd_digits}}})\.mid$", re.IGNORECASE)
    pdf_d_re = re.compile(rf"^D_VD(\d{{{vd_digits}}})\.pdf$", re.IGNORECASE)
    pdf_plain_re = re.compile(rf"^VD(\d{{{vd_digits}}})\.pdf$", re.IGNORECASE)
    pdf_w_re = re.compile(rf"^VD(\d{{{vd_digits}}})W\.pdf$", re.IGNORECASE)
    root_audios = _exact(song_dir, audio_re)
    mistakes = tuple(
        p.name for p in song_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp3", ".wav"} and p not in root_audios
    )
    fixes = [p for p in song_dir.iterdir() if p.is_dir() and p.name.lower() == "fix"]
    fix_audios: list[Path] = []
    midis = []
    for folder in fixes:
        fix_audios.extend(_exact(folder, audio_re))
    for folder in ([song_dir] + fixes):
        midis.extend(_exact(folder, midi_re))
    audio, audio_status = _select_exact_audio(root_audios, fix_audios, audio_re)
    pdfs = tuple(sorted(p.name for p in song_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"))
    pdf_d = _one(_exact(song_dir, pdf_d_re))
    pdf_plain = _one(_exact(song_dir, pdf_plain_re))
    pdf_w = _one(_exact(song_dir, pdf_w_re))
    audio_ids = {audio_re.match(p.name).group(1) for p in root_audios + fix_audios}
    midi_ids = {midi_re.match(p.name).group(1) for p in midis}
    if audio is None:
        midi = _one(midis)
        ids = set(audio_ids)
        if midi is not None:
            ids.add(midi_re.match(midi.name).group(1))
        vd_id = next(iter(ids)) if len(ids) == 1 else ""
        return DeliveryPair(audio_status, vd_id, None, midi, pdf_d, pdf_plain, pdf_w, pdfs, mistakes)
    vd_id = audio_re.match(audio.name).group(1)
    same = [p for p in midis if midi_re.match(p.name).group(1) == vd_id]
    if len(same) == 1:
        return DeliveryPair("対応している", vd_id, audio, same[0], pdf_d, pdf_plain, pdf_w, pdfs, mistakes)
    if not midis:
        return DeliveryPair("MIDIが無い", vd_id, audio, None, pdf_d, pdf_plain, pdf_w, pdfs, mistakes)
    if not same:
        return DeliveryPair("番号が違う", ",".join(sorted(audio_ids | midi_ids)), audio, None, pdf_d, pdf_plain, pdf_w, pdfs, mistakes)
    return DeliveryPair("MIDIが複数", vd_id, audio, None, pdf_d, pdf_plain, pdf_w, pdfs, mistakes)


def needs_choice(got: DeliveryPair) -> tuple[bool, bool]:
    """決まっていない側だけ人が選ぶ。(音源を選ぶ, MIDIを選ぶ)。"""
    return got.audio is None, got.midi is None


def list_song_media(song_dir: Path) -> tuple[list[Path], list[Path]]:
    """曲フォルダの中の音声と MIDI を、サブフォルダも含めて返す。"""
    audios: list[Path] = []
    midis: list[Path] = []
    for path in song_dir.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in AUDIO_SUFFIXES:
            audios.append(path)
        elif suffix in MIDI_SUFFIXES:
            midis.append(path)
    audios.sort()
    midis.sort()
    return audios, midis


def vd_ids_in_name(name: str, *, vd_digits: int) -> set[str]:
    return set(re.findall(rf"VD(\d{{{vd_digits}}})", name, flags=re.IGNORECASE))


def suggest_vd_id(*names: str, pdf_names: tuple[str, ...] = (), vd_digits: int) -> str:
    """選んだファイル名から納品番号を1つ取る。無ければ直下の PDF の名前から取る。複数あれば空。"""
    found: set[str] = set()
    for name in names:
        found |= vd_ids_in_name(Path(name).name, vd_digits=vd_digits)
    if len(found) == 1:
        return next(iter(found))
    if found:
        return ""
    for name in pdf_names:
        found |= vd_ids_in_name(name, vd_digits=vd_digits)
    if len(found) == 1:
        return next(iter(found))
    return ""


def _direct_fix(song_dir: Path) -> Path | None:
    fixes = [path for path in song_dir.iterdir() if path.is_dir() and path.name.lower() == "fix"]
    for folder in fixes:
        if folder.name == "Fix":
            return folder
    return fixes[0] if fixes else None


def copy_as_vd(src: Path, vd_id: str, *, song_dir: Path, vd_digits: int) -> Path:
    """判定が見る場所へ VD+納品番号の名前でコピーする。音源は曲フォルダの直下、MIDI は直下の Fix。Fix が無ければ MIDI も直下。元は残す。"""
    if not re.fullmatch(rf"\d{{{vd_digits}}}", vd_id):
        raise ValueError(f"納品番号は{vd_digits}桁の数字です: {vd_id}")
    if not src.is_file():
        raise FileNotFoundError(src)
    folder = song_dir
    if src.suffix.lower() in MIDI_SUFFIXES:
        fix = _direct_fix(song_dir)
        if fix is not None:
            folder = fix
    dest = folder / f"VD{vd_id}{src.suffix.lower()}"
    if dest.exists():
        if dest.resolve() == src.resolve():
            return dest
        raise FileExistsError(dest)
    shutil.copy2(src, dest)
    return dest
