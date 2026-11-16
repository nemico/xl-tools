"""10曲 GT ドラムの実態調査（P1-12）。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from a2m.evaluate import EvalConfig, EvalNote, stack_drum_hits
from a2m.sc_map import resolve_drum_name

P1_6_PITCHES = (3, 9, 13, 97, 118)


@dataclass
class DrumSurveyAggregate:
    class_note_counts: Counter[str] = field(default_factory=Counter)
    class_stack_counts: Counter[str] = field(default_factory=Counter)
    stack_overlap: dict[str, float] = field(default_factory=dict)
    pitch_top20: list[tuple[int, int, str, str]] = field(default_factory=list)
    missing_map_hits: list[str] = field(default_factory=list)
    p1_6_any_role: dict[int, list[str]] = field(default_factory=dict)


def survey_eval_drums(
    song: str,
    ref_drums: list[EvalNote],
    cfg: EvalConfig,
    karaoke_notes: list,
) -> DrumSurveyAggregate:
    """warp 後の正解ドラム + 全曲 MIDI の P1-6 音高チェック。"""
    out = DrumSurveyAggregate()
    stacks = stack_drum_hits(ref_drums, cfg.drum_stack_window_s)
    for n in ref_drums:
        out.class_note_counts[n.drum_class or "other"] += 1
    for h in stacks:
        out.class_stack_counts[h.drum_class] += 1
    for cls in ("kick", "snare"):
        cls_st = [h for h in stacks if h.drum_class == cls]
        if cls_st:
            out.stack_overlap[cls] = sum(1 for h in cls_st if h.stacked_n >= 2) / len(
                cls_st
            )
    pitch_counts: Counter[int] = Counter()
    pitch_info: dict[int, tuple[str, str]] = {}
    for n in ref_drums:
        pitch_counts[n.pitch] += 1
    for n in ref_drums:
        if n.pitch not in pitch_info:
            pitch_info[n.pitch] = ("", n.drum_class or "other")
    out.pitch_top20 = [
        (p, pitch_counts[p], pitch_info[p][0], pitch_info[p][1])
        for p, _ in pitch_counts.most_common(20)
    ]
    for note in karaoke_notes:
        if note.pitch not in P1_6_PITCHES:
            continue
        out.p1_6_any_role.setdefault(note.pitch, []).append(
            f"{song}\t{note.track_name}\tch{note.channel}\trole={note.role}\t"
            f"lsb={note.bank_lsb} pc={note.program}"
        )
    for n in karaoke_notes:
        if n.role != "drums":
            continue
        if resolve_drum_name(n.bank_lsb, n.program, n.pitch) == "unknown":
            out.missing_map_hits.append(
                f"{song}\t{n.track_name}\tch{n.channel}\t"
                f"lsb={n.bank_lsb} pc={n.program} note={n.pitch}"
            )
    return out


def survey_song_drums_from_karaoke(
    song: str,
    karaoke_notes: list,
    cfg: EvalConfig,
    *,
    t0: float | None = None,
    t1: float | None = None,
) -> DrumSurveyAggregate:
    out = DrumSurveyAggregate()
    drums = [n for n in karaoke_notes if n.role == "drums"]
    if t0 is not None and t1 is not None:
        drums = [n for n in drums if t0 <= n.start_s <= t1]

    eval_drums = [
        EvalNote(
            role="drums",
            pitch=n.pitch,
            start_s=n.start_s,
            end_s=n.end_s,
            drum_class=n.drum_class,
        )
        for n in drums
    ]
    stacks = stack_drum_hits(eval_drums, cfg.drum_stack_window_s)
    for n in drums:
        out.class_note_counts[n.drum_class or "other"] += 1
    for h in stacks:
        out.class_stack_counts[h.drum_class] += 1
    for cls in ("kick", "snare"):
        cls_st = [h for h in stacks if h.drum_class == cls]
        if cls_st:
            out.stack_overlap[cls] = sum(1 for h in cls_st if h.stacked_n >= 2) / len(
                cls_st
            )

    pitch_counts: Counter[int] = Counter()
    pitch_info: dict[int, tuple[str, str]] = {}
    for n in drums:
        pitch_counts[n.pitch] += 1
        name = resolve_drum_name(n.bank_lsb, n.program, n.pitch)
        pitch_info[n.pitch] = (name, n.drum_class or "other")
        if name == "unknown":
            out.missing_map_hits.append(
                f"{song}\t{n.track_name}\tch{n.channel}\t"
                f"lsb={n.bank_lsb} pc={n.program} note={n.pitch}"
            )

    out.pitch_top20 = [
        (p, pitch_counts[p], pitch_info[p][0], pitch_info[p][1])
        for p, _ in pitch_counts.most_common(20)
    ]

    for note in karaoke_notes:
        if note.pitch not in P1_6_PITCHES:
            continue
        out.p1_6_any_role.setdefault(note.pitch, []).append(
            f"{song}\t{note.track_name}\tch{note.channel}\trole={note.role}\t"
            f"lsb={note.bank_lsb} pc={note.program}"
        )
    return out


def merge_surveys(parts: list[DrumSurveyAggregate]) -> DrumSurveyAggregate:
    merged = DrumSurveyAggregate()
    kick_ratios: list[float] = []
    snare_ratios: list[float] = []
    pitch_total: Counter[int] = Counter()
    pitch_name: dict[int, str] = {}
    pitch_cls: dict[int, str] = {}
    for p in parts:
        merged.class_note_counts.update(p.class_note_counts)
        merged.class_stack_counts.update(p.class_stack_counts)
        merged.missing_map_hits.extend(p.missing_map_hits)
        for pitch, cnt, name, cls in p.pitch_top20:
            pitch_total[pitch] += cnt
            if name:
                pitch_name[pitch] = name
            pitch_cls[pitch] = cls
        if "kick" in p.stack_overlap and p.stack_overlap["kick"] == p.stack_overlap["kick"]:
            kick_ratios.append(float(p.stack_overlap["kick"]))
        if "snare" in p.stack_overlap and p.stack_overlap["snare"] == p.stack_overlap["snare"]:
            snare_ratios.append(float(p.stack_overlap["snare"]))
        for k, lines in p.p1_6_any_role.items():
            merged.p1_6_any_role.setdefault(k, []).extend(lines)
    if kick_ratios:
        merged.stack_overlap["kick"] = sum(kick_ratios) / len(kick_ratios)
    if snare_ratios:
        merged.stack_overlap["snare"] = sum(snare_ratios) / len(snare_ratios)
    merged.pitch_top20 = [
        (p, pitch_total[p], pitch_name.get(p, ""), pitch_cls.get(p, ""))
        for p, _ in pitch_total.most_common(20)
    ]
    return merged


def format_drum_survey_md(merged: DrumSurveyAggregate) -> list[str]:
    lines: list[str] = []
    lines.append("### GT ドラム（10曲・評価窓内）")
    lines.append("")
    lines.append("| クラス | 正解ノート数 | まとめ後打点数 |")
    lines.append("|---|---:|---:|")
    all_cls = set(merged.class_note_counts) | set(merged.class_stack_counts)
    ordered = [c for c in ("kick", "snare") if c in all_cls]
    ordered += sorted(
        all_cls - set(ordered),
        key=lambda c: (-merged.class_note_counts.get(c, 0), c),
    )
    for cls in ordered:
        mark = f"**{cls}**" if cls in ("kick", "snare") else cls
        lines.append(
            f"| {mark} | {merged.class_note_counts.get(cls, 0)} | "
            f"{merged.class_stack_counts.get(cls, 0)} |"
        )
    lines.append("")
    lines.append(
        f"重ねの割合（まとめ後打点のうち stacked_n≥2）: "
        f"kick **{merged.stack_overlap.get('kick', float('nan')):.1%}** / "
        f"snare **{merged.stack_overlap.get('snare', float('nan')):.1%}**"
    )
    lines.append("")
    lines.append("| 音高 | 件数 | 名前 | クラス |")
    lines.append("|---:|---:|---|---|")
    for p, cnt, name, cls in merged.pitch_top20:
        lines.append(f"| {p} | {cnt} | {name} | {cls} |")
    lines.append("")
    lines.append(
        f"drums.csv に無い音高: **{len(merged.missing_map_hits)}** 件"
    )
    if merged.missing_map_hits:
        for row in merged.missing_map_hits[:10]:
            lines.append(f"- {row}")
    lines.append("")
    lines.append("音高 3・9・13・97・118（全役割・全トラック）:")
    for p in P1_6_PITCHES:
        rows = merged.p1_6_any_role.get(p, [])
        lines.append(f"- **{p}**: {len(rows)} ノート")
        for row in rows[:5]:
            lines.append(f"  - {row}")
    lines.append("")
    return lines
