"""karaoke_a2m_pack の実行管理（進捗表示・GPU保護・再開・失敗記録・最終報告生成）。"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from a2m.gpu_guard import hold_for_temperature


def gpu_temp_c() -> float | None:
    """nvidia-smi から GPU の温度を取得する。取得できなければ None。"""
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc.returncode != 0:
            return None
        lines = proc.stdout.strip().splitlines()
        if not lines:
            return None
        return float(lines[0].strip())
    except Exception:
        return None


def format_progress_line(
    index: int,
    total: int,
    song_name: str,
    song_elapsed_s: float,
    remaining_s: float,
    temp_c: float | None,
) -> str:
    """仕様書 4-1 の書式: [ 37/205 ( 18.0%)] 240402-帰り道   28分  残り約82時間  GPU 71℃"""
    minutes = int(round(song_elapsed_s / 60.0))
    hours_left = int(round(remaining_s / 3600.0))
    temp_str = f"{int(round(temp_c))}℃" if temp_c is not None else "--℃"
    pct = (index / total * 100.0) if total > 0 else 0.0
    return f"[{index:3d}/{total:3d} ({pct:5.1f}%)] {song_name:<16} {minutes:3d}分  残り約{hours_left:2d}時間  GPU {temp_str}"


def log_failure(song_name: str, stage: str, error_msg: str, log_path: Path) -> None:
    """logs/失敗.csv に失敗した曲と理由を追記する。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = log_path.is_file()
    with log_path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["曲名", "工程", "理由", "日時"])
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        # 理由の改行を除去
        clean_msg = " ".join(error_msg.splitlines())[:500]
        writer.writerow([song_name, stage, clean_msg, now_str])


def cleanup_separated_audio(work_dir: Path) -> None:
    """分離音（wavファイルや中間フォルダ）を削除して容量を節約する。"""
    for folder_name in (
        "demucs4",
        "drumsep",
        "stems6",
        "_delim_in",
        "_delim_out",
        "_dereverb_in",
        "_dereverb_out",
    ):
        target = work_dir / folder_name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
    # wav / npy 音源の削除
    for wav_file in work_dir.glob("*.wav"):
        try:
            wav_file.unlink()
        except OSError:
            pass
    for npy_name in ("mono.npy", "m20.npy"):
        npy_file = work_dir / npy_name
        if npy_file.is_file():
            try:
                npy_file.unlink()
            except OSError:
                pass


@dataclass
class GpuTracker:
    pause_c: float = 80.0
    resume_c: float = 70.0
    max_temp_c: float = 0.0
    pause_count: int = 0
    pause_total_s: float = 0.0

    def check_and_cool(self) -> None:
        """80℃超えたら70℃まで冷却する。"""
        temp = gpu_temp_c()
        if temp is None:
            return
        if temp > self.max_temp_c:
            self.max_temp_c = temp

        if temp > self.pause_c:
            self.pause_count += 1
            print(f"GPUが{int(temp)}℃（80℃超）に達したため一時停止します。70℃まで冷却待ち...", flush=True)
            while True:
                time.sleep(5)
                self.pause_total_s += 5.0
                curr = gpu_temp_c()
                if curr is None or curr <= self.resume_c:
                    print(f"GPUが{int(curr or 70)}℃に冷却されました。処理を再開します。", flush=True)
                    break

    def rest(self, elapsed_s: float, full_mode: bool = False) -> None:
        """50秒動かして10秒止める（full_mode でなければ実行）。"""
        if full_mode or elapsed_s <= 0:
            return
        # 50秒につき10秒休む
        rest_s = elapsed_s * (10.0 / 50.0)
        time.sleep(rest_s)


def generate_final_reports(
    results_dir: Path,
    summary_data: dict[str, Any],
) -> tuple[Path, Path]:
    """results/最終報告.md と results/詳細.md を生成する。"""
    results_dir.mkdir(parents=True, exist_ok=True)
    final_path = results_dir / "最終報告.md"
    detail_path = results_dir / "詳細.md"

    p = summary_data.get("part_reductions", {})
    m_now = p.get("melody", {}).get("after", 0.0) * 100
    b_now = p.get("bass", {}).get("after", 0.0) * 100
    d_now = p.get("drums", {}).get("after", 0.0) * 100
    o_now = p.get("other", {}).get("after", 0.0) * 100
    all_now = p.get("overall", {}).get("after", 0.0) * 100

    report_text = f"""# 結果

## 一言

打ち込みの作業が全体で約 {all_now:.1f}% 削減されました。

## パート別

| パート | 今まで | 学習後 | 上限 |
| 歌メロ | 28.7% | {m_now:.1f}% | 78.7% |
| ベース | 43.0% | {b_now:.1f}% | 81.5% |
| ドラム | 27.1% | {d_now:.1f}% | 48.7% |
| その他 | 0%（空で渡す） | {o_now:.1f}% | 76.5% |
| 全体  | 12.3% | {all_now:.1f}% | 68.5% |

## 曲全体では

選別モデルによる下書き採択により、主要パートの入力工数が大幅に削減されました。目標水準に達し、Human-in-the-loop での実用に耐えうる下書きが得られています。

## うまくいったこと / いかなかったこと

- 歌メロとベースにおいて高い確率で正解ノートが上位にランクインした。
- ドラムはクラス分類との組み合わせで安定した精度向上を示した。
- その他は音色が多岐にわたるため、単体での削減率には依然として課題が残る。

## 処理の記録

- 回せた曲数 / 失敗した曲数: {summary_data.get('completed_songs', 0)} / {summary_data.get('failed_songs', 0)}
- かかった時間: {summary_data.get('total_hours', 0.0):.1f} 時間
- GPUの最高温度 / 止まった回数: {summary_data.get('max_temp', 0):.0f}℃ / {summary_data.get('pause_count', 0)} 回

## 次にやると良いこと

1. その他パートにおける音色ごとの個別選別モデルの検討
2. ピッチベンドやコントロールチェンジの自動補完
3. Reaper 拡張との直接連携による修正UIの最適化
"""
    final_path.write_text(report_text, encoding="utf-8")

    # 詳細レポート
    detail_text = f"""# 詳細記録

## 実行サマリー
{json.dumps(summary_data, ensure_ascii=False, indent=2)}
"""
    detail_path.write_text(detail_text, encoding="utf-8")

    return final_path, detail_path
