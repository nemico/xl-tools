"""gt_render.wav / check_LR.wav の生成（仕様 第6.1節）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from a2m.evaluate import EvalNote

KICK_PITCHES = frozenset({35, 36})


def _dbfs_to_amp(dbfs: float) -> float:
    return 10.0 ** (dbfs / 20.0)


def _render_pitched_note(
    out: np.ndarray,
    sr: int,
    start_s: float,
    end_s: float,
    pitch: int,
    amp: float,
) -> None:
    attack_s = 0.005
    release_s = 0.030
    freq = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
    t0 = max(0, int(start_s * sr))
    t1 = min(len(out), int((end_s + release_s) * sr))
    if t1 <= t0:
        return
    t = np.arange(t0, t1, dtype=np.float64) / sr
    rel = t - start_s
    env = np.ones_like(t)
    atk = int(attack_s * sr)
    if atk > 0:
        env[: min(atk, env.size)] = np.linspace(0.0, 1.0, min(atk, env.size))
    rel_end = end_s - start_s
    rel_idx = int(rel_end * sr)
    if rel_idx < env.size:
        tail = env.size - rel_idx
        if tail > 0:
            env[rel_idx:] = np.linspace(1.0, 0.0, tail)
    wave = amp * env * np.sin(2.0 * np.pi * freq * t)
    out[t0:t1] += wave


def _render_drum_hit(out: np.ndarray, sr: int, start_s: float, pitch: int, amp: float) -> None:
    if pitch in KICK_PITCHES:
        dur = 0.080
        t0 = max(0, int(start_s * sr))
        t1 = min(len(out), int((start_s + dur) * sr))
        if t1 <= t0:
            return
        t = np.arange(t0, t1, dtype=np.float64) / sr
        rel = t - start_s
        env = np.exp(-rel / 0.025)
        wave = amp * env * np.sin(2.0 * np.pi * 60.0 * t)
        out[t0:t1] += wave
    else:
        dur = 0.040
        t0 = max(0, int(start_s * sr))
        t1 = min(len(out), int((start_s + dur) * sr))
        if t1 <= t0:
            return
        n = t1 - t0
        rng = np.random.default_rng(int(start_s * 1e6) % (2**32))
        env = np.exp(-np.arange(n) / (0.012 * sr))
        wave = amp * env * rng.standard_normal(n)
        out[t0:t1] += wave


def render_gt_wav(
    notes: list[EvalNote],
    duration_s: float,
    sr: int,
    *,
    pitched_dbfs: float,
    drum_dbfs: float,
    peak_dbfs: float,
) -> np.ndarray:
    n = max(1, int(round(duration_s * sr)))
    out = np.zeros(n, dtype=np.float64)
    pitched_amp = _dbfs_to_amp(pitched_dbfs)
    drum_amp = _dbfs_to_amp(drum_dbfs)
    for note in notes:
        if note.role in ("melody", "melody2", "bass", "other"):
            _render_pitched_note(out, sr, note.start_s, note.end_s, note.pitch, pitched_amp)
        elif note.role == "drums":
            _render_drum_hit(out, sr, note.start_s, note.pitch, drum_amp)
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    limit = _dbfs_to_amp(peak_dbfs)
    if peak > limit:
        out *= limit / peak
    return out


def write_gt_render_wav(
    path: Path | str,
    notes: list[EvalNote],
    duration_s: float,
    sr: int,
    render_cfg: dict,
) -> np.ndarray:
    y = render_gt_wav(
        notes,
        duration_s,
        sr,
        pitched_dbfs=float(render_cfg["pitched_dbfs"]),
        drum_dbfs=float(render_cfg["drum_dbfs"]),
        peak_dbfs=float(render_cfg["peak_dbfs"]),
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, y, sr, subtype="PCM_16")
    return y


def write_check_lr_wav(
    path: Path | str,
    mp3_mono: np.ndarray,
    gt_render: np.ndarray,
    sr: int,
) -> None:
    n = min(mp3_mono.size, gt_render.size)
    left = mp3_mono[:n]
    right = gt_render[:n]
    stereo = np.column_stack([left, right])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, stereo, sr, subtype="PCM_16")
