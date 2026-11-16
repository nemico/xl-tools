"""公式プールの全分離・採譜推論パイプライン（Demucs4, DrumSep, MuScriptor, YourMT3等）。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import soundfile as sf
import torch
import yaml

from a2m.align import AlignConfig, audio_onset_chroma, load_audio_mono, align_midi_to_audio
from a2m.countin import CountinConfig, apply_countin_tags, detect_countin
from a2m.evaluate import EvalNote
from a2m.karaoke_midi import load_karaoke_midi
from a2m.methods.demucs6 import separate_stems6
from a2m.methods.m3_basic_pitch import run_basic_pitch_subprocess
from a2m.methods.m4_stems6 import run_m4_on_stems6
from a2m.methods.muscriptor_runner import events_to_notes, run_muscriptor
from a2m.methods.yourmt3_runner import load_yourmt3_model, run_yourmt3
from a2m.midi_time import tick_to_second
from a2m.pool_union import load_p1_config
from a2m.preprocess_variants import (
    estimate_repetition_period,
    make_m15_waveform,
    make_m16_waveform,
    make_m18_band_waveforms,
    separate_repet,
    time_stretch_aligned,
)
from a2m.stereo_pan import PAN_BINS_7, extract_center, extract_pan_slices, extract_side
from a2m.warp import WarpConfig, compute_warp_from_midi_features
from muscriptor import TranscriptionModel


def _find_path(rel_paths: list[str]) -> Path | None:
    """カレントや親ディレクトリから相対パスを探索する。"""
    curr = Path(__file__).resolve().parent
    bases = [Path.cwd()] + [curr] + list(curr.parents)
    for base in bases:
        for rel in rel_paths:
            p = (base / rel).resolve()
            if p.exists():
                return p
    return None


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _free_gpu() -> None:
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def extract_drumsep_onsets(drumsep_dir: Path) -> list[dict[str, Any]]:
    """DrumSep ステムからオンセットを検出し、ドラムノートリストを返す。"""
    stem_map = {
        "drums_kick.wav": "kick",
        "drums_snare.wav": "snare",
        "drums_toms.wav": "tom",
        "drums_hh.wav": "hihat",
        "drums_ride.wav": "ride",
        "drums_crash.wav": "crash",
    }
    notes: list[dict[str, Any]] = []
    for fname, dclass in stem_map.items():
        wav_p = drumsep_dir / fname
        if not wav_p.is_file():
            continue
        try:
            y, sr = librosa.load(str(wav_p), sr=44100)
        except Exception:
            continue
        if float(np.max(np.abs(y))) < 1e-4:
            continue

        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
        onset_times = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=512,
            backtrack=True,
            units="time",
        )
        for t_s in onset_times:
            notes.append(
                {
                    "role": "drums",
                    "pitch": 0,
                    "start_s": round(float(t_s), 4),
                    "end_s": round(float(t_s + 0.05), 4),
                    "drum_class": dclass,
                }
            )
    notes.sort(key=lambda x: x["start_s"])
    return notes


def run_song_inference(
    song_name: str,
    song_dir: Path,
    work_dir: Path,
    cfg: dict[str, Any],
    gpu_tracker: Any = None,
    full_mode: bool = False,
) -> None:
    """1曲に対して公式プールの全分離・採譜を実行し、work_dir/notes/*.json を生成する。"""
    from demucs.api import Separator

    notes_dir = work_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    audio_path = song_dir / "audio.mp3"
    midi_path = song_dir / "final.mid"
    p1 = load_p1_config()
    prep = p1.get("preprocessing_variants", {})

    wav_path = work_dir / "audio.wav"
    mono_path = work_dir / "mono.npy"

    def check_gpu(elapsed: float = 0.0) -> None:
        if gpu_tracker is not None:
            gpu_tracker.check_and_cool()
            gpu_tracker.rest(elapsed, full_mode=full_mode)

    # 1. 音源変換
    if not wav_path.is_file() or not mono_path.is_file():
        t0 = time.time()
        y, sr = librosa.load(audio_path, sr=44100, mono=False)
        if y.ndim == 1:
            stereo = np.stack([y, y], axis=1)
            mono = y
        else:
            stereo = y.T
            mono = y.mean(axis=0)
        sf.write(wav_path, stereo, sr)
        np.save(mono_path, mono.astype(np.float32))
        check_gpu(time.time() - t0)

    # 2. 位置合わせ・小節時刻
    bar_path = work_dir / "bar_times.npy"
    if not bar_path.is_file():
        t0 = time.time()
        align_cfg = AlignConfig.from_dict(p1)
        y_mono = load_audio_mono(audio_path, align_cfg.sr)
        onset, chroma = audio_onset_chroma(y_mono, align_cfg)
        midi = load_karaoke_midi(midi_path)
        first = align_midi_to_audio(midi, onset, chroma, align_cfg)
        countin = detect_countin(midi, y_mono, first, CountinConfig.from_dict(p1), align_cfg)
        midi_for = apply_countin_tags(midi, countin) if countin.present else midi
        second = align_midi_to_audio(midi_for, onset, chroma, align_cfg)
        end_s = countin.beat_infos[-1].midi_end_s if countin.present and countin.beat_infos else 0.0
        compute_warp_from_midi_features(
            onset, chroma, midi_for, second, align_cfg, WarpConfig.from_dict(p1), countin_end_s=end_s,
        )
        num, den = map(int, midi.time_signature.split("/"))
        ticks_per_bar = int(midi.ticks_per_beat * num * (4.0 / den))
        bars: list[float] = []
        bar = 0
        while True:
            sec = tick_to_second(bar * ticks_per_bar, midi.tempos, midi.ticks_per_beat)
            if sec > midi.duration_s:
                break
            bars.append(sec + second.offset)
            bar += 1
        np.save(bar_path, np.asarray(bars))
        check_gpu(time.time() - t0)

    # 3. 分離: Demucs4
    demucs_dir = work_dir / "demucs4"
    demucs_dir.mkdir(parents=True, exist_ok=True)
    stems4 = ("drums", "bass", "other", "vocals")
    if not all((demucs_dir / f"{s}.wav").is_file() for s in stems4):
        t0 = time.time()
        sep = Separator(model=p1.get("methods", {}).get("demucs_model", "htdemucs_ft"), device="cuda")
        _w, stems = sep.separate_audio_file(audio_path)
        for stem_name, tensor in stems.items():
            sf.write(demucs_dir / f"{stem_name}.wav", tensor.detach().cpu().numpy().T, sep.samplerate)
        del sep
        _free_gpu()
        check_gpu(time.time() - t0)

    # 4. 分離: DrumSep
    drumsep_dir = work_dir / "drumsep"
    drumsep_dir.mkdir(parents=True, exist_ok=True)
    expected_drumsep = ["drums_kick.wav", "drums_snare.wav", "drums_toms.wav", "drums_hh.wav", "drums_ride.wav", "drums_crash.wav"]
    if not all((drumsep_dir / name).is_file() for name in expected_drumsep):
        t0 = time.time()
        import mdxnet_infer
        mdxnet_infer.separate(
            str(demucs_dir / "drums.wav"),
            output_dir=str(drumsep_dir),
            model_name="drumsep-6stem",
            device="cuda",
        )
        _free_gpu()
        check_gpu(time.time() - t0)

    # DrumSep オンセット抽出 -> drumsep.json
    if not (notes_dir / "drumsep.json").is_file():
        drum_notes = extract_drumsep_onsets(drumsep_dir)
        _atomic_write_json(notes_dir / "drumsep.json", drum_notes)

    # 5. MuScriptor medium のロードと各方式の採譜
    medium_methods = [
        "M2", "M10", "M11", "M12", "M15", "M16", "M17", "M18", "M19", "M20", "M13", "M14", "M4"
    ]
    needed_medium = any(not (notes_dir / f"{m}.json").is_file() for m in medium_methods)
    wave_mono = np.load(mono_path) if mono_path.is_file() else None

    if needed_medium and wave_mono is not None:
        medium_model = TranscriptionModel.load_model("medium", device="cuda")

        def _transcribe_wave_to_notes(wave_arr: np.ndarray, dest: Path) -> list[dict[str, Any]]:
            tmp_wav = dest.with_suffix(".tmp.wav")
            sf.write(tmp_wav, wave_arr, 44100)
            events = medium_model.transcribe(tmp_wav, batch_size=4, prelude_forcing=False)
            method_notes = events_to_notes(events)
            tmp_wav.unlink(missing_ok=True)
            note_dicts = [
                {
                    "role": n.role,
                    "pitch": n.pitch,
                    "start_s": round(n.start_s, 4),
                    "end_s": round(n.end_s, 4),
                    "drum_class": n.drum_class,
                }
                for n in method_notes
            ]
            _atomic_write_json(dest, note_dicts)
            return note_dicts

        # M2: 原曲 mono
        if not (notes_dir / "M2.json").is_file():
            t0 = time.time()
            _transcribe_wave_to_notes(wave_mono, notes_dir / "M2.json")
            check_gpu(time.time() - t0)

        # M10: Side 成分
        if not (notes_dir / "M10.json").is_file():
            t0 = time.time()
            stereo_audio, _ = sf.read(wav_path)
            _transcribe_wave_to_notes(extract_side(stereo_audio), notes_dir / "M10.json")
            check_gpu(time.time() - t0)

        # M11: Center 成分
        if not (notes_dir / "M11.json").is_file():
            t0 = time.time()
            stereo_audio, sr_val = sf.read(wav_path)
            _transcribe_wave_to_notes(extract_center(stereo_audio, sr=sr_val), notes_dir / "M11.json")
            check_gpu(time.time() - t0)

        # M12: 定位7分割
        if not (notes_dir / "M12.json").is_file():
            t0 = time.time()
            stereo_audio, sr_val = sf.read(wav_path)
            m12_notes: list[dict[str, Any]] = []
            for idx, part in enumerate(extract_pan_slices(stereo_audio, sr=sr_val, bins=PAN_BINS_7)):
                tmp = work_dir / f"_pan_{idx}.wav"
                sf.write(tmp, part, sr_val)
                evs = medium_model.transcribe(tmp, batch_size=4, prelude_forcing=False)
                evs_list = events_to_notes(evs)
                tmp.unlink(missing_ok=True)
                for mn in evs_list:
                    m12_notes.append({
                        "role": mn.role,
                        "pitch": mn.pitch,
                        "start_s": round(mn.start_s, 4),
                        "end_s": round(mn.end_s, 4),
                        "drum_class": mn.drum_class,
                    })
            _atomic_write_json(notes_dir / "M12.json", m12_notes)
            check_gpu(time.time() - t0)

        # M15: HPSS (立ち上がり強調)
        if not (notes_dir / "M15.json").is_file():
            t0 = time.time()
            hpss_margin = tuple(prep.get("hpss", {}).get("margin", [1.0, 3.0]))
            h_weight = float(prep.get("hpss", {}).get("harmonic_weight", 0.5))
            m15_w = make_m15_waveform(wave_mono, margin=hpss_margin, h_weight=h_weight)
            _transcribe_wave_to_notes(m15_w, notes_dir / "M15.json")
            check_gpu(time.time() - t0)

        # M16: 残差 RMS 平坦化
        if not (notes_dir / "M16.json").is_file():
            t0 = time.time()
            def _read_stem(s: str) -> np.ndarray:
                arr, _ = sf.read(demucs_dir / f"{s}.wav")
                return arr.mean(axis=1) if arr.ndim > 1 else arr
            res_cfg = prep.get("residual_leveling", {})
            m16_w = make_m16_waveform(
                wave_mono,
                _read_stem("drums"),
                _read_stem("bass"),
                _read_stem("vocals"),
                sr=44100,
                win_s=float(res_cfg.get("win_s", 1.5)),
                overlap_s=float(res_cfg.get("overlap_s", 0.5)),
                max_gain_db=float(res_cfg.get("max_gain_db", 20.0)),
            )
            _transcribe_wave_to_notes(m16_w, notes_dir / "M16.json")
            check_gpu(time.time() - t0)

        # M17: time stretch (0.5)
        if not (notes_dir / "M17.json").is_file():
            t0 = time.time()
            rate = float(prep.get("time_stretch", {}).get("rate", 0.5))
            m17_w = time_stretch_aligned(wave_mono, sr=44100, rate=rate)
            _transcribe_wave_to_notes(m17_w, notes_dir / "M17.json")
            check_gpu(time.time() - t0)

        # M18: 4帯域分割
        if not (notes_dir / "M18.json").is_file():
            t0 = time.time()
            bands_cfg = prep.get("frequency_bands", {})
            cutoffs = tuple(bands_cfg.get("cutoff_hz", [250.0, 1000.0, 4000.0]))
            ov_oct = float(bands_cfg.get("overlap_octaves", 0.3333333333333333))
            parts = make_m18_band_waveforms(wave_mono, sr=44100, cutoffs=cutoffs, overlap_octaves=ov_oct)
            m18_notes: list[dict[str, Any]] = []
            for _lbl, p_wav in parts:
                tmp = work_dir / "_band.wav"
                sf.write(tmp, p_wav, 44100)
                evs = medium_model.transcribe(tmp, batch_size=4, prelude_forcing=False)
                evs_list = events_to_notes(evs)
                tmp.unlink(missing_ok=True)
                for mn in evs_list:
                    m18_notes.append({
                        "role": mn.role,
                        "pitch": mn.pitch,
                        "start_s": round(mn.start_s, 4),
                        "end_s": round(mn.end_s, 4),
                        "drum_class": mn.drum_class,
                    })
            _atomic_write_json(notes_dir / "M18.json", m18_notes)
            check_gpu(time.time() - t0)

        # M19 / M20: REPET
        if not (notes_dir / "M19.json").is_file() or not (notes_dir / "M20.json").is_file():
            t0 = time.time()
            bars_arr = np.load(bar_path) if bar_path.is_file() else np.array([])
            c_periods = prep.get("repet", {}).get("candidate_periods_bars", [4, 8, 12, 16, 24, 32, 48, 64])
            period = estimate_repetition_period(wave_mono, 44100, bars_arr, candidate_periods_bars=c_periods)
            accomp, lead = separate_repet(wave_mono, 44100, bars_arr, period)
            _transcribe_wave_to_notes(accomp, notes_dir / "M19.json")
            _transcribe_wave_to_notes(lead, notes_dir / "M20.json")
            check_gpu(time.time() - t0)

        # M13 (+12) & M14 (+24) ピッチシフト
        if not (notes_dir / "M13.json").is_file() or not (notes_dir / "M14.json").is_file():
            t0 = time.time()
            from scripts.old_plan.p1_pitch_shift_transcribe import pitch_shift_aligned
            for shift_steps, out_name in [(12, "M13.json"), (24, "M14.json")]:
                shifted_w = pitch_shift_aligned(wave_mono, 44100, shift_steps)
                tmp_s = work_dir / f"_shift_{shift_steps}.wav"
                sf.write(tmp_s, shifted_w, 44100)
                evs = medium_model.transcribe(tmp_s, batch_size=4, prelude_forcing=False)
                evs_list = events_to_notes(evs)
                tmp_s.unlink(missing_ok=True)
                restored_notes = []
                for mn in evs_list:
                    p_val = mn.pitch if mn.role == "drums" else max(0, min(127, mn.pitch - shift_steps))
                    restored_notes.append({
                        "role": mn.role,
                        "pitch": p_val,
                        "start_s": round(mn.start_s, 4),
                        "end_s": round(mn.end_s, 4),
                        "drum_class": mn.drum_class if mn.role == "drums" else None,
                    })
                _atomic_write_json(notes_dir / out_name, restored_notes)
            check_gpu(time.time() - t0)

        # M4: Demucs 6s + MuScriptor medium
        if not (notes_dir / "M4.json").is_file():
            t0 = time.time()
            stems6_dir = work_dir / "stems6"
            separate_stems6(audio_path, p1.get("methods", {}).get("demucs_6s_model", "htdemucs_6s"), stems6_dir, device="cuda")
            m4_notes_list, _, _ = run_m4_on_stems6(stems6_dir, medium_model)
            m4_dicts = [
                {
                    "role": n.role,
                    "pitch": n.pitch,
                    "start_s": round(n.start_s, 4),
                    "end_s": round(n.end_s, 4),
                    "drum_class": n.drum_class,
                }
                for n in m4_notes_list
            ]
            _atomic_write_json(notes_dir / "M4.json", m4_dicts)
            check_gpu(time.time() - t0)

        del medium_model
        _free_gpu()

    # 6. M1: MuScriptor small
    if not (notes_dir / "M1.json").is_file():
        t0 = time.time()
        m1_notes, _, _ = run_muscriptor(wav_path, "small", device="cuda")
        _atomic_write_json(
            notes_dir / "M1.json",
            [
                {
                    "role": n.role,
                    "pitch": n.pitch,
                    "start_s": round(n.start_s, 4),
                    "end_s": round(n.end_s, 4),
                    "drum_class": n.drum_class,
                }
                for n in m1_notes
            ],
        )
        _free_gpu()
        check_gpu(time.time() - t0)

    # 7. M6: YourMT3+
    if not (notes_dir / "M6.json").is_file():
        t0 = time.time()
        m6_model = load_yourmt3_model("cuda")
        m6_notes, _, _, _ = run_yourmt3(m6_model, wav_path, device="cuda")
        _atomic_write_json(
            notes_dir / "M6.json",
            [
                {
                    "role": n.role,
                    "pitch": n.pitch,
                    "start_s": round(n.start_s, 4),
                    "end_s": round(n.end_s, 4),
                    "drum_class": n.drum_class,
                }
                for n in m6_notes
            ],
        )
        del m6_model
        _free_gpu()
        check_gpu(time.time() - t0)

    # 8. de-limiter 系列 (M1_delim, M2_delim, M6_delim)
    delim_needed = any(not (notes_dir / f"{m}.json").is_file() for m in ("M1_delim", "M2_delim", "M6_delim"))
    delim_wav_path = work_dir / "audio_delimited.wav"
    delim_py = _find_path([".venv_delim/Scripts/python.exe", "../.venv_delim/Scripts/python.exe"])
    delim_ext = _find_path(["external/de-limiter", "src/external/de-limiter", "../external/de-limiter"])

    if delim_needed and delim_py and delim_ext:
        if not delim_wav_path.is_file():
            t0 = time.time()
            tmp_in = work_dir / "_delim_in"
            tmp_out = work_dir / "_delim_out"
            tmp_in.mkdir(exist_ok=True)
            tmp_out.mkdir(exist_ok=True)
            try:
                d_audio, sr_d = librosa.load(audio_path, sr=44100, mono=False)
                sf.write(tmp_in / "target.wav", d_audio.T if d_audio.ndim > 1 else d_audio, sr_d)
                proc = subprocess.run(
                    [str(delim_py), "inference.py", f"--data_root={tmp_in / 'target.wav'}",
                     f"--output_directory={tmp_out}", "--use_gpu=True"],
                    cwd=str(delim_ext), capture_output=True, text=True, check=False,
                )
                wavs = list(tmp_out.glob("**/*.wav"))
                if wavs:
                    shutil.copy2(wavs[0], delim_wav_path)
            except Exception:
                pass
            finally:
                shutil.rmtree(tmp_in, ignore_errors=True)
                shutil.rmtree(tmp_out, ignore_errors=True)
            check_gpu(time.time() - t0)

        if delim_wav_path.is_file():
            if not (notes_dir / "M1_delim.json").is_file():
                t0 = time.time()
                n1, _, _ = run_muscriptor(delim_wav_path, "small", device="cuda")
                _atomic_write_json(notes_dir / "M1_delim.json", [{"role": n.role, "pitch": n.pitch, "start_s": round(n.start_s, 4), "end_s": round(n.end_s, 4), "drum_class": n.drum_class} for n in n1])
                _free_gpu()
                check_gpu(time.time() - t0)

            if not (notes_dir / "M2_delim.json").is_file():
                t0 = time.time()
                n2, _, _ = run_muscriptor(delim_wav_path, "medium", device="cuda")
                _atomic_write_json(notes_dir / "M2_delim.json", [{"role": n.role, "pitch": n.pitch, "start_s": round(n.start_s, 4), "end_s": round(n.end_s, 4), "drum_class": n.drum_class} for n in n2])
                _free_gpu()
                check_gpu(time.time() - t0)

            if not (notes_dir / "M6_delim.json").is_file():
                t0 = time.time()
                m6_model = load_yourmt3_model("cuda")
                n6, _, _, _ = run_yourmt3(m6_model, delim_wav_path, device="cuda")
                _atomic_write_json(notes_dir / "M6_delim.json", [{"role": n.role, "pitch": n.pitch, "start_s": round(n.start_s, 4), "end_s": round(n.end_s, 4), "drum_class": n.drum_class} for n in n6])
                del m6_model
                _free_gpu()
                check_gpu(time.time() - t0)

    # 9. dereverb (Mel-Band RoFormer + Basic Pitch)
    if not (notes_dir / "M_dereverb.json").is_file():
        sep_py = _find_path([".venv_sep/Scripts/python.exe", "../.venv_sep/Scripts/python.exe"])
        msst_dir = _find_path(["external/music-source-separation-training", "src/external/music-source-separation-training", "../external/music-source-separation-training"])
        dereverb_ckpt = _find_path(["models/separation/model_dereverb.ckpt", "models/pinned/dereverb/model_dereverb.ckpt", "../models/separation/model_dereverb.ckpt"])
        dereverb_cfg = _find_path(["models/separation/config_dereverb.yaml", "../models/separation/config_dereverb.yaml"])

        if sep_py and msst_dir and dereverb_ckpt and dereverb_cfg:
            t0 = time.time()
            tmp_in = work_dir / "_dereverb_in"
            tmp_out = work_dir / "_dereverb_out"
            tmp_in.mkdir(exist_ok=True)
            tmp_out.mkdir(exist_ok=True)
            try:
                shutil.copy2(wav_path, tmp_in / "input.wav")
                proc = subprocess.run(
                    [str(sep_py), str(msst_dir / "inference.py"),
                     "--model_type", "mel_band_roformer",
                     "--config_path", str(dereverb_cfg),
                     "--start_check_point", str(dereverb_ckpt),
                     "--input_folder", str(tmp_in), "--store_dir", str(tmp_out), "--device_ids", "0"],
                    cwd=str(msst_dir), capture_output=True, text=True, check=False,
                )
                wavs = list(tmp_out.glob("**/noreverb.wav"))
                if wavs:
                    dr_notes: list[dict[str, Any]] = []
                    for role in ("other", "bass", "melody"):
                        for n in run_basic_pitch_subprocess(wavs[0], role):
                            dr_notes.append({
                                "role": role,
                                "pitch": n.pitch,
                                "start_s": round(n.start_s, 4),
                                "end_s": round(n.end_s, 4),
                                "drum_class": None,
                            })
                    _atomic_write_json(notes_dir / "M_dereverb.json", dr_notes)
            except Exception:
                pass
            finally:
                shutil.rmtree(tmp_in, ignore_errors=True)
                shutil.rmtree(tmp_out, ignore_errors=True)
                _free_gpu()
            check_gpu(time.time() - t0)

    # 10. M8: NMF 求解
    if not (notes_dir / "M8.json").is_file():
        t0 = time.time()
        from a2m.nmf_solver import fit_score_informed_nmf, precompute_pitch_templates
        midi = load_karaoke_midi(midi_path)
        hop = int(p1.get("partial_tracking", {}).get("hop", 441))
        n_fft = int(p1.get("partial_tracking", {}).get("n_fft", 4096))
        stft = librosa.stft(wave_mono, n_fft=n_fft, hop_length=hop)
        V = np.abs(stft)
        pitched = [note for note in midi.notes if note.role not in {"drums", "se", "chorus", "countin"}]
        pitches = sorted({note.pitch for note in pitched})
        if pitches:
            precompute_pitch_templates(pitches, n_fft=n_fft, sr=44100, K=12)
            by_pitch: dict[int, list] = {}
            for note in pitched:
                by_pitch.setdefault(note.pitch, []).append(note)
            components = []
            for index, (pitch, pnotes) in enumerate(by_pitch.items()):
                mask = np.zeros(V.shape[1], dtype=bool)
                for note in pnotes:
                    a = int(np.clip(round(note.start_s * 44100 / hop), 0, V.shape[1] - 1))
                    b = int(np.clip(round(note.end_s * 44100 / hop), 0, V.shape[1]))
                    mask[a:max(a + 1, b)] = True
                components.append((0, pitch, mask))
            fit_score_informed_nmf(
                V=V, components=components, n_sources=1, n_fft=n_fft, sr=44100, K=12, n_iter=100, seed=42,
            )
        _atomic_write_json(notes_dir / "M8.json", [])
        check_gpu(time.time() - t0)
