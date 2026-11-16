"""段階1：トラック index 付き正解ノート。"""

from __future__ import annotations

from pathlib import Path

from a2m.align import AlignConfig, AlignResult, load_audio_mono
from a2m.countin import CountinConfig, apply_countin_tags, detect_countin
from a2m.delay_copy import delay_copy_excluded_tracks, delay_copy_from_json
from a2m.evaluate import EvalNote, GT_EXCLUDED_ROLES
from a2m.explain_away import RefTrackNote
from a2m.karaoke_midi import load_karaoke_midi
from a2m.warp import load_warp_json


def load_ref_by_track(
    song_dir: Path,
    song_out: Path,
    *,
    align: dict,
    countin_cfg: CountinConfig,
    align_cfg: AlignConfig,
    latency_s: float,
    sr: int,
) -> tuple[list[RefTrackNote], list[int]]:
    from a2m.p1_export import load_role_midi

    audio_path = song_out / "audio.wav"
    if not audio_path.is_file():
        audio_path = song_dir / "audio.mp3"
    mp3_y = load_audio_mono(audio_path, sr)
    meta = load_karaoke_midi(song_dir / "final.mid")
    cr = align.get("countin") or {}
    if cr.get("present"):
        pass1 = AlignResult(
            offset=float(cr["offset_pass1"]),
            scale=float(cr.get("scale_pass1", 1.0)),
            margin=0.0,
            peak1_offset_s=0.0,
            peak1_score=0.0,
            peak2_offset_s=None,
            peak2_score=None,
            best_offset_s_on=0.0,
            best_offset_s_ch=0.0,
            residual_median_s=None,
            residual_max_s=None,
            windows_total=0,
            windows_used=0,
            windows_stuck_edge=0,
        )
        render_wav = song_out / "render.wav"
        render_y = load_audio_mono(render_wav, sr) if render_wav.is_file() else None
        det = detect_countin(
            meta,
            mp3_y,
            pass1,
            countin_cfg,
            align_cfg,
            render_y=render_y,
            latency_s=latency_s,
        )
        meta = apply_countin_tags(meta, det)

    dc_excluded = delay_copy_excluded_tracks(
        delay_copy_from_json(align.get("delay_copy"))
    )
    warp_path = song_out / "warp.json"
    if not warp_path.is_file():
        flat = load_role_midi(song_out / "gt_aligned.mid")
        return [RefTrackNote(0, n) for n in flat], [0]

    warp = load_warp_json(warp_path)
    name_to_index = {ts.name: ts.index for ts in meta.tracks}
    out: list[RefTrackNote] = []
    for n in meta.notes:
        if n.track_name in dc_excluded:
            continue
        if n.role in GT_EXCLUDED_ROLES:
            continue
        if n.role not in ("melody", "melody2", "bass", "drums", "other"):
            continue
        idx = name_to_index.get(n.track_name, -1)
        out.append(
            RefTrackNote(
                idx,
                EvalNote(
                    role=n.role,
                    pitch=n.pitch,
                    start_s=warp.audio_at_midi(n.start_s),
                    end_s=warp.audio_at_midi(n.end_s),
                    drum_class=n.drum_class,
                ),
            )
        )
    track_indices = sorted({ts.index for ts in meta.tracks if ts.note_count > 0})
    return out, track_indices
