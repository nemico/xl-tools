"""惜しい音の分類。許容はグリッド間隔の半分。固定の秒数には広げない。"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from a2m.evaluate import EvalNote
from a2m.midi_time import TempoEvent, second_to_tick, tempo_to_bpm
from a2m.track_profile import GRID_TICKS


def half_grid_s(bpm: float, grid: str) -> float:
    """クオンタイズが正しい格子に吸う範囲。格子間隔の半分。"""
    if bpm <= 0:
        return 0.0
    return (60.0 / bpm) * (GRID_TICKS[grid] / 480.0) / 2.0


def pick_grid(used: set[str] | frozenset[str], errors: dict[str, float]) -> str:
    """誤差が一番小さい刻み。三連がそれなら三連の間隔を使う。"""
    if not used:
        return "sixteenth"
    return min(used, key=lambda g: (errors.get(g, 1e9), GRID_TICKS[g]))


def bpm_at_tick(tick: int, tempos: list[TempoEvent]) -> float:
    current = tempos[0]
    for ev in tempos:
        if ev.tick > tick:
            break
        current = ev
    return tempo_to_bpm(current.tempo)


def windows_for_notes(
    notes: list[EvalNote],
    tempos: list[TempoEvent],
    grid: str,
    warp=None,
) -> list[float]:
    scale = warp.linear_scale if warp is not None and abs(warp.linear_scale) > 1e-9 else 1.0
    offset = warp.linear_offset if warp is not None else 0.0
    out = []
    for n in notes:
        midi_s = (n.start_s - offset) / scale if warp is not None else n.start_s
        tick = second_to_tick(midi_s, tempos, 480)
        out.append(half_grid_s(bpm_at_tick(tick, tempos), grid))
    return out


def _same_pitch(gn: EvalNote, dn: EvalNote, drums: bool) -> bool:
    if drums:
        return (dn.drum_class or "") == (gn.drum_class or "") or dn.pitch == gn.pitch
    return dn.pitch == gn.pitch


def _octave_delta(gn: EvalNote, dn: EvalNote) -> int | None:
    dp = dn.pitch - gn.pitch
    if dp != 0 and dp % 12 == 0:
        return dp
    return None


@dataclass
class NearMiss:
    n_exact: int
    n_timing: int
    n_octave: int
    n_pitch: int
    n_missing: int
    n_extra: int
    cost: int
    cost_timing: int
    cost_octave: int
    cost_pitch: int
    cost_missing: int
    cost_extra: int
    timing_pairs: list[tuple[EvalNote, EvalNote]]
    octave_pairs: list[tuple[EvalNote, EvalNote]]
    exact_pairs: list[tuple[EvalNote, EvalNote]]
    pitch_pairs: list[tuple[EvalNote, EvalNote]]


def _region_cost(times: list[float], exact_times: list[float], gap_s: float) -> int:
    if not times:
        return 0
    order = sorted(times)
    runs: list[list[float]] = [[order[0]]]
    for t in order[1:]:
        if t - runs[-1][-1] <= gap_s:
            runs[-1].append(t)
        else:
            runs.append([t])
    cost = 0
    for run in runs:
        mixed = any(run[0] <= t <= run[-1] for t in exact_times)
        cost += len(run) if mixed else 1
    return cost


def _octave_cost(pairs: list[tuple[float, int]], exact_times: list[float], gap_s: float) -> int:
    """pairs は (時刻, オクターブの差)。向きと量が同じ区間だけをまとめる。"""
    if not pairs:
        return 0
    by_dp: dict[int, list[float]] = {}
    for t, dp in pairs:
        by_dp.setdefault(dp, []).append(t)
    return sum(_region_cost(ts, exact_times, gap_s) for ts in by_dp.values())


def classify_near_miss(
    gt: list[EvalNote],
    draft: list[EvalNote],
    windows_s: list[float] | float,
    *,
    onset_tol_s: float,
    run_gap_s: float,
    drums: bool,
) -> NearMiss:
    """上から順に、合ってる / ずれ / オクターブ / 音名 / 足りない / 余分。"""
    gt_sorted = sorted(gt, key=lambda n: n.start_s)
    draft_sorted = sorted(draft, key=lambda n: n.start_s)
    d_starts = [n.start_s for n in draft_sorted]
    if isinstance(windows_s, (int, float)):
        wins = [float(windows_s)] * len(gt_sorted)
    else:
        # gt の元の順で渡された窓を、ソート後の順に並べる。
        order = sorted(range(len(gt)), key=lambda i: gt[i].start_s)
        wins = [windows_s[i] for i in order]
    used_g: set[int] = set()
    used_d: set[int] = set()
    exact_times: list[float] = []
    exact: list[tuple[EvalNote, EvalNote]] = []
    timing: list[tuple[EvalNote, EvalNote]] = []
    octave: list[tuple[EvalNote, EvalNote]] = []
    pitched: list[tuple[EvalNote, EvalNote]] = []
    n_pitch = 0

    def take(pairs, bucket):
        pairs.sort()
        for _dt, gi, di, kind in pairs:
            if gi in used_g or di in used_d:
                continue
            used_g.add(gi)
            used_d.add(di)
            gn, dn = gt_sorted[gi], draft_sorted[di]
            if kind == "exact":
                exact_times.append(gn.start_s)
                exact.append((gn, dn))
            elif kind == "timing":
                timing.append((gn, dn))
            elif kind == "octave":
                octave.append((gn, dn))
            else:
                pitched.append((gn, dn))
                bucket.append(1)

    # 1 合ってる
    exact_pairs = []
    for gi, gn in enumerate(gt_sorted):
        i0 = bisect.bisect_left(d_starts, gn.start_s - onset_tol_s)
        i1 = bisect.bisect_right(d_starts, gn.start_s + onset_tol_s)
        for di in range(i0, i1):
            dn = draft_sorted[di]
            if _same_pitch(gn, dn, drums):
                exact_pairs.append((abs(dn.start_s - gn.start_s), gi, di, "exact"))
    pitch_bucket: list[int] = []
    take(exact_pairs, pitch_bucket)

    # 2 ずれているだけ
    timing_pairs = []
    for gi, gn in enumerate(gt_sorted):
        if gi in used_g:
            continue
        limit = wins[gi] if gi < len(wins) else onset_tol_s
        if limit <= onset_tol_s:
            continue
        i0 = bisect.bisect_left(d_starts, gn.start_s - limit)
        i1 = bisect.bisect_right(d_starts, gn.start_s + limit)
        for di in range(i0, i1):
            if di in used_d:
                continue
            dn = draft_sorted[di]
            dt = abs(dn.start_s - gn.start_s)
            if dt <= onset_tol_s:
                continue
            if _same_pitch(gn, dn, drums):
                timing_pairs.append((dt, gi, di, "timing"))
    take(timing_pairs, pitch_bucket)

    # 3 オクターブ（ドラムには無い）
    if not drums:
        oct_pairs = []
        for gi, gn in enumerate(gt_sorted):
            if gi in used_g:
                continue
            i0 = bisect.bisect_left(d_starts, gn.start_s - onset_tol_s)
            i1 = bisect.bisect_right(d_starts, gn.start_s + onset_tol_s)
            for di in range(i0, i1):
                if di in used_d:
                    continue
                dn = draft_sorted[di]
                if _octave_delta(gn, dn) is None:
                    continue
                oct_pairs.append((abs(dn.start_s - gn.start_s), gi, di, "octave"))
        take(oct_pairs, pitch_bucket)

    # 4 音名違い
    wrong = []
    for gi, gn in enumerate(gt_sorted):
        if gi in used_g:
            continue
        i0 = bisect.bisect_left(d_starts, gn.start_s - onset_tol_s)
        i1 = bisect.bisect_right(d_starts, gn.start_s + onset_tol_s)
        for di in range(i0, i1):
            if di in used_d:
                continue
            dn = draft_sorted[di]
            wrong.append((abs(dn.start_s - gn.start_s), gi, di, "pitch"))
    take(wrong, pitch_bucket)
    n_pitch = len(pitch_bucket)

    n_missing = len(gt_sorted) - len(used_g)
    n_extra = len(draft_sorted) - len(used_d)
    timing_cost = _region_cost([g.start_s for g, _d in timing], exact_times, run_gap_s)
    oct_cost = _octave_cost(
        [(g.start_s, d.pitch - g.pitch) for g, d in octave],
        exact_times,
        run_gap_s,
    )
    cost = timing_cost + oct_cost + n_pitch + n_missing + n_extra
    return NearMiss(
        n_exact=len(exact_times),
        n_timing=len(timing),
        n_octave=len(octave),
        n_pitch=n_pitch,
        n_missing=n_missing,
        n_extra=n_extra,
        cost=cost,
        cost_timing=timing_cost,
        cost_octave=oct_cost,
        cost_pitch=n_pitch,
        cost_missing=n_missing,
        cost_extra=n_extra,
        timing_pairs=timing,
        octave_pairs=octave,
        exact_pairs=exact,
        pitch_pairs=pitched,
    )
