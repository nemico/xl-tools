"""ドラム音名から評価用クラスを付ける（SPEC 2.9：上から順に最初の一致）。"""

from __future__ import annotations

import re

_PERC_SUBSTRINGS: tuple[str, ...] = (
    "conga",
    "bongo",
    "timbale",
    "agogo",
    "tambourin",
    "shaker",
    "maracas",
    "cowbell",
    "clave",
    "guiro",
    "cabasa",
    "whistle",
    "triangle",
    "wood block",
    "cuica",
    "vibraslap",
    "surdo",
    "tabla",
    "djembe",
    "taiko",
    "castanet",
    "udu",
    "shekere",
    "timpani",
    "talking drum",
    "bell tree",
    "bar chimes",
    "caxixi",
    "atarigane",
    "wadaiko",
    "tamborim",
    "jang-gu",
    "buk",
)

_SNARE_BRUSH_PHRASES: tuple[str, ...] = (
    "brush slap",
    "brush swirl",
    "brush tap",
    "brush roll",
    "brush long",
)


def _lower(name: str) -> str:
    return name.lower()


def _tokens(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _lower(name))


def _has_token(name: str, token: str) -> bool:
    t = token.lower()
    return t in _tokens(name)


def _is_bd_token(tok: str) -> bool:
    t = tok.lower()
    if t == "bd":
        return True
    if t.startswith("bd") and len(t) > 2 and t[2:].isdigit():
        return True
    if len(t) > 2 and t.endswith("bd"):
        return True
    return False


def _has_bd(name: str) -> bool:
    return any(_is_bd_token(t) for t in _tokens(name))


def _contains(name: str, sub: str) -> bool:
    return sub.lower() in _lower(name)


def _starts_with_tom(name: str) -> bool:
    for tok in _tokens(name):
        if tok.startswith("tom"):
            return True
    return False


def drum_class_from_name(name: str) -> str:
    """第2.9節の表どおり。常にいずれかのクラス名を返す（最終は other）。"""
    n = _lower(name)

    if _contains(name, "rim shot") or _contains(name, "side stick"):
        return "rim"
    if not _contains(name, "reverse") and (
        _has_token(name, "stick") or _has_token(name, "sticks")
    ):
        return "rim"

    if _contains(name, "clap"):
        return "clap"

    if _contains(name, "kick") or _contains(name, "bass drum") or _has_bd(name):
        return "kick"

    if _contains(name, "snare") or any(_has_token(name, t) for t in ("sn", "sd", "snr")):
        return "snare"
    if any(_contains(name, p) for p in _SNARE_BRUSH_PHRASES):
        return "snare"

    if any(
        _contains(name, s)
        for s in ("hi-hat", "hihat", "hi hat", "high hat")
    ) or any(_has_token(name, t) for t in ("hh", "chh", "ohh", "phh", "mhh")):
        return "hihat"

    if _starts_with_tom(name):
        return "tom"

    if _contains(name, "crash"):
        return "crash"

    if _contains(name, "ride"):
        return "ride"

    if any(_contains(name, s) for s in ("cymbal", "china", "splash", "gong")):
        return "cymbal"

    if any(_contains(name, s) for s in _PERC_SUBSTRINGS):
        return "perc"

    if "rim" in n:
        return "rim"

    return "other"


def drum_class_from_name_or_other(name: str) -> str:
    return drum_class_from_name(name)


def unclassified_drum_names(names: set[str]) -> list[str]:
    """クラス other の名前（効果音など）。"""
    return sorted(n for n in names if drum_class_from_name(n) == "other")
