"""原曲1曲から、プロジェクトに入れる物を全部作る（Reaper のマクロとスクリプトの両方から使う）。

音源分離 → 拍とテンポマップ → ドラム・ベース・歌メロの下書き → コード
時刻の決まり：
  音源の秒 + shift = プロジェクトの秒（shift = timeline.item_position_s − timeline.item_offset_s）
  MIDI の位置は tick（4分音符 = tpb）で、プロジェクトの頭が 0。
"""

from __future__ import annotations

import gc
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from a2m.chord_track import fill_notes, snap_segments
from a2m.chunk_offset import remove_chunk_offsets, transcribe_staggered
from a2m.draft_midi import DraftNote, guide_melody, make_mono, seconds_to_ticks, ticks_to_seconds, to_draft_notes
from a2m.project_layout import ProjectTimeline
from a2m.rpp_project import MidiItem, MidiNote

Progress = Callable[[int, str], None]


@dataclass
class SongBuild:
    timeline: ProjectTimeline
    audio_length_s: float = 0.0  # 原曲の長さ（秒）
    stems: list[tuple[str, Path]] = field(default_factory=list)  # (ステムの名前, ファイル)。2MIX の中に入れる順
    drafts: list[MidiItem] = field(default_factory=list)
    chord_track: MidiItem | None = None
    summary: list[str] = field(default_factory=list)


def _free_gpu() -> None:
    gc.collect()
    try:
        import torch

        torch.cuda.empty_cache()
    except Exception:
        pass


def make_timeline(mix_wav: Path, drums: np.ndarray, sr: int, y_mono: np.ndarray, beats_cache: Path | None,
                  m1: dict, rules: dict) -> tuple[ProjectTimeline, Any]:
    """拍を推定し、ドラムの立ち上がりに合わせた整数BPMのテンポマップを作る。"""
    import json

    from a2m.beat_track import track_beats
    from a2m.m1_pipeline import make_grid
    from a2m.project_layout import build_timeline, first_sound_s
    from a2m.tempo_dp import pick_onsets

    if beats_cache is not None and beats_cache.is_file():
        saved = json.loads(beats_cache.read_text(encoding="utf-8"))
        beats, downbeats = np.array(saved["beats"]), np.array(saved["downbeats"])
    else:
        beats, downbeats = track_beats(mix_wav, checkpoint=m1["beat_checkpoint"], device=m1["device"])
        if beats_cache is not None:
            beats_cache.write_text(json.dumps({"beats": beats.tolist(), "downbeats": downbeats.tolist()}), encoding="utf-8")
    onsets = pick_onsets(drums, sr, hop=m1["onset_hop"], n_fft=m1["onset_n_fft"], delta=m1["onset_delta"],
                         min_gap_s=m1["onset_min_gap_s"])
    grid = make_grid(beats, downbeats, m1, rules, onsets)
    sound = first_sound_s(y_mono, sr, m1["silence_db"], m1["silence_frame_s"])
    return build_timeline(grid, sound, m1["bar_eps_s"], m1["lead_bars"]), grid


