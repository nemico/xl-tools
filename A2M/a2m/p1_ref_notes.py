"""段階1：正解 EvalNote（warp・除外適用済み）。"""

from __future__ import annotations

import json
from pathlib import Path

from a2m.align import AlignConfig, AlignResult, load_audio_mono
from a2m.countin import CountinConfig, apply_countin_tags, detect_countin
from a2m.delay_copy import delay_copy_excluded_tracks, delay_copy_from_json
from a2m.evaluate import EvalNote
from a2m.karaoke_midi import load_karaoke_midi
from a2m.warp import load_warp_json, warp_eval_notes


def load_ref_eval_notes(
    song_dir: Path,
    song_out: Path,
    *,
    align: dict,
    countin_cfg: CountinConfig,
    align_cfg: AlignConfig,
    latency_s: float,
    sr: int,
) -> list[EvalNote]:
    """final.mid → countin タグ → warp 時刻。delay_copy トラックは除外。"""
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

    warp_path = song_out / "warp.json"
    if not warp_path.is_file():
        from a2m.p1_export import load_role_midi

        return load_role_midi(song_out / "gt_aligned.mid")

    dc_excluded = delay_copy_excluded_tracks(
        delay_copy_from_json(align.get("delay_copy"))
    )
    warp = load_warp_json(warp_path)
    return warp_eval_notes(
        meta, warp, exclude_track_names=dc_excluded
    )


def load_align_json(song_out: Path) -> dict:
    return json.loads((song_out / "align.json").read_text(encoding="utf-8"))
