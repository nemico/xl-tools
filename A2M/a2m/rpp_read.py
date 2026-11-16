"""人が Reaper で作業した .rpp から、トラックの音を読む（秒、そのプロジェクトのテンポマップで）。"""

from __future__ import annotations

from pathlib import Path


def tempo_points(text: str) -> list[tuple[float, float]]:
    """テンポの点 (秒, BPM)。無ければ TEMPO 行の値で一定。"""
    pts = [(float(l.split()[1]), float(l.split()[2])) for l in text.splitlines() if l.startswith("    PT ")]
    if pts:
        return pts
    for line in text.splitlines():
        if line.startswith("  TEMPO "):
            return [(0.0, float(line.split()[1]))]
    return [(0.0, 120.0)]


def qn_to_seconds(qn: float, pts: list[tuple[float, float]]) -> float:
    """プロジェクトの頭からの拍（4分音符）を秒にする（テンポの点は四角い変化とみなす）。"""
    t, bpm, beat = pts[0][0], pts[0][1], 0.0
    for t1, b1 in pts[1:]:
        nb = beat + (t1 - t) * bpm / 60.0
        if qn < nb:
            break
        t, bpm, beat = t1, b1, nb
    return t + (qn - beat) * 60.0 / bpm


def seconds_to_qn(sec: float, pts: list[tuple[float, float]]) -> float:
    t, bpm, beat = pts[0][0], pts[0][1], 0.0
    for t1, b1 in pts[1:]:
        if sec < t1:
            break
        beat += (t1 - t) * bpm / 60.0
        t, bpm = t1, b1
    return beat + (sec - t) * bpm / 60.0


def track_notes(path: Path, track_name: str) -> list[tuple[float, float, int]]:
    """名前が track_name のトラックの MIDI アイテムの音 (始まり, 終わり, 音高)。秒。ミュートしたアイテムは読まない。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    pts = tempo_points(text)
    lines = text.splitlines()
    notes: list[tuple[float, float, int]] = []
    in_track = False
    item: dict | None = None
    for line in lines:
        if line.startswith("  <TRACK"):
            in_track = False
        elif line.startswith("    NAME ") and item is None:
            in_track = line[len("    NAME "):].strip().strip('"') == track_name
        if not in_track:
            continue
        s = line.strip()
        if line.startswith("    <ITEM"):
            item = {"pos": 0.0, "mute": False, "ppq": 960, "tick": 0, "on": {}, "midi": False}
        elif item is not None and line.startswith("      POSITION "):
            item["pos"] = float(s.split()[1])
        elif item is not None and line.startswith("      MUTE "):
            item["mute"] = s.split()[1] == "1"
        elif item is not None and s.startswith("HASDATA "):
            f = s.split()
            item["ppq"] = int(f[2])
            item["midi"] = len(f) > 3 and f[3] == "QN"
        elif item is not None and item["midi"] and (s.startswith("E ") or s.startswith("e ")):
            f = s.split()
            item["tick"] += int(f[1])
            status, d1, d2 = int(f[2], 16), int(f[3], 16), int(f[4], 16)
            kind = status & 0xF0
            qn0 = seconds_to_qn(item["pos"], pts)
            if kind == 0x90 and d2 > 0:
                item["on"][d1] = item["tick"]
            elif kind in (0x80, 0x90) and d1 in item["on"]:
                start = item["on"].pop(d1)
                if not item["mute"]:
                    a = qn_to_seconds(qn0 + start / item["ppq"], pts)
                    b = qn_to_seconds(qn0 + item["tick"] / item["ppq"], pts)
                    notes.append((a, b, d1))
        elif item is not None and line == "    >":
            item = None
    return sorted(notes)
