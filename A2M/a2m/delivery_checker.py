"""
Delivery Checker backend for JOYSOUND R02Checker & NTVT integration.
Handles:
1. Automatic NTVT conversion (a2m.ntvt_equiv)
2. Proper delivery filename generation (^.{2}[0-9]{5,6}W_R02.mid)
3. Running R02Checker.exe in MIDIChecker directory
4. Parsing error/warning logs into structured records
5. Converting [MMM:BB:TTT] measure/beat/tick coordinates into timeline seconds
6. Writing formatted human-readable reports to reports/delivery/
"""

import os
import re
import json
import subprocess
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional

import mido

try:
    from ntvt_equiv import transform_midi_bytes, detect_comments_from_mid
except ImportError:
    from a2m.ntvt_equiv import transform_midi_bytes, detect_comments_from_mid


@dataclass
class CheckerIssue:
    severity: str  # "ERROR" or "WARNING"
    raw_text: str
    rule_name: str
    measure: Optional[int] = None      # 1-based measure
    beat: Optional[int] = None         # 1-based beat
    tick: Optional[int] = None         # 0-based tick (0..479)
    track_num: Optional[int] = None    # 1-based track number (Tr.N)
    seconds: Optional[float] = None    # Timeline seconds


@dataclass
class DeliveryCheckResult:
    song_name: str
    delivery_filename: str
    total_errors: int
    total_warnings: int
    passed: bool
    issues: List[CheckerIssue]
    report_path: str
    log_path: str


def compute_tick_to_seconds_map(mid: mido.MidiFile) -> list[tuple[int, float, float]]:
    """
    Builds a list of (start_tick, start_seconds, seconds_per_tick) for each tempo change.
    """
    ticks_per_beat = mid.ticks_per_beat or 480
    current_tempo = 500000  # Default 120 BPM (500,000 microseconds per beat)
    
    # Collect tempo events from track 0 (or all tracks)
    tempo_events = []
    for track in mid.tracks:
        cur_tick = 0
        for msg in track:
            cur_tick += msg.time
            if msg.type == 'set_tempo':
                tempo_events.append((cur_tick, msg.tempo))
                
    tempo_events.sort(key=lambda x: x[0])
    
    segments = []
    last_tick = 0
    last_time = 0.0
    sec_per_tick = (current_tempo / 1_000_000.0) / ticks_per_beat
    
    for tick, tempo in tempo_events:
        if tick > last_tick:
            duration = (tick - last_tick) * sec_per_tick
            segments.append((last_tick, last_time, sec_per_tick))
            last_time += duration
            last_tick = tick
        current_tempo = tempo
        sec_per_tick = (current_tempo / 1_000_000.0) / ticks_per_beat
        
    segments.append((last_tick, last_time, sec_per_tick))
    return segments


def tick_to_seconds(tick: int, segments: list[tuple[int, float, float]]) -> float:
    """Converts absolute tick to seconds using tempo segments."""
    for i in range(len(segments) - 1, -1, -1):
        seg_tick, seg_time, sec_per_tick = segments[i]
        if tick >= seg_tick:
            return seg_time + (tick - seg_tick) * sec_per_tick
    return 0.0


def build_time_signature_map(mid: mido.MidiFile) -> list[tuple[int, int, int, int]]:
    """
    Returns list of (start_measure, start_tick, numerator, denominator) changes.
    """
    ticks_per_beat = mid.ticks_per_beat or 480
    ts_events = []
    
    cur_tick = 0
    for msg in mid.tracks[0]:
        cur_tick += msg.time
        if msg.type == 'time_signature':
            ts_events.append((cur_tick, msg.numerator, msg.denominator))
            
    if not ts_events or ts_events[0][0] != 0:
        ts_events.insert(0, (0, 4, 4))
        
    ts_events.sort(key=lambda x: x[0])
    
    # Map to measures
    result = []
    cur_measure = 1
    last_tick = 0
    last_num = 4
    last_den = 4
    
    for tick, num, den in ts_events:
        if tick > last_tick:
            measure_len_ticks = int(last_num * (ticks_per_beat * 4 / last_den))
            measures_passed = (tick - last_tick) // measure_len_ticks
            cur_measure += measures_passed
            last_tick = tick
        result.append((cur_measure, tick, num, den))
        last_num = num
        last_den = den
        
    return result


