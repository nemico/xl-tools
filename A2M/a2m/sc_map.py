"""SC-VA / PhoenixMAX / X-Laben 音色表の参照（results/sc_map/）。"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

DEFAULT_SC_MAP_DIR = Path("results/sc_map")
X_LABEN_MAP_LSB = 4
X_LABEN_DRIVE_MSB_OFFSET = 80


def classify_program(msb: int, lsb: int, pc: int) -> str:
    """missing 組の種別（報告用）。"""
    if lsb == X_LABEN_MAP_LSB:
        return "xlaben_map"
    if msb >= X_LABEN_DRIVE_MSB_OFFSET:
        return "xlaben_drive_rule"
    return "parse_gap"


@lru_cache(maxsize=1)
def _load_sc_tone_index(path: str) -> dict[tuple[int, int, int], str]:
    index: dict[tuple[int, int, int], str] = {}
    p = Path(path)
    if not p.is_file():
        return index
    with p.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (int(row["msb"]), int(row["lsb"]), int(row["pc"]))
            index[key] = str(row["name"])
    return index


@lru_cache(maxsize=1)
def _load_drum_index(path: str) -> dict[tuple[int, int, int], str]:
    index: dict[tuple[int, int, int], str] = {}
    p = Path(path)
    if not p.is_file():
        return index
    with p.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if "set_pc" in row:
                key = (int(row["lsb"]), int(row["set_pc"]), int(row["note"]))
            else:
                key = (int(row.get("lsb", row.get("map", 3))), int(row["pc"]), int(row["note"]))
            index[key] = str(row["name"])
    return index


@lru_cache(maxsize=1)
def _load_xleben_tone_index(path: str) -> dict[tuple[int, int, int], str]:
    index: dict[tuple[int, int, int], str] = {}
    p = Path(path)
    if not p.is_file():
        return index
    with p.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (int(row["msb"]), int(row["lsb"]), int(row["pc"]))
            index[key] = str(row["name"])
    return index


@lru_cache(maxsize=1)
def _load_xleben_rules(path: str) -> dict[tuple[int, int, int], str]:
    index: dict[tuple[int, int, int], str] = {}
    p = Path(path)
    if not p.is_file():
        return index
    with p.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = str(row["name"])
            xl_msb, xl_lsb, xl_pc = int(row["xl_msb"]), int(row["xl_lsb"]), int(row["xl_pc"])
            index[(xl_msb, xl_lsb, xl_pc)] = name
            sc_msb, sc_lsb, sc_pc = row.get("sc_msb"), row.get("sc_lsb"), row.get("sc_pc")
            if sc_msb not in ("", None) and sc_lsb not in ("", None) and sc_pc not in ("", None):
                index[(int(sc_msb), int(sc_lsb), int(sc_pc))] = name
    return index


def load_known_program_keys(sc_map_dir: Path | None = None) -> set[tuple[int, int, int]]:
    """10曲突合用：Phoenix 表 + X-LEBEN 表 + 規則表の全組。"""
    base = (sc_map_dir or DEFAULT_SC_MAP_DIR).resolve()
    known: set[tuple[int, int, int]] = set()
    known.update(_load_sc_tone_index(str(base / "tones.csv")).keys())
    known.update(_load_xleben_tone_index(str(base / "xleben_tones.csv")).keys())
    known.update(_load_xleben_rules(str(base / "xleben_rules.csv")).keys())
    return known


def find_missing_programs(
    combos: set[tuple[int, int, int]],
    known: set[tuple[int, int, int]],
) -> list[tuple[int, int, int]]:
    return sorted(c for c in combos if c not in known)


def resolve_tone_name(
    msb: int,
    lsb: int,
    pc: int,
    *,
    sc_map_dir: Path | None = None,
    tones_path: Path | None = None,
) -> str:
    """音色名。Phoenix 表 → X-LEBEN LSB4 → X-LEBEN 規則 → MSB−80 → unknown。"""
    base = (sc_map_dir or DEFAULT_SC_MAP_DIR).resolve()
    sc_path = tones_path or (base / "tones.csv")
    sc_index = _load_sc_tone_index(str(sc_path.resolve()))
    key = (msb, lsb, pc)
    if key in sc_index:
        return sc_index[key]

    xl_index = _load_xleben_tone_index(str(base / "xleben_tones.csv"))
    if key in xl_index:
        return xl_index[key]

    rules = _load_xleben_rules(str(base / "xleben_rules.csv"))
    if key in rules:
        return rules[key]

    if msb >= X_LABEN_DRIVE_MSB_OFFSET:
        base_key = (msb - X_LABEN_DRIVE_MSB_OFFSET, lsb, pc)
        if base_key in sc_index:
            return f"{sc_index[base_key]} (X-Laben)"
        if base_key in rules:
            return f"{rules[base_key]} (X-Laben)"
        if base_key in xl_index:
            return f"{xl_index[base_key]} (X-Laben)"

    return "unknown"


def resolve_drum_name(
    lsb: int,
    set_pc: int,
    note: int,
    *,
    sc_map_dir: Path | None = None,
) -> str:
    base = (sc_map_dir or DEFAULT_SC_MAP_DIR).resolve()
    index = _load_drum_index(str(base / "drums.csv"))
    return index.get((lsb, set_pc, note), "unknown")


def drum_class_for_kit_note(
    lsb: int,
    set_pc: int,
    note: int,
    *,
    sc_map_dir: Path | None = None,
) -> str:
    """PhoenixMAX 表 + 第2.9節のクラス名。"""
    from a2m.drum_name_class import drum_class_from_name

    name = resolve_drum_name(lsb, set_pc, note, sc_map_dir=sc_map_dir)
    if name == "unknown":
        return "other"
    return drum_class_from_name(name)


def clear_tone_cache() -> None:
    _load_sc_tone_index.cache_clear()
    _load_drum_index.cache_clear()
    _load_xleben_tone_index.cache_clear()
    _load_xleben_rules.cache_clear()
