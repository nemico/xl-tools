"""音色ベクトル・recall@budget（P1-14 測定用）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from a2m.evaluate import (
    EvalConfig,
    EvalNote,
    _match_onsets_smallest_first,
    stack_drum_hits,
)
from a2m.explain_away import AGG_ROLES, CandidateNote, agg_role_for_track
from a2m.units import midi_to_hz

HARMONIC_COUNT = 8
ONSET_POINTS = 10
ONSET_T0_S = -0.02
ONSET_T1_S = 0.05
HARMONIC_OFFSET_S = 0.03
ANCHOR_MAX = 8
ONSET_MATCH_TOL_S = 0.05


@dataclass(frozen=True)
class TimbreFeatures:
    vector: np.ndarray


@dataclass(frozen=True)
class TrackTimbreProfile:
    track_index: int
    role: str
    agg_role: str
    profile: np.ndarray
    anchor_vectors: tuple[np.ndarray, ...]


def vector_cache_key(t: float, pitch: int) -> tuple[int, int]:
    return (int(round(t * 1000)), pitch)


@dataclass
class SongAudioFeatures:
    sr: int
    hop: int
    stft_mag: np.ndarray
    stft_freqs: np.ndarray
    onset_strength: np.ndarray
    frame_times: np.ndarray


def build_song_audio_features(y: np.ndarray, sr: int, hop: int, n_fft: int) -> SongAudioFeatures:
    import librosa

    stft = librosa.stft(y, n_fft=n_fft, hop_length=hop)
    mag = np.abs(stft)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    n = min(mag.shape[1], onset.size)
    mag = mag[:, :n]
    onset = onset[:n]
    times = librosa.frames_to_time(np.arange(n), sr=sr, hop_length=hop)
    return SongAudioFeatures(sr=sr, hop=hop, stft_mag=mag, stft_freqs=freqs, onset_strength=onset, frame_times=times)


def _time_to_frame(t: float, feat: SongAudioFeatures) -> int:
    idx = int(np.searchsorted(feat.frame_times, t, side="left"))
    return int(np.clip(idx, 0, feat.stft_mag.shape[1] - 1))


def _amp_at_freq(t: float, freq_hz: float, feat: SongAudioFeatures) -> float:
    frame = _time_to_frame(t, feat)
    if freq_hz <= 0 or freq_hz >= feat.sr / 2:
        return 0.0
    bin_i = int(np.argmin(np.abs(feat.stft_freqs - freq_hz)))
    return float(feat.stft_mag[bin_i, frame])


def timbre_vector_at(t: float, pitch: int, feat: SongAudioFeatures) -> np.ndarray:
    f0 = midi_to_hz(float(pitch))
    harm = np.zeros(HARMONIC_COUNT, dtype=float)
    t_h = t + HARMONIC_OFFSET_S
    for i in range(HARMONIC_COUNT):
        harm[i] = _amp_at_freq(t_h, f0 * (i + 1), feat)
    s = harm.sum()
    if s > 0:
        harm /= s
    onset = np.zeros(ONSET_POINTS, dtype=float)
    for i, alpha in enumerate(np.linspace(0.0, 1.0, ONSET_POINTS)):
        ts = t + ONSET_T0_S + alpha * (ONSET_T1_S - ONSET_T0_S)
        fr = _time_to_frame(ts, feat)
        onset[i] = float(feat.onset_strength[fr])
    mx = onset.max()
    if mx > 0:
        onset /= mx
    return np.concatenate([harm, onset])


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def profile_median(vectors: Sequence[np.ndarray]) -> np.ndarray:
    if not vectors:
        return np.zeros(HARMONIC_COUNT + ONSET_POINTS, dtype=float)
    return np.median(np.stack(list(vectors), axis=0), axis=0)


def build_track_timbre_profiles(
    notes_by_index: dict[int, list[EvalNote]],
    eval_tracks: list[tuple[int, str, str]],
    audio_feat: SongAudioFeatures,
) -> list[TrackTimbreProfile]:
    """P1-14 と同じ手順でトラック音色プロファイルを構築する。"""
    vec_cache: dict[tuple[int, int], np.ndarray] = {}

    def get_vec(t: float, pitch: int) -> np.ndarray:
        key = vector_cache_key(t, pitch)
        if key not in vec_cache:
            vec_cache[key] = timbre_vector_at(t, pitch, audio_feat)
        return vec_cache[key]

    out: list[TrackTimbreProfile] = []
    for ti, role, agg in eval_tracks:
        track_notes = notes_by_index[ti]
        other = [n for idx, ns in notes_by_index.items() if idx != ti for n in ns]
        anchors = select_anchors(track_notes, other)
        vecs = [get_vec(n.start_s, n.pitch) for n in anchors]
        prof = profile_median(vecs)
        out.append(
            TrackTimbreProfile(
                track_index=ti,
                role=role,
                agg_role=agg,
                profile=prof,
                anchor_vectors=tuple(vecs),
            )
        )
    return out


def pairwise_profile_stats(profiles: list[TrackTimbreProfile]) -> dict[str, float]:
    sims: list[float] = []
    for i in range(len(profiles)):
        for j in range(i + 1, len(profiles)):
            sims.append(cosine_similarity(profiles[i].profile, profiles[j].profile))
    if not sims:
        return {"n_tracks": len(profiles), "median": float("nan"), "min": float("nan"), "frac_ge_095": float("nan")}
    arr = np.array(sims, dtype=float)
    return {
        "n_tracks": len(profiles),
        "median": float(np.median(arr)),
        "min": float(arr.min()),
        "frac_ge_095": float(np.mean(arr >= 0.95)),
    }


def within_track_anchor_stats(profiles: list[TrackTimbreProfile]) -> list[tuple[str, str, float]]:
    """(agg_role, role, median cosine anchor vs profile) per track."""
    rows: list[tuple[str, str, float]] = []
    for tp in profiles:
        if not tp.anchor_vectors:
            rows.append((tp.agg_role, tp.role, float("nan")))
            continue
        sims = [cosine_similarity(v, tp.profile) for v in tp.anchor_vectors]
        rows.append((tp.agg_role, tp.role, float(np.median(sims))))
    return rows


def select_anchors(
    track_notes: list[EvalNote],
    other_notes: list[EvalNote],
    *,
    max_n: int = ANCHOR_MAX,
    tol_s: float = ONSET_MATCH_TOL_S,
) -> list[EvalNote]:
    if not track_notes:
        return []

    def overlap(n: EvalNote) -> int:
        c = 0
        for o in other_notes:
            if abs(o.start_s - n.start_s) <= tol_s:
                c += 1
        return c

    ranked = sorted(track_notes, key=lambda n: (overlap(n), n.start_s))
    return ranked[:max_n]


def pitch_in_range(pitch: int, notes: list[EvalNote]) -> bool:
    if not notes:
        return False
    lo = min(n.pitch for n in notes)
    hi = max(n.pitch for n in notes)
    return lo <= pitch <= hi


def _note_match(a: EvalNote, b: EvalNote, tol_s: float) -> bool:
    return a.pitch == b.pitch and abs(a.start_s - b.start_s) <= tol_s


def recall_at_budget(
    ref_notes: list[EvalNote],
    ranked_candidates: list[EvalNote],
    budget: int,
    track_role: str,
    cfg: EvalConfig,
) -> float:
    if not ref_notes:
        return float("nan")
    if budget <= 0:
        return 0.0
    top = ranked_candidates[:budget]
    if track_role == "drums":
        ref_st = stack_drum_hits(ref_notes, cfg.drum_stack_window_s)
        est_st = stack_drum_hits(top, cfg.drum_stack_window_s)
        if not ref_st:
            return float("nan")
        ref_t = np.array([h.start_s for h in ref_st], dtype=float)
        est_t = np.array([h.start_s for h in est_st], dtype=float)
        tp, _fp, fn = _match_onsets_smallest_first(ref_t, est_t, cfg.onset_tol_s)
        return tp / len(ref_st) if ref_st else 0.0
    hit = 0
    for r in ref_notes:
        if any(_note_match(r, c, cfg.onset_tol_s) for c in top):
            hit += 1
    return hit / len(ref_notes)


def union_recall_ceiling(
    ref_notes: list[EvalNote],
    pool: list[EvalNote],
    budget: int,
    track_role: str,
    cfg: EvalConfig,
) -> float:
    """B 個まで選べるときの recall 上限（貪欲マッチ）。"""
    if not ref_notes or budget <= 0:
        return 0.0 if ref_notes else float("nan")
    if track_role == "drums":
        ref_st = stack_drum_hits(ref_notes, cfg.drum_stack_window_s)
        if not ref_st:
            return float("nan")
        ref_t = np.array([h.start_s for h in ref_st], dtype=float)
        est_st = stack_drum_hits(pool, cfg.drum_stack_window_s)
        est_t = np.array([h.start_s for h in est_st], dtype=float)
        if est_t.size == 0:
            return 0.0
        pairs: list[tuple[float, int, int]] = []
        for i, rt in enumerate(ref_t):
            for j, et in enumerate(est_t):
                d = abs(float(rt) - float(et))
                if d <= cfg.onset_tol_s:
                    pairs.append((d, i, j))
        pairs.sort(key=lambda x: x[0])
        used_r: set[int] = set()
        used_e: set[int] = set()
        tp = 0
        for _d, i, j in pairs:
            if i in used_r or j in used_e:
                continue
            used_r.add(i)
            used_e.add(j)
            tp += 1
            if len(used_e) >= budget:
                break
        return tp / len(ref_st)

    pairs: list[tuple[float, int, int]] = []
    for i, r in enumerate(ref_notes):
        for j, c in enumerate(pool):
            if _note_match(r, c, cfg.onset_tol_s):
                pairs.append((abs(r.start_s - c.start_s), i, j))
    pairs.sort(key=lambda x: x[0])
    used_r: set[int] = set()
    used_c: set[int] = set()
    matched = 0
    for _d, i, j in pairs:
        if i in used_r or j in used_c:
            continue
        used_r.add(i)
        used_c.add(j)
        matched += 1
        if len(used_c) >= budget:
            break
    return matched / len(ref_notes)


def rank_baseline0(candidates: list[CandidateNote], rng: np.random.Generator) -> list[EvalNote]:
    def key(c: CandidateNote) -> tuple[int, float, float]:
        n_methods = len(c.methods)
        return (-n_methods, c.start_s, float(rng.random()))

    ordered = sorted(candidates, key=key)
    return [c.to_eval_note() for c in ordered]


def rank_baseline1(
    candidates: list[CandidateNote],
    ref_track: list[EvalNote],
    rng: np.random.Generator,
) -> list[EvalNote]:
    def key(c: CandidateNote) -> tuple[int, float, float]:
        inside = 1 if pitch_in_range(c.pitch, ref_track) else 0
        return (-inside, c.start_s, float(rng.random()))

    ordered = sorted(candidates, key=key)
    return [c.to_eval_note() for c in ordered]


def rank_by_scores(
    candidates: list[CandidateNote],
    scores: list[float],
) -> list[EvalNote]:
    order = sorted(range(len(candidates)), key=lambda i: (-scores[i], candidates[i].start_s))
    return [candidates[i].to_eval_note() for i in order]
