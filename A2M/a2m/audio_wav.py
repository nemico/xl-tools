"""第6.0節: mp3 → 参照 wav と復号ずれの測定。"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import soundfile as sf

from a2m.align import AlignConfig, audio_onset_chroma, first_onset_above_fraction, hop_seconds


@dataclass(frozen=True)
class AudioWavConfig:
    decode: str
    sr: int
    subtype: str

    @classmethod
    def from_dict(cls, p1: dict[str, Any]) -> AudioWavConfig:
        aw = p1.get("audio_wav") or {}
        return cls(
            decode=str(aw.get("decode", "auto")).lower(),
            sr=int(aw.get("sr", p1.get("sr", 44100))),
            subtype=str(aw.get("subtype", "PCM_16")),
        )


@dataclass(frozen=True)
class DecodeOffsetReport:
    tool: str
    mp3_rise_s: float
    wav_rise_s: float
    diff_ms: float


def _ffmpeg_convert(mp3: Path, wav: Path, sr: int) -> None:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FileNotFoundError("ffmpeg が PATH にありません")
    wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        exe,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(mp3),
        "-ar",
        str(sr),
        "-sample_fmt",
        "s16",
        str(wav),
    ]
    subprocess.run(cmd, check=True)


def _librosa_convert(mp3: Path, wav: Path, sr: int, subtype: str) -> None:
    y, file_sr = librosa.load(str(mp3), sr=sr, mono=False)
    if y.ndim == 1:
        data = y
    else:
        data = y.T
    wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(wav, data, sr, subtype=subtype)


def ensure_reference_wav(
    mp3_path: Path,
    wav_path: Path,
    cfg: AudioWavConfig,
    *,
    force: bool = False,
) -> str:
    """mp3 から wav を作る。戻り値は使用した方式（ffmpeg / librosa）。"""
    mp3_path = Path(mp3_path)
    wav_path = Path(wav_path)
    if (
        not force
        and wav_path.is_file()
        and wav_path.stat().st_mtime >= mp3_path.stat().st_mtime
    ):
        return "cached"
    prefer = cfg.decode
    if prefer == "librosa":
        _librosa_convert(mp3_path, wav_path, cfg.sr, cfg.subtype)
        return "librosa"
    if prefer == "ffmpeg":
        _ffmpeg_convert(mp3_path, wav_path, cfg.sr)
        return "ffmpeg"
    if shutil.which("ffmpeg"):
        _ffmpeg_convert(mp3_path, wav_path, cfg.sr)
        return "ffmpeg"
    _librosa_convert(mp3_path, wav_path, cfg.sr, cfg.subtype)
    return "librosa"


def rise_time_half_max(path: Path, align_cfg: AlignConfig) -> float:
    """立ち上がり強度が最大の 50% を初めて超える時刻（秒）。"""
    y, _ = librosa.load(str(path), sr=align_cfg.sr, mono=True)
    on, _ = audio_onset_chroma(y, align_cfg)
    hop_s = hop_seconds(align_cfg)
    return first_onset_above_fraction(on, hop_s, 0.5)


def measure_decode_offset_ms(
    mp3_path: Path,
    wav_path: Path,
    align_cfg: AlignConfig,
) -> DecodeOffsetReport:
    mp3_r = rise_time_half_max(mp3_path, align_cfg)
    wav_r = rise_time_half_max(wav_path, align_cfg)
    return DecodeOffsetReport(
        tool="",
        mp3_rise_s=mp3_r,
        wav_rise_s=wav_r,
        diff_ms=(wav_r - mp3_r) * 1000.0,
    )
