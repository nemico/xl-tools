"""第5.1節：「情報があるか」の診断。"""

from __future__ import annotations

from dataclasses import dataclass

from a2m.evaluate import (
    EvalConfig,
    EvalNote,
    est_notes_for_eval_role,
    evaluate_drums,
    evaluate_pitched_role,
    evaluate_song_method,
    ref_notes_for_eval_role,
    stack_drum_hits,
)


DIAG_ROLES = ("melody", "bass", "other", "drums")


def _match_onset(
    ref: EvalNote,
    est: EvalNote,
    tol_s: float,
    *,
    octave: bool,
) -> bool:
    if abs(ref.start_s - est.start_s) > tol_s:
        return False
    if octave:
        return int(ref.pitch) % 12 == int(est.pitch) % 12
    return ref.pitch == est.pitch


def union_recall_drums_stacked(
    ref_notes: list[EvalNote],
    est_union: list[EvalNote],
    tol_s: float,
    stack_window_s: float,
) -> float:
    ref_hits = stack_drum_hits(ref_notes, stack_window_s)
    est_hits = stack_drum_hits(est_union, stack_window_s)
    if not ref_hits:
        return float("nan")
    hit = 0
    for r in ref_hits:
        if any(
            r.drum_class == e.drum_class
            and abs(r.start_s - e.start_s) <= tol_s
            for e in est_hits
        ):
            hit += 1
    return hit / len(ref_hits)


def union_recall(
    ref_notes: list[EvalNote],
    est_union: list[EvalNote],
    tol_s: float,
    *,
    octave: bool = False,
) -> float:
    if not ref_notes:
        return float("nan")
    hit = 0
    for r in ref_notes:
        if any(_match_onset(r, e, tol_s, octave=octave) for e in est_union):
            hit += 1
    return hit / len(ref_notes)


def est_note_breakdown(
    ref_notes: list[EvalNote],
    est_notes: list[EvalNote],
    tol_s: float,
) -> dict[str, float]:
    if not est_notes:
        return {"match": 0.0, "time_only": 0.0, "orphan": 0.0}
    a = b = c = 0
    for e in est_notes:
        near = [r for r in ref_notes if abs(r.start_s - e.start_s) <= tol_s]
        if not near:
            c += 1
        elif any(r.pitch == e.pitch for r in near):
            a += 1
        else:
            b += 1
    n = len(est_notes)
    return {"match": a / n, "time_only": b / n, "orphan": c / n}


def pitch_set_jaccard_at_second(
    ref_notes: list[EvalNote],
    est_notes: list[EvalNote],
    t0: float,
    t1: float,
) -> float:
    scores: list[float] = []
    t = int(t0)
    end = int(t1) + 1
    while t < end:
        lo, hi = float(t), float(t + 1)
        r = {
            int(n.pitch) % 12
            for n in ref_notes
            if n.end_s > lo and n.start_s < hi
        }
        e = {
            int(n.pitch) % 12
            for n in est_notes
            if n.end_s > lo and n.start_s < hi
        }
        if not r and not e:
            scores.append(1.0)
        elif not r or not e:
            scores.append(0.0)
        else:
            scores.append(len(r & e) / len(r | e))
        t += 1
    return sum(scores) / len(scores) if scores else float("nan")


@dataclass(frozen=True)
class SongDiagnosis:
    song: str
    union_recall_exact: dict[str, float]
    union_recall_octave: dict[str, float]
    f1_by_tol: dict[str, dict[str, dict[float, float]]]  # method -> role -> tol_ms -> f1
    chroma_jaccard: dict[str, dict[str, float]]  # method -> role -> mean jaccard
    breakdown: dict[str, dict[str, dict[str, float]]]  # method -> role -> fractions


