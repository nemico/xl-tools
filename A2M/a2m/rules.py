"""config.yaml の読み出し（納品仕様の決まり、段階ごとの設定）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_config_section(section: str, config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or REPO_ROOT / "config.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if section not in data:
        raise KeyError(f"config.yaml に {section} がありません")
    return data[section]


def load_delivery_rules(config_path: Path | None = None) -> dict[str, Any]:
    return load_config_section("delivery_rules", config_path)