def make_drafts(parts: dict[str, np.ndarray], part_sr: int, timeline: ProjectTimeline, m4: dict, rules: dict) -> list[MidiItem]:
    """分離音（ドラム・ベース・歌、モノラル）から、下書きの MIDI アイテムを作る。"""
    import librosa

    from a2m.m4_infer import load_model, transcribe_notes

    d = m4["draft"]
    tpb = rules["ticks_per_beat"]
    tm = load_model(m4, d["ckpt"], device=d.get("device", "cuda"))
    items = []
    for part, (track_name, channel) in d["tracks"].items():
        wav = librosa.resample(parts[part], orig_sr=part_sr, target_sr=m4["sr"]) if part_sr != m4["sr"] else parts[part]
        inst = m4["parts"][part]["instrument"]
        st = d["stagger"] if part in d["stagger"]["parts"] else {**d["stagger"], "passes": 1}
        notes = transcribe_staggered(lambda x: transcribe_notes(tm, x, m4["sr"], inst, 1, True), wav, m4["sr"], st)
        co = d["chunk_offset"]
        if co["enabled"] and part in co["parts"]:
            notes = remove_chunk_offsets(notes, audio_beat_times(timeline, len(wav) / m4["sr"], tpb), co)
        if part == "drums":
            draft = to_draft_notes(notes, timeline, d, tpb, d["drum_velocity"], d["drum_len_ticks"])
        else:
            velocity = rules["bass"]["velocity"] if part == "bass" else rules["vocal"]["velocity"]
            draft = make_mono(to_draft_notes(notes, timeline, d, tpb, velocity, None), d["min_len_ticks"])
            if part == "vocals":
                g = d["guide_melody"]
                draft = guide_melody(draft, rules["vocal"]["tick_unit"], g["legato_max_gap_ticks"], g["min_len_ticks"])
        items.append(draft_item(track_name, channel, draft, timeline, d, tpb))
    del tm
    _free_gpu()
    return items


def audio_beat_times(timeline: ProjectTimeline, length_s: float, tpb: int) -> np.ndarray:
    """プロジェクトのテンポマップの4分音符の線を、音源の秒（音源の頭が 0）で。0〜length_s を覆う。"""
    shift = timeline.item_position_s - timeline.item_offset_s
    last = float(seconds_to_ticks(timeline.points, np.array([length_s + shift]), tpb)[0])
    t = ticks_to_seconds(timeline.points, np.arange(0, int(np.ceil(last / tpb)) + 2) * tpb, tpb) - shift
    return t


def draft_item(track_name: str, channel: int, draft: list[DraftNote], timeline: ProjectTimeline, d: dict, tpb: int) -> MidiItem:
    end_tick = max((n.end_tick for n in draft), default=tpb)
    length = float(ticks_to_seconds(timeline.points, np.array([end_tick + tpb]), tpb)[0])
    notes = [MidiNote(n.start_tick, n.end_tick, n.pitch, n.velocity) for n in draft]
    return MidiItem(track_name, f"{d['item_prefix']} {track_name.split(maxsplit=1)[-1]}", channel, notes, length)


def item_notes_in_audio_seconds(item: MidiItem, timeline: ProjectTimeline, tpb: int) -> list[tuple[float, float, int]]:
    """MIDI アイテムの音を、音源の秒にする。"""
    if not item.notes:
        return []
    shift = timeline.item_position_s - timeline.item_offset_s
    ticks = np.array([[n.start_tick, n.end_tick] for n in item.notes], dtype=float)
    secs = ticks_to_seconds(timeline.points, ticks.ravel(), tpb).reshape(-1, 2) - shift
    return [(float(a), float(b), n.pitch) for (a, b), n in zip(secs, item.notes)]


def make_chords(mix: np.ndarray, vocals: np.ndarray, sr: int, timeline: ProjectTimeline,
                bass_notes: list[tuple[float, float, int]] | None, lc: dict, ann: dict, tpb: int) -> MidiItem:
    """コード（lv-chordia、分かっているベースを入れる）→ 構成音を塗りつぶした MIDI アイテム（「コード」トラック用）。

    bass_notes は音源の秒。
    """
    from a2m.lvchord import bass_track, chord_probs, decode, frame_rate, inject_bass

    n = min(len(mix), len(vocals))
    source = mix[:n] - vocals[:n] if lc["input"] == "novocal" else mix
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "chord_input.wav"
        sf.write(wav, source, sr)
        probs = chord_probs(wav)
    _free_gpu()
    if bass_notes:
        csr, hop = frame_rate()
        probs = inject_bass(probs, bass_track(probs[1].shape[0], bass_notes, hop / csr), lc["bass_weight"])
    shift = timeline.item_position_s - timeline.item_offset_s
    segments = [(a + shift, b + shift, c) for a, b, c in decode(probs, lc["dict"])]
    start_tick = int(round(seconds_to_ticks(timeline.points, np.array([timeline.bar_times_s[0]]), tpb)[0]))
    snapped = snap_segments(segments, timeline.points, tpb, ann["snap_ticks"], start_tick)
    end_tick = snapped[-1][1] if snapped else start_tick + tpb
    item = MidiItem(ann["chord_track_name"], ann["chord_track_name"], ann["chord_channel"],
                    fill_notes(snapped, ann["fill_low"], ann["fill_high"], ann["fill_velocity"]),
                    float(ticks_to_seconds(timeline.points, np.array([end_tick + tpb]), tpb)[0]))
    return item


