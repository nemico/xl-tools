"""第3.0b節: gt_render と同一 MIDI の自己一致 offset。"""

from __future__ import annotations

from dataclasses import replace

from a2m.align import AlignConfig, align_midi_to_audio, audio_onset_chroma
from a2m.evaluate import EvalNote
from a2m.karaoke_midi import KaraokeMidiData
from a2m.p1_export import EXPORT_ROLES, RenderConfig, synthesize_gt_render


def eval_notes_for_gt_render(midi: KaraokeMidiData) -> list[EvalNote]:
    out: list[EvalNote] = []
    for n in midi.notes:
        if n.role not in EXPORT_ROLES:
            continue
        out.append(
            EvalNote(
                role=n.role,
                pitch=n.pitch,
                start_s=n.start_s,
                end_s=n.end_s,
                drum_class=n.drum_class,
            )
        )
    return out


def gt_render_self_offset_s(
    midi: KaraokeMidiData,
    duration_s: float,
    align_cfg: AlignConfig,
    render_cfg: RenderConfig,
    *,
    feature_bias_s: float = 0.0,
) -> float:
    """MIDI から gt_render を合成し、同じ MIDI と位置合わせした offset（秒）。"""
    cfg = (
        replace(align_cfg, feature_bias_s=feature_bias_s)
        if feature_bias_s != align_cfg.feature_bias_s
        else align_cfg
    )
    notes = eval_notes_for_gt_render(midi)
    y = synthesize_gt_render(notes, duration_s, cfg.sr, render_cfg)
    audio_on, audio_ch = audio_onset_chroma(y, cfg)
    result = align_midi_to_audio(midi, audio_on, audio_ch, cfg)
    return float(result.offset)
