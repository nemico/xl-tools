"""モデルは版を固定し、ハッシュが合うときだけ使う。合わなければ落とさない。"""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class HashMismatch(RuntimeError):
    """ファイルはあるが、記録した SHA256 と違う。落とし直さない。"""


def check_present(path: Path, expected_sha256: str) -> str:
    """あるなら照合する。無いなら missing。違うなら止める。"""
    if not path.is_file():
        return "missing"
    got = sha256_file(path)
    if got != expected_sha256:
        raise HashMismatch(f"{path.name}: 記録 {expected_sha256} 実際 {got}")
    return "ok"
