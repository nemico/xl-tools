"""第6.3 / 6.3b節: render.wav の品質指標。"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

CLIP_INT16 = 32767
DISCONTINUITY_THRESH = 0.2


@dataclass(frozen=True)
class RenderQualityRow:
    song: str
    peak_dbfs: float
    clip_samples: int
    clip_ratio: float
    clip_longest_run: int
    top_peak_times_s: str


@dataclass(frozen=True)
class RenderQualityDetail:
    path: Path
    peak_dbfs: float
    clip_samples: int
    discontinuity_count: int
    discontinuity_max: float
    discontinuity_max_time_s: float
    render_time_s: float | None = None


@dataclass(frozen=True)
class RenderOptSettingReport:
    setting: str
    render_time_s: float
    peak_dbfs: float
    discontinuity_count: int
    discontinuity_max: float
    runs_match: bool
    diff_vs_a_max: float
    diff_vs_a_top_times_s: str
    output_dirs: str


def load_mono_float(path: Path) -> tuple[np.ndarray, int]:
    raw, sr = sf.read(str(path), dtype="int16", always_2d=False)
    raw = np.asarray(raw)
    mono = raw if raw.ndim == 1 else raw.max(axis=1)
    y = mono.astype(np.float64) / 32768.0
    return y, int(sr)


def analyze_render_detail(path: Path, *, render_time_s: float | None = None) -> RenderQualityDetail:
    y, sr = load_mono_float(path)
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    peak_db = 20.0 * np.log10(max(peak, 1e-12))

    raw, _ = sf.read(str(path), dtype="int16", always_2d=False)
    mono_i = np.asarray(raw)
    if mono_i.ndim > 1:
        mono_i = mono_i.max(axis=1)
    clip_n = int(np.sum(np.abs(mono_i.astype(np.int32)) >= CLIP_INT16))

    disc_count, disc_max, disc_t = discontinuity_stats(y, sr)

    return RenderQualityDetail(
        path=path,
        peak_dbfs=peak_db,
        clip_samples=clip_n,
        discontinuity_count=disc_count,
        discontinuity_max=disc_max,
        discontinuity_max_time_s=disc_t,
        render_time_s=render_time_s,
    )


def discontinuity_stats(y: np.ndarray, sr: int) -> tuple[int, float, float]:
    if y.size < 2:
        return 0, 0.0, 0.0
    d = np.abs(np.diff(y))
    mask = d > DISCONTINUITY_THRESH
    count = int(np.count_nonzero(mask))
    if count == 0:
        return 0, 0.0, 0.0
    idx = int(np.argmax(d))
    return count, float(d[idx]), float(idx) / float(sr)


def wavs_sample_identical(a: Path, b: Path) -> bool:
    da, _ = sf.read(str(a), dtype="int16", always_2d=False)
    db, _ = sf.read(str(b), dtype="int16", always_2d=False)
    da = np.asarray(da)
    db = np.asarray(db)
    if da.shape != db.shape:
        return False
    return bool(np.array_equal(da, db))


def max_abs_diff_vs_reference(
    reference: Path,
    other: Path,
    *,
    top_n: int = 3,
) -> tuple[float, list[float]]:
    ya, sr = load_mono_float(reference)
    yb, sr2 = load_mono_float(other)
    if sr != sr2:
        raise ValueError("sample rate mismatch")
    n = min(ya.size, yb.size)
    if n == 0:
        return 0.0, []
    diff = np.abs(ya[:n] - yb[:n])
    mx = float(np.max(diff))
    if mx <= 0:
        return 0.0, []
    sec = max(1, int(sr))
    n_sec = int(np.ceil(n / sec))
    per_sec: list[tuple[float, float]] = []
    for i in range(n_sec):
        seg = diff[i * sec : (i + 1) * sec]
        if seg.size == 0:
            continue
        per_sec.append((float(np.max(seg)), float(i)))
    per_sec.sort(key=lambda x: x[0], reverse=True)
    times = [t for _, t in per_sec[:top_n]]
    return mx, times


def summarize_setting_runs(
    setting: str,
    song_dirs: list[Path],
    *,
    reference_run1: dict[str, Path] | None,
) -> RenderOptSettingReport:
    details: list[RenderQualityDetail] = []
    times: list[float] = []
    runs_ok = True
    diff_max = 0.0
    diff_times: list[float] = []
    paths_out: list[str] = []

    for song_dir in song_dirs:
        run1 = song_dir / "run1.wav"
        run2 = song_dir / "run2.wav"
        paths_out.append(str(song_dir.resolve()))
        if not run1.is_file() or not run2.is_file():
            runs_ok = False
            continue
        meta_path = song_dir / "render_meta.txt"
        rt = None
        if meta_path.is_file():
            for line in meta_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("render_time_s="):
                    rt = float(line.split("=", 1)[1])
        details.append(analyze_render_detail(run1, render_time_s=rt))
        if meta_path.is_file():
            for line in meta_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("render_time_s_run2="):
                    times.append(float(line.split("=", 1)[1]))
        if rt is not None:
            times.append(rt)
        if not wavs_sample_identical(run1, run2):
            runs_ok = False
        if reference_run1 is not None:
            song = song_dir.name
            if song in reference_run1:
                mx, ts = max_abs_diff_vs_reference(reference_run1[song], run1)
                diff_max = max(diff_max, mx)
                diff_times.extend(ts)

    peak = max((d.peak_dbfs for d in details), default=-999.0)
    disc_n = sum(d.discontinuity_count for d in details)
    disc_mx = max((d.discontinuity_max for d in details), default=0.0)
    t_total = sum(times) if times else 0.0
    top_diff = ",".join(f"{t:.1f}" for t in sorted(set(diff_times), reverse=True)[:3])

    return RenderOptSettingReport(
        setting=setting,
        render_time_s=t_total,
        peak_dbfs=peak,
        discontinuity_count=disc_n,
        discontinuity_max=disc_mx,
        runs_match=runs_ok,
        diff_vs_a_max=diff_max,
        diff_vs_a_top_times_s=top_diff if setting != "A" else "-",
        output_dirs="; ".join(paths_out),
    )


def write_render_opt_report_csv(rows: list[RenderOptSettingReport], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "setting",
                "render_time_s",
                "peak_dbfs",
                "discontinuity_count",
                "discontinuity_max",
                "runs_match",
                "diff_vs_a_max",
                "diff_vs_a_top_times_s",
                "output_dirs",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.setting,
                    f"{r.render_time_s:.1f}",
                    f"{r.peak_dbfs:.2f}",
                    r.discontinuity_count,
                    f"{r.discontinuity_max:.4f}",
                    "yes" if r.runs_match else "no",
                    f"{r.diff_vs_a_max:.6f}" if r.setting != "A" else "-",
                    r.diff_vs_a_top_times_s,
                    r.output_dirs,
                ]
            )


def _to_mono_float_legacy(data: np.ndarray) -> np.ndarray:
    if data.ndim == 1:
        y = data.astype(np.float64)
    else:
        y = data.mean(axis=1, dtype=np.float64)
    if y.dtype == np.int16 or np.issubdtype(y.dtype, np.integer):
        y = y.astype(np.float64) / 32768.0
    return y


def analyze_render_wav(path: Path, *, sr_hint: int | None = None) -> RenderQualityRow:
    d = analyze_render_detail(path)
    y, sr = load_mono_float(path)
    top_times = _top_second_peaks(y, sr, n=5)
    times_s = ",".join(f"{t:.1f}" for t in top_times[:3])
    raw, _ = sf.read(str(path), dtype="int16", always_2d=False)
    mono_i = np.asarray(raw)
    if mono_i.ndim > 1:
        mono_i = mono_i.max(axis=1)
    clip_mask = np.abs(mono_i.astype(np.int32)) >= CLIP_INT16
    clip_n = int(np.count_nonzero(clip_mask))
    ratio = float(clip_n / clip_mask.size) if clip_mask.size else 0.0
    longest = _longest_true_run(clip_mask)
    return RenderQualityRow(
        song=path.parent.name,
        peak_dbfs=d.peak_dbfs,
        clip_samples=clip_n,
        clip_ratio=ratio,
        clip_longest_run=longest,
        top_peak_times_s=times_s,
    )


def _longest_true_run(mask: np.ndarray) -> int:
    if mask.size == 0:
        return 0
    best = cur = 0
    for v in mask:
        if v:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _top_second_peaks(y: np.ndarray, sr: int, *, n: int) -> list[float]:
    if y.size == 0:
        return []
    sec = max(1, int(sr))
    n_sec = int(np.ceil(y.size / sec))
    peaks: list[tuple[float, float]] = []
    for i in range(n_sec):
        seg = y[i * sec : (i + 1) * sec]
        if seg.size == 0:
            continue
        peaks.append((float(np.max(np.abs(seg))), float(i)))
    peaks.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in peaks[:n]]


def write_render_quality_csv(rows: list[RenderQualityRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "song",
                "peak_dbfs",
                "clip_samples",
                "clip_ratio",
                "clip_longest_run",
                "top_peak_times_s_top3",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.song,
                    f"{r.peak_dbfs:.2f}",
                    r.clip_samples,
                    f"{r.clip_ratio:.6f}",
                    r.clip_longest_run,
                    r.top_peak_times_s,
                ]
            )


def scan_p1_render_quality(p1_dir: Path) -> list[RenderQualityRow]:
    rows: list[RenderQualityRow] = []
    for song_dir in sorted(p1_dir.iterdir()):
        if not song_dir.is_dir():
            continue
        wav = song_dir / "render.wav"
        if wav.is_file():
            rows.append(analyze_render_wav(wav))
    return rows


def build_render_opt_reports(opts_root: Path, songs: list[str]) -> list[RenderOptSettingReport]:
    from a2m.render_opt import RENDER_OPT_PRESETS

    ref: dict[str, Path] = {}
    for song in songs:
        p = opts_root / "A" / song / "run1.wav"
        if p.is_file():
            ref[song] = p

    reports: list[RenderOptSettingReport] = []
    for name in RENDER_OPT_PRESETS:
        song_dirs = [opts_root / name / song for song in songs]
        reports.append(
            summarize_setting_runs(
                name,
                song_dirs,
                reference_run1=ref if name != "A" else None,
            )
        )
    return reports