def measure_beat_tick_to_abs_tick(
    measure: int, beat: int, tick: int, mid: mido.MidiFile
) -> int:
    """
    Converts 1-based measure, 1-based beat, 0-based tick to absolute tick.
    """
    ticks_per_beat = mid.ticks_per_beat or 480
    ts_map = build_time_signature_map(mid)
    
    # Find matching time signature segment
    # Iterate in reverse to find the active time signature at `measure`
    active_seg = ts_map[0]
    for seg in ts_map:
        if measure >= seg[0]:
            active_seg = seg
        else:
            break
            
    seg_measure, seg_tick, num, den = active_seg
    ticks_per_measure = int(num * (ticks_per_beat * 4 / den))
    ticks_per_current_beat = int(ticks_per_beat * 4 / den)
    
    measures_diff = measure - seg_measure
    abs_tick = seg_tick + measures_diff * ticks_per_measure + (beat - 1) * ticks_per_current_beat + tick
    return abs_tick


def abs_tick_to_measure_beat_tick(abs_tick: int, mid: mido.MidiFile) -> tuple[int, int, int]:
    """
    Converts absolute tick to 1-based measure, 1-based beat, 0-based tick.
    """
    ticks_per_beat = mid.ticks_per_beat or 480
    ts_map = build_time_signature_map(mid)
    active_seg = ts_map[0]
    for seg in ts_map:
        if abs_tick >= seg[1]:
            active_seg = seg
        else:
            break
            
    seg_measure, seg_tick, num, den = active_seg
    ticks_per_measure = int(num * (ticks_per_beat * 4 / den))
    ticks_per_current_beat = int(ticks_per_beat * 4 / den)
    
    diff_ticks = abs_tick - seg_tick
    measures_passed = diff_ticks // ticks_per_measure
    rem_ticks = diff_ticks % ticks_per_measure
    beats_passed = rem_ticks // ticks_per_current_beat
    tick_in_beat = rem_ticks % ticks_per_current_beat
    
    measure = seg_measure + measures_passed
    beat = 1 + beats_passed
    tick = tick_in_beat
    return measure, beat, tick


def get_delivery_filename(midi_bytes: bytes, fallback_name: str = "VD000000") -> str:
    """
    Extracts Track 0 track name or derives 6-digit VD code to satisfy R02Checker regex:
    ^.{2}[0-9]{5,6}W_(R02|RB2|RN2)\\.mid$
    Strips any revision suffix like _41, _42 to produce clean VDxxxxxxW_R02.mid.
    """
    idx = midi_bytes.find(b"\xFF\x03")
    if 0 <= idx < 1000:
        length = midi_bytes[idx + 2]
        t0_name = midi_bytes[idx + 3 : idx + 3 + length].decode("latin-1", errors="replace").strip()
        m = re.search(r"^([A-Za-z]{2}\d{5,6})", t0_name)
        if m:
            return f"{m.group(1)}W_R02.mid"
        if re.match(r"^.{2}[0-9]{5,6}$", t0_name):
            return f"{t0_name}W_R02.mid"
            
    # Try finding 2 letters + 5 or 6 digits in fallback name (e.g. VD664135_41 -> VD664135)
    m = re.search(r"([A-Za-z]{2}\d{5,6})", fallback_name)
    if m:
        return f"{m.group(1)}W_R02.mid"

    # Try finding 5 or 6 digits in fallback name
    m = re.search(r"(\d{5,6})", fallback_name)
    if m:
        return f"VD{m.group(1)}W_R02.mid"
        
    return "VD000000W_R02.mid"


