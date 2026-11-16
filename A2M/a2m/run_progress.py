"""曲と工程の進捗。一時ファイルに書いてから名前を変える。"""

from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def match_recorded(path: Path) -> bool:
    """第3部の一致が pass のときだけ本番に進む。"""
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return bool(payload.get("pass"))


def load_progress(path: Path) -> dict:
    if not path.is_file():
        return {"songs": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def mark_stage(progress: dict, song: str, stage: str, status: str, detail: str = "") -> None:
    songs = progress.setdefault("songs", {})
    entry = songs.setdefault(song, {"stages": {}, "error": ""})
    entry["stages"][stage] = status
    if status == "失敗":
        entry["error"] = detail


def pending_stages(progress: dict, song: str, stages: list[str]) -> list[str]:
    done = progress.get("songs", {}).get(song, {}).get("stages", {})
    return [stage for stage in stages if done.get(stage) != "完了"]


def outside_reference(
    value: float,
    reference: list[float],
    *,
    k: float,
) -> bool:
    """基準の平均から標準偏差の k 倍より外なら True。"""
    if len(reference) < 2:
        return False
    import numpy as np

    arr = np.asarray(reference, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std())
    return abs(value - mean) > k * std


def init_progress_session(
    progress: dict,
    total_songs: int,
) -> dict:
    """実行セッション情報（手を放した時点の基準値など）を初期化・更新する。"""
    import time

    songs = progress.get("songs", {})
    completed_count = sum(
        1 for s in songs.values() if s.get("stages", {}).get("全工程") == "完了"
    )
    failed_count = sum(
        1 for s in songs.values() if s.get("stages", {}).get("全工程") == "失敗"
    )

    session = progress.setdefault("session", {})
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    session["session_start_time"] = now_str
    session["baseline_completed"] = completed_count
    session["total_songs"] = total_songs
    session["completed_songs"] = completed_count
    session["failed_songs"] = failed_count
    session["percent_total"] = round((completed_count / total_songs * 100.0), 2) if total_songs > 0 else 0.0
    session["since_baseline_completed"] = 0
    session["since_baseline_percent"] = 0.0
    session["current_song"] = ""
    session["current_status"] = "実行中"
    session["updated_at"] = now_str
    return progress


def update_progress_summary(
    progress: dict,
    total_songs: int,
    current_song: str = "",
    status: str = "実行中",
    eta_seconds: float | None = None,
) -> dict:
    """現在の全体進捗率およびセッション基準（手を放して以降）の進捗率を更新する。"""
    import time

    songs = progress.get("songs", {})
    completed_count = sum(
        1 for s in songs.values() if s.get("stages", {}).get("全工程") == "完了"
    )
    failed_count = sum(
        1 for s in songs.values() if s.get("stages", {}).get("全工程") == "失敗"
    )

    session = progress.setdefault("session", {})
    baseline = session.get("baseline_completed", 0)
    since_baseline = max(0, completed_count - baseline)
    target_remain = max(1, total_songs - baseline)

    session["total_songs"] = total_songs
    session["completed_songs"] = completed_count
    session["failed_songs"] = failed_count
    session["percent_total"] = round((completed_count / total_songs * 100.0), 2) if total_songs > 0 else 0.0
    session["since_baseline_completed"] = since_baseline
    session["since_baseline_percent"] = round((since_baseline / target_remain * 100.0), 2)
    session["current_song"] = current_song
    session["current_status"] = status
    session["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if eta_seconds is not None:
        session["eta_hours"] = round(eta_seconds / 3600.0, 1)
    return progress