def diagnose_song(
    song: str,
    ref_notes: list[EvalNote],
    est_by_method: dict[str, list[EvalNote]],
    audio_duration_s: float,
    base_cfg: EvalConfig,
    exclude_intervals: list | None,
) -> SongDiagnosis:
    bounds = evaluate_song_method(
        ref_notes, est_by_method.get("M1", []), audio_duration_s, base_cfg,
        exclude_intervals=exclude_intervals,
    ).get("window")
    if bounds is None:
        empty: dict[str, float] = {r: float("nan") for r in DIAG_ROLES}
        return SongDiagnosis(
            song=song,
            union_recall_exact=dict(empty),
            union_recall_octave=dict(empty),
            f1_by_tol={m: {r: {} for r in DIAG_ROLES} for m in est_by_method},
            chroma_jaccard={m: {r: float("nan") for r in ("melody", "bass", "other")} for m in est_by_method},
            breakdown={
                m: {
                    r: {"match": 0.0, "time_only": 0.0, "orphan": 0.0}
                    for r in ("melody", "bass", "other")
                }
                for m in est_by_method
            },
        )
    t0, t1 = bounds["t0"], bounds["t1"]

    union_exact: dict[str, float] = {}
    union_oct: dict[str, float] = {}
    f1_tol: dict[str, dict[str, dict[float, float]]] = {
        m: {r: {} for r in DIAG_ROLES} for m in est_by_method
    }
    chroma: dict[str, dict[str, float]] = {
        m: {} for m in est_by_method
    }
    breakdown: dict[str, dict[str, dict[str, float]]] = {
        m: {} for m in est_by_method
    }

    ref_in = [n for n in ref_notes if t0 <= n.start_s <= t1]

    for role in DIAG_ROLES:
        ref_r = ref_notes_for_eval_role(ref_in, role)
        est_u: list[EvalNote] = []
        for m in est_by_method:
            est_u.extend(
                est_notes_for_eval_role(
                    [n for n in est_by_method[m] if t0 <= n.start_s <= t1],
                    role,
                )
            )
        if role == "drums":
            union_exact[role] = union_recall_drums_stacked(
                ref_r,
                est_u,
                base_cfg.onset_tol_s,
                base_cfg.drum_stack_window_s,
            )
            union_oct[role] = union_exact[role]
        else:
            union_exact[role] = union_recall(
                ref_r, est_u, base_cfg.onset_tol_s, octave=False
            )
            union_oct[role] = union_recall(
                ref_r, est_u, base_cfg.onset_tol_s, octave=True
            )

    for tol_ms in (50, 100, 200):
        cfg = EvalConfig(
            onset_tol_s=tol_ms / 1000.0,
            offset_ratio=base_cfg.offset_ratio,
            offset_min_tol_s=base_cfg.offset_min_tol_s,
        )
        for m, est_all in est_by_method.items():
            est_in = [n for n in est_all if t0 <= n.start_s <= t1]
            for role in DIAG_ROLES:
                ref_r = ref_notes_for_eval_role(ref_in, role)
                est_r = est_notes_for_eval_role(est_in, role)
                if not ref_r and not est_r:
                    f1_tol[m][role][float(tol_ms)] = float("nan")
                    continue
                if role == "drums":
                    d = evaluate_drums(ref_r, est_r, cfg)
                    f1_tol[m][role][float(tol_ms)] = d.get(
                        "weighted_stack_f1", float("nan")
                    )
                else:
                    f1_tol[m][role][float(tol_ms)] = evaluate_pitched_role(
                        ref_r, est_r, cfg
                    )["onset_f1"]

    for m, est_all in est_by_method.items():
        est_in = [n for n in est_all if t0 <= n.start_s <= t1]
        for role in ("melody", "bass", "other"):
            ref_r = ref_notes_for_eval_role(ref_in, role)
            est_r = est_notes_for_eval_role(est_in, role)
            chroma[m][role] = pitch_set_jaccard_at_second(ref_r, est_r, t0, t1)
            breakdown[m][role] = est_note_breakdown(
                ref_r, est_r, base_cfg.onset_tol_s
            )

    return SongDiagnosis(
        song=song,
        union_recall_exact=union_exact,
        union_recall_octave=union_oct,
        f1_by_tol=f1_tol,
        chroma_jaccard=chroma,
        breakdown=breakdown,
    )
