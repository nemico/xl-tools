"""Cakewalk / Sonar 形式の Instrument Definition（PhoenixMAX）を読む。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PATCH_LINE = re.compile(r"^Patch\[(\d+|\*)\]=(.+)$")
KEY_LINE = re.compile(r"^Key\[(\d+|\*),(\d+|\*)\]=(.+)$")
KV_LINE = re.compile(r"^(\d+)=(.*)$")
SECTION_LINE = re.compile(r"^\[(.+)\]$")

MODE_PATCH = "patch_names"
MODE_NOTE = "note_names"
MODE_CTRL = "controller_names"
MODE_NRPN = "nrpn_names"
MODE_INST = "instrument_definitions"


@dataclass
class ParsedInstrumentDef:
    patch_sections: dict[str, dict[int, str]] = field(default_factory=dict)
    note_sections: dict[str, dict[int, str]] = field(default_factory=dict)
    note_based_on: dict[str, str] = field(default_factory=dict)
    controller_sections: dict[str, dict[int, str]] = field(default_factory=dict)
    nrpn_sections: dict[str, dict[int, str]] = field(default_factory=dict)
    phoenix_patches: dict[int, str] = field(default_factory=dict)
    phoenix_drum_keys: dict[tuple[int, int], str] = field(default_factory=dict)


def parse_instrument_definition(text: str) -> ParsedInstrumentDef:
    out = ParsedInstrumentDef()
    mode: str | None = None
    inst_block: str | None = None
    current_section: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("."):
            tag = line[1:].strip().lower()
            if tag == "patch names":
                mode = MODE_PATCH
                inst_block = None
                current_section = None
            elif tag == "note names":
                mode = MODE_NOTE
                inst_block = None
                current_section = None
            elif tag == "controller names":
                mode = MODE_CTRL
                inst_block = None
                current_section = None
            elif tag == "nrpn names":
                mode = MODE_NRPN
                inst_block = None
                current_section = None
            elif tag == "instrument definitions":
                mode = MODE_INST
                inst_block = None
                current_section = None
            continue

        if mode == MODE_INST:
            msec = SECTION_LINE.match(line)
            if msec:
                current_section = msec.group(1)
                inst_block = current_section
                continue
            if inst_block == "PhoenixMAX":
                mp = PATCH_LINE.match(line)
                if mp and mp.group(1) != "*":
                    out.phoenix_patches[int(mp.group(1))] = mp.group(2).strip()
                continue
            if inst_block == "PhoenixMAX Drumsets":
                mk = KEY_LINE.match(line)
                if mk and mk.group(1) != "*" and mk.group(2) != "*":
                    lsb, pc = int(mk.group(1)), int(mk.group(2))
                    out.phoenix_drum_keys[(lsb, pc)] = mk.group(3).strip()
                continue
            continue

        msec = SECTION_LINE.match(line)
        if msec:
            current_section = msec.group(1)
            if mode == MODE_PATCH:
                out.patch_sections.setdefault(current_section, {})
            elif mode == MODE_NOTE:
                out.note_sections.setdefault(current_section, {})
            elif mode == MODE_CTRL:
                out.controller_sections.setdefault(current_section, {})
            elif mode == MODE_NRPN:
                out.nrpn_sections.setdefault(current_section, {})
            continue

        if line.startswith("BasedOn=") and mode == MODE_NOTE and current_section:
            out.note_based_on[current_section] = line.split("=", 1)[1].strip()
            continue

        mkv = KV_LINE.match(line)
        if not mkv or current_section is None:
            continue
        key, val = int(mkv.group(1)), mkv.group(2).strip()
        if mode == MODE_PATCH:
            out.patch_sections[current_section][key] = val
        elif mode == MODE_NOTE:
            out.note_sections[current_section][key] = val
        elif mode == MODE_CTRL:
            out.controller_sections[current_section][key] = val
        elif mode == MODE_NRPN:
            out.nrpn_sections[current_section][key] = val

    return out


def expand_note_map(
    section: str,
    sections: dict[str, dict[int, str]],
    based_on: dict[str, str],
    _stack: frozenset[str] | None = None,
) -> dict[int, str]:
    stack = _stack or frozenset()
    if section in stack:
        return {}
    merged: dict[int, str] = {}
    parent = based_on.get(section)
    if parent:
        merged.update(
            expand_note_map(parent, sections, based_on, stack | {section})
        )
    merged.update(sections.get(section, {}))
    return merged


def capital_section_for_lsb(parsed: ParsedInstrumentDef, lsb: int) -> str | None:
    idx = lsb
    name = parsed.phoenix_patches.get(idx)
    if name and "Capital Tones" in name:
        return name
    for _idx, sect in parsed.phoenix_patches.items():
        if _idx % 128 == lsb and "Capital Tones" in sect:
            return sect
    return None


def resolve_patch_name(
    parsed: ParsedInstrumentDef,
    msb: int,
    lsb: int,
    pc: int,
) -> tuple[str | None, str | None]:
    """音色名と Patch 節名。無ければ (None, None)。"""
    idx = msb * 128 + lsb
    section = parsed.phoenix_patches.get(idx)
    if section is None:
        return None, None
    var_map = parsed.patch_sections.get(section, {})
    if pc in var_map:
        return var_map[pc], section
    capital = capital_section_for_lsb(parsed, lsb)
    if capital:
        cap_map = parsed.patch_sections.get(capital, {})
        if pc in cap_map:
            return cap_map[pc], section
    if pc in var_map:
        return var_map[pc], section
    return None, section


def build_tone_rows(parsed: ParsedInstrumentDef) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    seen: set[tuple[int, int, int]] = set()
    for idx, section in sorted(parsed.phoenix_patches.items()):
        if "Drumsets" in section:
            continue
        msb, lsb = idx // 128, idx % 128
        var_map = parsed.patch_sections.get(section, {})
        capital = capital_section_for_lsb(parsed, lsb)
        cap_map = parsed.patch_sections.get(capital, {}) if capital else {}
        for pc in range(128):
            name = var_map.get(pc) or cap_map.get(pc)
            if not name:
                continue
            key = (msb, lsb, pc)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "msb": msb,
                    "lsb": lsb,
                    "pc": pc,
                    "name": name,
                    "section": section,
                }
            )
    return rows


def build_drum_rows(parsed: ParsedInstrumentDef) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for (lsb, set_pc), set_name in sorted(parsed.phoenix_drum_keys.items()):
        note_map = expand_note_map(
            set_name, parsed.note_sections, parsed.note_based_on
        )
        for note, name in sorted(note_map.items()):
            rows.append(
                {
                    "lsb": lsb,
                    "set_pc": set_pc,
                    "note": note,
                    "name": name,
                    "set_name": set_name,
                }
            )
    return rows


def build_controller_rows(parsed: ParsedInstrumentDef) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for section, mapping in sorted(parsed.controller_sections.items()):
        for cc, name in sorted(mapping.items()):
            rows.append({"cc": cc, "name": name, "section": section})
    return rows


def build_nrpn_rows(parsed: ParsedInstrumentDef) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for section, mapping in sorted(parsed.nrpn_sections.items()):
        for num, name in sorted(mapping.items()):
            rows.append({"number": num, "name": name, "section": section})
    return rows


def load_parsed(path: Path) -> ParsedInstrumentDef:
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_instrument_definition(text)


def drum_set_note_names(
    parsed: ParsedInstrumentDef, lsb: int, set_pc: int
) -> dict[int, str]:
    set_name = parsed.phoenix_drum_keys.get((lsb, set_pc))
    if not set_name:
        return {}
    return expand_note_map(set_name, parsed.note_sections, parsed.note_based_on)
