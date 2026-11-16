"""原曲から拍と小節頭を推定する（M1）。Beat This! を使う。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

_MODELS: dict[tuple[str, str], object] = {}


def track_beats(audio_path: Path, *, checkpoint: str, device: str) -> tuple[np.ndarray, np.ndarray]:
    """(拍の時刻, 小節頭の時刻) を秒で返す。"""
    from beat_this.inference import File2Beats

    key = (checkpoint, device)
    if key not in _MODELS:
        _MODELS[key] = File2Beats(checkpoint_path=checkpoint, device=device, dbn=False)
    beats, downbeats = _MODELS[key](str(audio_path))
    return np.asarray(beats, dtype=float), np.asarray(downbeats, dtype=float)