def build_song(audio: Path, layout_name: str, stems_dir: Path, name: str, work_dir: Path, cfg: dict[str, dict],
               progress: Progress) -> SongBuild:
    """全部やる。cfg は config.yaml の m1 / m4 / separation / lvchord / annotate と、delivery_rules（rules）。"""
    import librosa

    from a2m.separate import combine_stems, run_separation, stem_layout

    m1, m4, sep, lc, ann, rules = (cfg[k] for k in ("m1", "m4", "separation", "lvchord", "annotate", "rules"))
    tpb = rules["ticks_per_beat"]
    work_dir.mkdir(parents=True, exist_ok=True)
    stems_dir.mkdir(parents=True, exist_ok=True)

    progress(3, "音源を読み込んでいます")
    y, sr = librosa.load(audio, sr=m1["project_sr"], mono=False)
    y = np.stack([y, y]) if y.ndim == 1 else y
    mix_wav = work_dir / "mix.wav"
    sf.write(mix_wav, y.T, sr)
    mono = y.mean(axis=0)

    progress(8, "音源を分離しています（BS-RoFormer）")
    stems6, ssr = run_separation(mix_wav, work_dir / "sep", sep, sep["overlap_listen"])
    out = SongBuild(timeline=None, audio_length_s=y.shape[1] / sr)  # type: ignore[arg-type]
    for stem, data in combine_stems(stems6, stem_layout(sep, layout_name)).items():
        f = stems_dir / f"{name}_{stem}.flac"
        sf.write(f, np.clip(data, -1.0, 1.0), ssr, format="FLAC", subtype="PCM_16")
        out.stems.append((stem, f))
    parts = {k: stems6[k].mean(axis=1) for k in ("drums", "bass", "vocals")}

    progress(35, "拍とテンポマップを作っています")
    timeline, grid = make_timeline(mix_wav, parts["drums"], ssr, mono, work_dir / "beats.json", m1, rules)
    out.timeline = timeline
    _free_gpu()

    progress(45, "ドラム・ベース・歌メロの下書きを作っています（MuScriptor）")
    out.drafts = make_drafts(parts, ssr, timeline, m4, rules)

    progress(85, "コードを判定しています（lv-chordia）")
    bass_item = next((i for i in out.drafts if i.track_name == m4["draft"]["tracks"]["bass"][0]), None)
    bass_notes = item_notes_in_audio_seconds(bass_item, timeline, tpb) if bass_item else None
    vocals = librosa.resample(parts["vocals"], orig_sr=ssr, target_sr=sr) if ssr != sr else parts["vocals"]
    out.chord_track = make_chords(mono, vocals, sr, timeline, bass_notes, lc, ann, tpb)

    changes = len(grid.segments) - 1
    out.summary = [
        f"テンポ {timeline.master_bpm}（変更 {changes} 回）、曲の頭は M{2 + m1['lead_bars']}",
        f"ステム {len(out.stems)} 本（{', '.join(s for s, _ in out.stems)}）",
        "下書き " + "、".join(f"{i.track_name.split(maxsplit=1)[-1]} {len(i.notes)}" for i in out.drafts),
        f"コード {len({(n.start_tick, n.end_tick) for n in out.chord_track.notes})} 区間",
    ]
    progress(98, "仕上げています")
    return out
