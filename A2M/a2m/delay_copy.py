"""ショートディレイ複製トラックの判定（仕様 第2.7節）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from a2m.karaoke_midi import KaraokeMidiData, KaraokeNote, has_keyoff_track_prefix


@dataclass(frozen=True)
class DelayCopyConfig:
    min_ms: float
    max_ms: float
    jitter_ms: float
    match_ratio: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DelayCopyConfig:
        return cls(
            min_ms=float(d["min_ms"]),
            max_ms=float(d["max_ms"]),
            jitter_ms=float(d["jitter_ms"]),
            match_ratio=float(d["match_ratio"]),
        )


@dataclass(frozen=True)
class DelayCopyMatch:
    """B は A のショートディレイ複製。"""

    track: str
    of: str
    lag_ms: float
    note_count: int
    match_ratio: float

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "track": self.track,
            "of": self.of,
            "lag_ms": round(self.lag_ms, 3),
            "note_count": self.note_count,
            "match_ratio": round(self.match_ratio, 4),
        }


@dataclass(frozen=True)
class SimpleNote:
    pitch: int
    start_s: float
    end_s: float


def is_delay_copy_eligible_track(track_name: str, track_role: str) -> bool:
    """第2.7節 手順0：drums と番号直後 R のトラックは対象外。"""
    if track_role == "drums":
        return False
    if has_keyoff_track_prefix(track_name):
        return False
    return True


def _track_roles(data: KaraokeMidiData) -> dict[str, str]:
    return {tr.name: tr.role for tr in data.tracks if tr.name}


def _notes_by_track(data: KaraokeMidiData) -> dict[str, list[KaraokeNote]]:
    out: dict[str, list[KaraokeNote]] = {}
    for n in data.notes:
        out.setdefault(n.track_name, []).append(n)
    return out


def score_delay_copy_pair(
    a_notes: list[SimpleNote],
    b_notes: list[SimpleNote],
    cfg: DelayCopyConfig,
) -> tuple[float, float, float] | None:
    """A が早い側。B が複製候補。満たすなら (match_ratio, lag_ms, matched_count)。"""
    if not a_notes or not b_notes:
        return None
    min_s = cfg.min_ms / 1000.0
    max_s = cfg.max_ms / 1000.0
    jitter_s = cfg.jitter_ms / 1000.0

    lags_per_b: list[float] = []
    for b in b_notes:
        best_lag: float | None = None
        for a in a_notes:
            if a.pitch != b.pitch:
                continue
            lag = b.start_s - a.start_s
            if min_s <= lag <= max_s:
                if best_lag is None or lag < best_lag:
                    best_lag = lag
        if best_lag is not None:
            lags_per_b.append(best_lag)

    if not lags_per_b:
        return None

    median_lag = float(np.median(lags_per_b))
    matched = sum(1 for lag in lags_per_b if abs(lag - median_lag) <= jitter_s)
    ratio = matched / len(b_notes)
    if ratio < cfg.match_ratio:
        return None
    return ratio, median_lag * 1000.0, float(matched)


def detect_delay_copies(
    data: KaraokeMidiData,
    cfg: DelayCopyConfig,
) -> list[DelayCopyMatch]:
    by_track = _notes_by_track(data)
    roles = _track_roles(data)
    simple: dict[str, list[SimpleNote]] = {}
    for name, notes in by_track.items():
        role = roles.get(name, notes[0].role if notes else "unknown")
        if not is_delay_copy_eligible_track(name, role):
            continue
        simple[name] = [
            SimpleNote(pitch=n.pitch, start_s=n.start_s, end_s=n.end_s) for n in notes
        ]

    matches: list[DelayCopyMatch] = []
    for b_name, b_notes in simple.items():
        if not b_notes:
            continue
        best: DelayCopyMatch | None = None
        for a_name, a_notes in simple.items():
            if a_name == b_name:
                continue
            scored = score_delay_copy_pair(a_notes, b_notes, cfg)
            if scored is None:
                continue
            ratio, lag_ms, _matched = scored
            cand = DelayCopyMatch(
                track=b_name,
                of=a_name,
                lag_ms=lag_ms,
                note_count=len(b_notes),
                match_ratio=ratio,
            )
            if best is None or cand.match_ratio > best.match_ratio:
                best = cand
        if best is not None:
            matches.append(best)
    return matches


def delay_copy_excluded_tracks(matches: list[DelayCopyMatch]) -> frozenset[str]:
    return frozenset(m.track for m in matches)


def delay_copy_from_json(raw: list[dict[str, Any]] | None) -> list[DelayCopyMatch]:
    if not raw:
        return []
    out: list[DelayCopyMatch] = []
    for item in raw:
        out.append(
            DelayCopyMatch(
                track=str(item["track"]),
                of=str(item["of"]),
                lag_ms=float(item["lag_ms"]),
                note_count=int(item["note_count"]),
                match_ratio=float(item.get("match_ratio", 1.0)),
            )
        )
    return out


def exclude_delay_copy_notes(
    notes: list[KaraokeNote],
    excluded_tracks: frozenset[str],
) -> list[KaraokeNote]:
    if not excluded_tracks:
        return notes
    return [n for n in notes if n.track_name not in excluded_tracks]
