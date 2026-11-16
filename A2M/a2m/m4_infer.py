"""M4 の推論：分離音を MuScriptor（追加学習済み）で採譜する。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


def load_model(m4: dict[str, Any], ckpt: str | None, device: str = "cuda"):
    """MuScriptor を読み込む。ckpt を渡すと、<work_root>/ckpt/<ckpt>.pt の重みに入れ替える。"""
    import torch
    from muscriptor import TranscriptionModel

    tm = TranscriptionModel.load_model(m4["model_size"], device=device)
    if ckpt:
        from a2m.portable import resolve

        tm._model.load_state_dict(torch.load(resolve(m4["work_root"]) / "ckpt" / f"{ckpt}.pt", map_location=device))
    tm._model.eval()
    return tm


def transcribe_notes(tm, wav: np.ndarray, sr: int, instrument: str, batch: int, prelude_forcing: bool) -> list[tuple[float, float, int]]:
    """(始まり, 終わり, 音高) の一覧（秒）。wav は -1〜1 のモノラル。"""
    import torch
    from muscriptor import NoteEndEvent

    audio = torch.from_numpy(np.asarray(wav, dtype=np.float32))
    notes = []
    for ev in tm.transcribe((audio, sr), instruments=[instrument], batch_size=batch, prelude_forcing=prelude_forcing):
        if isinstance(ev, NoteEndEvent):
            notes.append((float(ev.start_event.start_time), float(ev.end_time), int(ev.start_event.pitch)))
    return sorted(notes)
