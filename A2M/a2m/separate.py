"""音源分離。BS-RoFormer SW（6ステム）を、外部の実行ファイル（BSRoformer.cpp の CLI）で回す。

CLI は日本語のパスを扱えないことがあるので、英数字だけの作業フォルダで input.wav → out_stem_N.wav として回す。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


def stem_layout(sep: dict[str, Any], choice: str) -> dict[str, list[str]]:
    """出力するステムの組み方。{出力名: [足し合わせるモデルのステム, ...]}"""
    layouts = sep["layouts"]
    if choice not in layouts:
        raise ValueError(f"ステムの組み方は {list(layouts)} から選ぶ: {choice}")
    layout = {name: list(parts) for name, parts in layouts[choice].items()}
    used = sorted(p for parts in layout.values() for p in parts)
    if used != sorted(sep["stem_order"]):
        raise ValueError(f"組み方 {choice} が、モデルのステムを過不足なく使っていない")
    return layout


def combine_stems(stems: dict[str, np.ndarray], layout: dict[str, list[str]]) -> dict[str, np.ndarray]:
    """モデルのステムを、組み方どおりに足し合わせる。"""
    return {name: sum(stems[p] for p in parts) for name, parts in layout.items()}


def run_separation(wav_in: Path, work_dir: Path, sep: dict[str, Any], overlap: int | None) -> tuple[dict[str, np.ndarray], int]:
    """wav を分離して、{モデルのステム名: 波形 (サンプル, チャンネル)} とサンプリング周波数を返す。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    src = work_dir / "input.wav"
    shutil.copyfile(wav_in, src)
    from a2m.portable import resolve

    cmd = [str(resolve(sep["cli"])), str(resolve(sep["model"])), "input.wav", "out.wav"]
    if overlap:
        cmd += ["--overlap", str(overlap)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Reaper から呼ばれたとき、黒い窓を出さない
    proc = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, errors="replace", creationflags=flags)
    order = sep["stem_order"]
    files = [work_dir / f"out_stem_{k}.wav" for k in range(len(order))]
    if proc.returncode != 0 or not all(f.is_file() for f in files):
        raise RuntimeError(f"分離に失敗: {proc.stdout[-500:]} {proc.stderr[-500:]}")
    out: dict[str, np.ndarray] = {}
    sr = 0
    for name, f in zip(order, files):
        data, sr = sf.read(f, dtype="float32", always_2d=True)
        out[name] = data
    for f in [src, *files]:
        f.unlink(missing_ok=True)
    n = min(len(v) for v in out.values())
    return {k: v[:n] for k, v in out.items()}, sr