def run_delivery_check(
    input_midi_path: str,
    project_root: str | None = None,
    output_dir: str | None = None,
) -> DeliveryCheckResult:
    """
    Full pipeline:
    1. Reads MIDI file
    2. Runs ntvt_equiv byte transformations
    3. Writes to temp folder with proper delivery name
    4. Executes R02Checker.exe
    5. Parses results, computes marker timings in seconds
    6. Writes delivery report to reports/delivery/
    """
    if project_root is None:
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        
    checker_exe = os.path.join(project_root, "MIDIChecker", "R02Checker.exe")
    msg_file = os.path.join(project_root, "MIDIChecker", "R02Checker_Messages.txt")
    midichecker_dir = os.path.join(project_root, "MIDIChecker")
    
    if not os.path.exists(checker_exe):
        raise FileNotFoundError(f"R02Checker.exe not found at {checker_exe}")
        
    if output_dir is None:
        work_dir = os.path.join(project_root, "scratch", "reaper_delivery_check")
    else:
        work_dir = output_dir
    os.makedirs(work_dir, exist_ok=True)
    
    reports_dir = os.path.join(project_root, "reports", "delivery")
    os.makedirs(reports_dir, exist_ok=True)
    
    input_path = Path(input_midi_path)
    with open(input_path, "rb") as f:
        src_bytes = f.read()
        
    # Transform via ntvt_equiv
    transformed_bytes = transform_midi_bytes(src_bytes)
    delivery_filename = get_delivery_filename(src_bytes, fallback_name=input_path.stem)
    target_mid_path = os.path.abspath(os.path.join(work_dir, delivery_filename))
    with open(target_mid_path, "wb") as f:
        f.write(transformed_bytes)
        
    log_path = os.path.abspath(target_mid_path + ".log.txt")
    if os.path.exists(log_path):
        os.remove(log_path)
        
    # Run R02Checker.exe (2nd argument is the full log file path)
    subprocess.run([checker_exe, target_mid_path, log_path, msg_file], cwd=midichecker_dir)
    
    # Parse MIDI for tempo / time signature
    mid = mido.MidiFile(target_mid_path)
    tempo_segments = compute_tick_to_seconds_map(mid)
    
    issues: List[CheckerIssue] = []
    # Build a lookup of channel events per track to resolve coordinates for channel mismatch errors
    channel_event_ticks: dict[tuple[int, int], list[int]] = {}
    for trk_idx, track in enumerate(mid.tracks):
        cur_t = 0
        for msg in track:
            cur_t += msg.time
            ch = getattr(msg, "channel", None)
            if ch is not None:
                channel_event_ticks.setdefault((trk_idx, ch), []).append(cur_t)
    channel_event_cursors: dict[tuple[int, int], int] = {}

    if os.path.exists(log_path):
        with open(log_path, "r", encoding="cp932", errors="replace") as f_log:
            for line in f_log:
                l = line.strip()
                if not (l.startswith("[ERROR]") or l.startswith("[WARNING]")):
                    continue
                    
                sev = "ERROR" if l.startswith("[ERROR]") else "WARNING"
                
                # Check for coordinate pattern: [MMM:BB:TTT]
                pos_m = re.search(r"\[(\d{3}):(\d{2}):(\d{3})\]", l)
                meas, beat, tick = None, None, None
                sec = None
                
                if pos_m:
                    meas = int(pos_m.group(1))
                    beat = int(pos_m.group(2))
                    tick = int(pos_m.group(3))
                    abs_tick = measure_beat_tick_to_abs_tick(meas, beat, tick, mid)
                    sec = round(tick_to_seconds(abs_tick, tempo_segments), 4)
                    
                # Check for track pattern: Tr.N or Track N
                tr_m = re.search(r"Tr(?:ack|\.)\s*(\d+)", l)
                trk_num = int(tr_m.group(1)) if tr_m else None
                
                # For channel mismatch errors without coordinate prefix, resolve exact tick from MIDI events
                if not pos_m and trk_num is not None:
                    ch_m = re.search(r"Ch\.(\d+)", l)
                    if ch_m:
                        bad_ch = int(ch_m.group(1)) - 1  # 0-indexed
                        key = (trk_num, bad_ch)
                        cur_idx = channel_event_cursors.get(key, 0)
                        if key in channel_event_ticks and cur_idx < len(channel_event_ticks[key]):
                            abs_tick = channel_event_ticks[key][cur_idx]
                            channel_event_cursors[key] = cur_idx + 1
                            meas, beat, tick = abs_tick_to_measure_beat_tick(abs_tick, mid)
                            sec = round(tick_to_seconds(abs_tick, tempo_segments), 4)
                            # Prepend coordinate to line for readable report
                            l = f"[{sev}][{meas:03d}:{beat:02d}:{tick:03d}]{l[len(sev)+2:]}"
                
                # Clean rule name
                clean_line = re.sub(r"\[(ERROR|WARNING)\](\[\d+:\d+:\d+\])?", "", l).strip()
                rule_name = clean_line.split(":")[0].strip()
                
                issues.append(
                    CheckerIssue(
                        severity=sev,
                        raw_text=l,
                        rule_name=rule_name,
                        measure=meas,
                        beat=beat,
                        tick=tick,
                        track_num=trk_num,
                        seconds=sec,
                    )
                )

    total_errors = sum(1 for i in issues if i.severity == "ERROR")
    total_warnings = sum(1 for i in issues if i.severity == "WARNING")
    passed = (total_errors == 0 and total_warnings == 0)
    
    # Write report
    report_filename = f"{Path(delivery_filename).stem}_report.txt"
    report_path = os.path.join(reports_dir, report_filename)
    with open(report_path, "w", encoding="utf-8") as f_rep:
        f_rep.write("=" * 60 + "\n")
        f_rep.write(f"JOYSOUND 納品チェック レポート: {delivery_filename}\n")
        f_rep.write("=" * 60 + "\n")
        f_rep.write(f"判定: {'【合格】エラー0件・警告0件' if passed else '【要修正】不備があります'}\n")
        f_rep.write(f"エラー件数: {total_errors} 件\n")
        f_rep.write(f"警告件数  : {total_warnings} 件\n\n")
        
        if issues:
            f_rep.write("-" * 60 + "\n")
            f_rep.write("検出された指摘一覧 (位置順):\n")
            f_rep.write("-" * 60 + "\n")
            for idx, issue in enumerate(issues, 1):
                loc = f"[{issue.measure:03d}:{issue.beat:02d}:{issue.tick:03d}] ({issue.seconds}s)" if issue.measure is not None else "[全体]"
                trk = f"Tr.{issue.track_num}" if issue.track_num is not None else "全Track"
                f_rep.write(f"{idx:3d}. [{issue.severity}] {loc} {trk} : {issue.rule_name}\n")
                f_rep.write(f"     原文: {issue.raw_text}\n")
                
    return DeliveryCheckResult(
        song_name=input_path.stem,
        delivery_filename=delivery_filename,
        total_errors=total_errors,
        total_warnings=total_warnings,
        passed=passed,
        issues=issues,
        report_path=report_path,
        log_path=log_path,
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m a2m.delivery_checker <input_midi_path> [json_output_path]")
        sys.exit(1)
        
    in_mid = sys.argv[1]
    res = run_delivery_check(in_mid)
    
    # If json_output_path is provided, write json
    if len(sys.argv) >= 3:
        json_out = sys.argv[2]
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump({
                "song_name": res.song_name,
                "delivery_filename": res.delivery_filename,
                "total_errors": res.total_errors,
                "total_warnings": res.total_warnings,
                "passed": res.passed,
                "report_path": res.report_path,
                "log_path": res.log_path,
                "issues": [asdict(i) for i in res.issues]
            }, f, indent=2, ensure_ascii=False)
            
    print(f"Check finished: Passed={res.passed}, Errors={res.total_errors}, Warnings={res.total_warnings}")
