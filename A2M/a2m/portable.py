"""配布（ZIP）で動かすための決まり：config.yaml の相対パス、モデルの置き場、GPU が使えるか。

- config.yaml のパスは、リポジトリ（ZIP では A2M フォルダ）からの相対でも書ける（resolve）。
- A2M フォルダに models/ があれば、モデルはそこから読む（ネットにつながない。use_bundled_models）。
- GPU が使えなければ CPU で動かす（usable_device）。この PyTorch（CUDA 12.8 版）が使えるのは RTX 20 / GTX 16 以降（sm_75〜）。
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def resolve(p: str | Path, root: Path = REPO_ROOT) -> Path:
    """絶対パスはそのまま、相対パスは root からのパスにする。"""
    p = Path(p)
    return p if p.is_absolute() else root / p


def use_bundled_models(root: Path = REPO_ROOT, environ: dict | None = None) -> bool:
    """root/models があれば、Hugging Face と torch のモデルの置き場をそこにし、ネットにつながないようにする。
    すでに決めてある環境変数は変えない。使ったら True。"""
    env = os.environ if environ is None else environ
    models = root / "models"
    if not models.is_dir():
        return False
    env.setdefault("HF_HOME", str(models / "hf"))
    env.setdefault("TORCH_HOME", str(models / "torch"))
    env.setdefault("HF_HUB_OFFLINE", "1")
    return True


def usable_device(prefer: str = "cuda", torch_module=None) -> tuple[str, str]:
    """(使うデバイス, 理由)。prefer が cuda でも、GPU が無い・この PyTorch が対応していない GPU なら cpu。"""
    if prefer != "cuda":
        return prefer, f"設定で {prefer}"
    if torch_module is None:
        import torch as torch_module
    cuda = torch_module.cuda
    if not cuda.is_available():
        return "cpu", "NVIDIA の GPU が見つからないので CPU で動かします（時間がかかります）"
    name = cuda.get_device_name(0)
    major, minor = cuda.get_device_capability(0)
    arch = f"sm_{major}{minor}"
    if arch not in cuda.get_arch_list():
        return "cpu", f"{name}（{arch}）はこの PyTorch では使えないので CPU で動かします（時間がかかります。RTX 20 / GTX 16 以降なら GPU で動く）"
    return "cuda", f"{name}（{arch}）"
