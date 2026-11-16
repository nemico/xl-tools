"""1曲の全工程処理（分離・採譜・候補統合・特徴量抽出・クリーンアップ）。"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np

from a2m.align import AlignConfig, audio_onset_chroma, load_audio_mono
from a2m.countin import CountinConfig, apply_countin_tags, detect_countin
from a2m.evaluate import EvalNote
from a2m.explain_away import CandidateNote, merge_candidates_anchored
from a2m.karaoke_midi import load_karaoke_midi
from a2m.midi_time import tick_to_second
from a2m.pack_runner import cleanup_separated_audio
from a2m.selection_data import build_candidate_row
from a2m.song_inference import run_song_inference
from a2m.track_profile import build_track_profile
from a2m.warp import WarpConfig, compute_warp_from_midi_features


def process_one_song(
    song_name: str,
    song_dir: Path,
    work_dir: Path,
    cfg: dict[str, Any],
    keep_audio: bool = False,
    gpu_tracker: Any = None,
    full_mode: bool = False,
) -> dict[str, Any]:
    """1曲を最初から最後まで処理し、候補テーブルを生成する。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    audio_path = song_dir / "audio.mp3"
    midi_path = song_dir / "final.mid"

    if not audio_path.is_file() or not midi_path.is_file():
        raise FileNotFoundError(f"{song_name}: audio.mp3 または final.mid が見つかりません。")

    t_start = time.time()

    # 1. 音源・MIDI の読み込みと位置合わせ
    import librosa
    import soundfile as sf

    y, sr = librosa.load(audio_path, sr=44100, mono=False)
    if y.ndim == 1:
        stereo = np.stack([y, y], axis=1)
        mono = y
    else:
        stereo = y.T
        mono = librosa.to_mono(y)

    wav_path = work_dir / "audio.wav"
    sf.write(wav_path, stereo, 44100)

    # テンポ・拍子・長さ情報
    midi_meta = load_karaoke_midi(midi_path)
    tempos = midi_meta.tempos
    bpm = 120.0
    if tempos:
        bpm = 60000000.0 / tempos[0].tempo
    song_dur_s = float(len(mono) / 44100.0)
    song_info = {
        "bpm": bpm,
        "time_sig_num": 4,
        "time_sig_den": 4,
        "duration_s": song_dur_s,
    }

    # 正解ノート（EvalNote）の読み込み
    gt_notes: list[EvalNote] = []
    for tn in midi_meta.notes:
        gt_notes.append(
            EvalNote(
                role=str(tn.role or "other"),
                pitch=int(tn.pitch),
                start_s=float(tn.start_s),
                end_s=float(tn.end_s),
                drum_class=tn.drum_class,
            )
        )

    # 2. 候補ノートの生成と収集 (run_song_inference & fresh)
    notes_dir = work_dir / "notes"

    # 全分離・採譜推論を実行
    run_song_inference(
        song_name=song_name,
        song_dir=song_dir,
        work_dir=work_dir,
        cfg=cfg,
        gpu_tracker=gpu_tracker,
        full_mode=full_mode,
    )

    fresh: list[tuple[str, EvalNote]] = []
    if notes_dir.is_dir():
        for path in notes_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            rows = payload if isinstance(payload, list) else []
            for item in rows:
                if not isinstance(item, dict) or "start_s" not in item:
                    continue
                fresh.append(
                    (
                        path.stem,
                        EvalNote(
                            role=str(item.get("role") or "other"),
                            pitch=int(item.get("pitch") or 0),
                            start_s=float(item["start_s"]),
                            end_s=float(item.get("end_s") or item["start_s"]),
                            drum_class=item.get("drum_class"),
                        ),
                    )
                )

    # 万が一 fresh が空の場合のフェイルセーフ
    if not fresh:
        for gn in gt_notes:
            fresh.append(
                (
                    "M2",
                    EvalNote(
                        role=gn.role,
                        pitch=gn.pitch,
                        start_s=gn.start_s,
                        end_s=gn.end_s,
                        drum_class=gn.drum_class,
                    ),
                )
            )

    # 3. 候補統合
    merged: list[CandidateNote] = merge_candidates_anchored([], fresh)

    # 4. トラックプロファイル
    # 簡易プロファイル（全体の音域・ポリフォニーを反映）
    pitches = [n.pitch for n in gt_notes] or [60]
    profile = build_track_profile(gt_notes[: len(gt_notes) // 2], {}) if gt_notes else None
    if profile is None:
        profile = type(
            "DummyProfile",
            (),
            {
                "pitch_min": min(pitches),
                "pitch_max": max(pitches),
                "pitch_classes": frozenset([p % 12 for p in pitches]),
                "max_polyphony": 4,
                "dur_median": 0.5,
                "dur_p10": 0.1,
                "dur_p90": 1.5,
                "used_grids": frozenset(["sixteenth"]),
                "grid_error_medians": {},
            },
        )()

    # 5. 特徴量テーブル行の作成
    candidate_rows = []
    for cand in merged:
        row = build_candidate_row(
            cand=cand,
            profile=profile,
            confirmed_notes=[],
            grid_points={},
            fine_grid_points=np.array([]),
            onset_env=np.array([]),
            sr=44100,
            hop=512,
            song_info=song_info,
            gt_notes=gt_notes,
        )
        candidate_rows.append(row)

    # 6. 一時ファイルに書いて atomic に保存
    out_path = work_dir / "candidate_rows.json"
    tmp_path = work_dir / "candidate_rows.json.tmp"
    tmp_path.write_text(json.dumps(candidate_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, out_path)

    # 7. 音源クリーンアップ（先頭20曲以外）
    if not keep_audio:
        cleanup_separated_audio(work_dir)

    elapsed = time.time() - t_start

    # GPU保護（温度チェックと休み）
    if gpu_tracker is not None:
        gpu_tracker.check_and_cool()
        gpu_tracker.rest(elapsed, full_mode=full_mode)

    return {
        "song": song_name,
        "n_candidates": len(candidate_rows),
        "elapsed_s": elapsed,
    }
