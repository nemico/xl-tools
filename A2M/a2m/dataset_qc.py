"""200曲の検品。音源と MIDI を読む。位置合わせの数値は呼び出し側が渡す。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mido

import numpy as np

from a2m.align import AlignResult, LocalAlignSegment
from a2m.karaoke_midi import load_karaoke_midi
from a2m.onset_shift import best_onset_shift_s, count_matches


@dataclass(frozen=True)
class MidiFacts:
    readable: bool
    smf_type: int | None
    ticks_per_beat: int | None
    note_count: int
    melody: int
    bass: int
    drums: int
    kicks: int
    duration_s: float | None
    error: str


def read_midi_facts(path: Path) -> MidiFacts:
    try:
        mid = mido.MidiFile(path)
        loaded = load_karaoke_midi(path)
    except (OSError, ValueError, EOFError, IndexError, TypeError, KeyError) as exc:
        return MidiFacts(False, None, None, 0, 0, 0, 0, 0, None, str(exc))
    melody = sum(1 for note in loaded.notes if note.role in {"melody", "melody2"})
    bass = sum(1 for note in loaded.notes if note.role == "bass")
    drums = sum(1 for note in loaded.notes if note.role == "drums")
    kicks = sum(1 for note in loaded.notes if note.drum_class == "kick")
    return MidiFacts(
        True,
        int(mid.type),
        int(mid.ticks_per_beat),
        len(loaded.notes),
        melody,
        bass,
        drums,
        kicks,
        float(loaded.duration_s),
        "",
    )


def part1_reasons(
    facts: MidiFacts,
    audio_s: float | None,
    audio_error: str,
    *,
    smf_type: int,
    ticks_per_beat: int,
    length_diff_s: float,
) -> list[str]:
    reasons: list[str] = []
    if not facts.readable:
        reasons.append("MIDIが読めない")
        return reasons
    if audio_error or audio_s is None:
        reasons.append("音源が読めない")
    if facts.smf_type != smf_type or facts.ticks_per_beat != ticks_per_beat:
        reasons.append("要項に合わない")
    if facts.note_count == 0:
        reasons.append("音が無い")
    if facts.melody == 0 and facts.bass == 0 and facts.drums == 0:
        reasons.append("役割が判定できない")
    if audio_s is not None and facts.duration_s is not None:
        if abs(audio_s - facts.duration_s) > length_diff_s:
            reasons.append("長さが合わない")
    return reasons


def eight_bar_width_s(result: AlignResult) -> float | None:
    """8小節窓の、直線に乗らなかったずれの幅（最大−最小）。"""
    used = [seg for seg in result.local_segments if seg.used_for_fit]
    if len(used) < 2:
        return None
    residuals = [
        seg.local_offset_s - (result.offset + (result.scale - 1.0) * seg.center_s)
        for seg in used
    ]
    return max(residuals) - min(residuals)


def mismatch_ratio(intervals: list[dict], duration_s: float) -> float:
    if duration_s <= 0:
        return 1.0
    covered = sum(float(item["end_s"]) - float(item["start_s"]) for item in intervals)
    return covered / duration_s


def top_shift_candidates(
    ref_s: list[float],
    hyp_s: list[float],
    *,
    n: int = 5,
    tol_s: float = 0.05,
    span_s: float = 2.0,
    coarse_s: float = 0.01,
    separate_s: float = 0.05,
) -> list[tuple[float, int]]:
    """一致数が多いずれを、近い山をまとめて上位 n 個返す。"""
    if not ref_s or not hyp_s:
        return []
    ref = np.asarray(ref_s, dtype=np.float64)
    hyp = np.asarray(hyp_s, dtype=np.float64)
    shifts = np.arange(-span_s, span_s + coarse_s * 0.5, coarse_s)
    scored = []
    for shift in shifts:
        scored.append((float(shift), count_matches(ref, hyp + float(shift), tol_s)))
    scored.sort(key=lambda item: (-item[1], abs(item[0])))
    picked: list[tuple[float, int]] = []
    for shift, count in scored:
        if any(abs(shift - prev) <= separate_s for prev, _count in picked):
            continue
        picked.append((shift, count))
        if len(picked) == n:
            break
    return picked


def pair_shift_s(onset_s: float | None, chroma_s: float | None, *, tol_s: float) -> float | None:
    """立ち上がりとクロマが tol 以内なら、そのずれを返す。"""
    if onset_s is None or chroma_s is None:
        return None
    if max(onset_s, chroma_s) - min(onset_s, chroma_s) > tol_s:
        return None
    return float((onset_s + chroma_s) / 2.0)


def note_hit_rate(
    gt: list[tuple[str, str, float]],
    cand: list[tuple[str, str, float]],
    *,
    tol_s: float,
) -> float | None:
    """納品ノートのうち、同じ役割・同じ音（ドラムは同じ種類）が tol 以内にある割合。"""
    from a2m.evaluate import _match_onsets_smallest_first

    if not gt:
        return None
    grouped: dict[tuple[str, str], list[float]] = {}
    estimated: dict[tuple[str, str], list[float]] = {}
    for role, key, start_s in gt:
        grouped.setdefault((role, key), []).append(start_s)
    for role, key, start_s in cand:
        estimated.setdefault((role, key), []).append(start_s)
    hits = 0
    for key, times in grouped.items():
        tp, _fp, _fn = _match_onsets_smallest_first(
            np.asarray(times, dtype=np.float64),
            np.asarray(estimated.get(key, []), dtype=np.float64),
            tol_s,
        )
        hits += tp
    return hits / len(gt)


def is_low_outlier(value: float, reference: list[float], *, k: float) -> bool:
    """基準の平均から標準偏差の k 倍より低い。"""
    if len(reference) < 2:
        return False
    arr = np.asarray(reference, dtype=np.float64)
    return float(value) < float(arr.mean() - k * arr.std())


def shifts_agree(*values: float, tol_s: float) -> bool:
    if len(values) < 2:
        return False
    return max(values) - min(values) <= tol_s


def length_bucket(diff_s: float) -> str:
    diff = abs(diff_s)
    if diff < 5:
        return "5秒未満"
    if diff < 15:
        return "5〜15秒"
    if diff < 30:
        return "15〜30秒"
    if diff < 60:
        return "30〜60秒"
    return "60秒以上"


def assign_year_split(
    songs: list[tuple[str, int]],
    *,
    train: float,
    valid: float,
    holdout: float,
    seed: int,
) -> dict[str, str]:
    """納品年ごとに、指定の割合で学習・検証・最終確認へ分ける。"""
    import random

    by_year: dict[int, list[str]] = {}
    for name, year in songs:
        by_year.setdefault(year, []).append(name)
    out: dict[str, str] = {}
    rng = random.Random(seed)
    for year in sorted(by_year):
        names = by_year[year][:]
        rng.shuffle(names)
        n = len(names)
        n_hold = int(round(n * holdout))
        n_valid = int(round(n * valid))
        if n_hold + n_valid > n:
            n_valid = max(0, n - n_hold)
        n_train = n - n_hold - n_valid
        labels = ["最終確認"] * n_hold + ["検証"] * n_valid + ["学習"] * n_train
        for name, label in zip(names, labels):
            out[name] = label
    return out


def annot_color_name(rgb: tuple[float, float, float]) -> str:
    red, green, blue = rgb
    if red > 0.8 and green > 0.8 and blue < 0.4:
        return "黄"
    if red > 0.75 and green < 0.45 and blue < 0.45:
        return "赤"
    if red > 0.75 and 0.4 <= green <= 0.75 and blue < 0.4:
        return "オレンジ"
    if red > 0.7 and blue > 0.5 and green < 0.6:
        return "ピンク"
    if blue > red and blue > green and blue > 0.4:
        return "青"
    if green > red and green > blue and green > 0.4:
        return "緑"
    return "その他"


def kick_shift_s(audio_onsets_s: list[float], kick_times_s: list[float]) -> float | None:
    """位置合わせ後のキックへ、音源の打点を足す秒。測れなければ None。"""
    if not audio_onsets_s or not kick_times_s:
        return None
    return best_onset_shift_s(
        audio_onsets_s, kick_times_s, tol_s=0.05, span_s=0.2, coarse_s=0.005,
    )


def part2_reasons(
    *,
    kick_shift_s: float | None,
    bar_width_s: float | None,
    mismatch: float | None,
    kick_limit_s: float,
    width_limit_s: float,
    mismatch_limit: float,
) -> list[str]:
    reasons: list[str] = []
    if kick_shift_s is None:
        reasons.append("ずれが測れない")
    elif abs(kick_shift_s) > kick_limit_s:
        reasons.append("ずれが大きい")
    if bar_width_s is None or bar_width_s > width_limit_s:
        reasons.append("曲中で安定しない")
    if mismatch is None or mismatch > mismatch_limit:
        reasons.append("食い違いが多い")
    return reasons


def low_density_flags(values: list[float | None], *, iqr_k: float) -> list[bool]:
    """少ない側だけに印を付ける。落とす判定には使わない。"""
    present = [value for value in values if value is not None]
    if len(present) < 4:
        return [False for _ in values]
    ordered = sorted(present)
    def _q(fraction: float) -> float:
        pos = (len(ordered) - 1) * fraction
        lo = int(pos)
        hi = min(lo + 1, len(ordered) - 1)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
    fence = _q(0.25) - iqr_k * (_q(0.75) - _q(0.25))
    return [value is not None and value < fence for value in values]


def fake_align_result(offsets: list[float], *, scale: float = 1.0, line: float = 0.0) -> AlignResult:
    segments = [
        LocalAlignSegment(
            midi_start_s=float(i),
            midi_end_s=float(i + 1),
            center_s=float(i),
            local_offset_s=line + offset,
            used_for_fit=True,
        )
        for i, offset in enumerate(offsets)
    ]
    return AlignResult(
        offset=line,
        scale=scale,
        margin=1.0,
        peak1_offset_s=line,
        peak1_score=1.0,
        peak2_offset_s=None,
        peak2_score=None,
        best_offset_s_on=line,
        best_offset_s_ch=line,
        residual_median_s=None,
        residual_max_s=None,
        windows_total=len(segments),
        windows_used=len(segments),
        windows_stuck_edge=0,
        local_segments=segments,
    )
