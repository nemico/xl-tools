"""Reaper のマクロ（ai_build_project.lua）から呼ばれる裏方。

使い方: python scripts/reaper_backend.py <作業フォルダ>
  <作業フォルダ>/request.txt（UTF-8、1行に「キー<TAB>値」）を読む：
    audio   原曲のファイル
    layout  4 | piano | guitar | 6
    overlap 分離の重ねの数（config.yaml の separation.overlap_choices のどれか。省くと overlap_listen）
    media   プロジェクトの録音フォルダ（ステムは <media>/<reaper.stems_subdir>/<name>/ に書く）
    name    ファイル名に使う名前
  書くもの：
    progress.txt  「進み具合(0-100)<TAB>いまやっていること」（何度も上書き）
    result.txt    結果（タブ区切り。形式は write_result を見る）
    error.txt     失敗したとき、理由
    log.txt       詳しいログ
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 配布（ZIP）では、A2M フォルダの models/ からモデルを読む（ネットにつながない）。torch などを読む前に決める
from a2m.portable import use_bundled_models, usable_device  # noqa: E402

use_bundled_models()

from a2m.rules import load_config_section, load_delivery_rules  # noqa: E402
from a2m.song_build import SongBuild, build_song  # noqa: E402

RESULT_VERSION = 1


def read_request(path: Path) -> dict[str, str]:
    req = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "\t" in line:
            k, v = line.split("\t", 1)
            req[k.strip()] = v.strip()
    return req


def request_overlap(req: dict[str, str], sep: dict) -> int:
    """依頼の overlap（分離の重ねの数）。省けば config の既定、選べない値なら止める。"""
    if not req.get("overlap"):
        return int(sep["overlap_listen"])
    try:
        n = int(req["overlap"])
    except ValueError:
        n = -1
    if n not in sep["overlap_choices"]:
        raise ValueError(f"分離の重ねの数 {req['overlap']} は選べません（{sep['overlap_choices']} のどれか）")
    return n


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def result_lines(b: SongBuild, cfg: dict) -> list[str]:
    """結果の形式（1行1件、タブ区切り）。
    VERSION <版>
    AUDIO <置く位置(秒)> <頭を捨てる長さ(秒)> <原曲の長さ(秒)>
    TEMPO <秒> <BPM> <拍子の分子 or 0> <拍子の分母 or 0>
    STEM <2MIX の中の何番目(0始まり)> <トラック名> <ファイル>
    MIDI <トラック名> <テイク名> <チャンネル(1-16)> <長さ(秒)>      … 続く N 行がこのアイテムの音
    CHORDTRACK <トラック名> <この名前のトラックの上に作る> <チャンネル> <長さ(秒)>  … 続く N 行
    N <始まり(tick)> <終わり(tick)> <音高> <ベロシティ>
    SUMMARY <1行の説明>
    """
    t = b.timeline
    lines = [f"VERSION\t{RESULT_VERSION}", f"PPQ\t{cfg['rules']['ticks_per_beat']}",
             f"AUDIO\t{t.item_position_s:.9f}\t{t.item_offset_s:.9f}\t{b.audio_length_s:.6f}"]
    for p in t.points:
        lines.append(f"TEMPO\t{p.time_s:.9f}\t{p.bpm}\t{p.numerator or 0}\t{4 if p.numerator else 0}")
    for k, (stem, f) in enumerate(b.stems):
        lines.append(f"STEM\t{k}\t{stem}\t{f}")
    for item in b.drafts:
        lines.append(f"MIDI\t{item.track_name}\t{item.item_name}\t{item.channel}\t{item.length_s:.6f}")
        lines += [f"N\t{n.start_tick}\t{n.end_tick}\t{n.pitch}\t{n.velocity}" for n in item.notes]
    if b.chord_track is not None:
        c = b.chord_track
        lines.append(f"CHORDTRACK\t{c.track_name}\t{cfg['annotate']['chord_track_before']}\t{c.channel}\t{c.length_s:.6f}")
        lines += [f"N\t{n.start_tick}\t{n.end_tick}\t{n.pitch}\t{n.velocity}" for n in c.notes]
    lines += [f"SUMMARY\t{s}" for s in b.summary]
    return lines


def main() -> None:
    if not os.environ.get("HF_TOKEN"):
        # Reaper から起動するとトークンが無いことがある。無ければダウンロード済みのモデルだけで動かす
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    job = Path(sys.argv[1])
    for f in ("result.txt", "error.txt", "progress.txt"):
        (job / f).unlink(missing_ok=True)
    log = (job / "log.txt").open("w", encoding="utf-8")

    def progress(pct: int, msg: str) -> None:
        _write_atomic(job / "progress.txt", f"{pct}\t{msg}")
        log.write(f"{pct}\t{msg}\n")
        log.flush()

    try:
        req = read_request(job / "request.txt")
        cfg = {k: load_config_section(k) for k in ("m1", "m4", "separation", "lvchord", "annotate")}
        cfg["rules"] = load_delivery_rules()
        cfg["separation"]["overlap_listen"] = request_overlap(req, cfg["separation"])
        # GPU が使えなければ CPU（拍・下書き）。理由はログに
        device, why = usable_device(cfg["m1"].get("device", "cuda"))
        cfg["m1"]["device"] = device
        cfg["m4"]["draft"]["device"] = device
        log.write(f"デバイス：{device}（{why}）\n")
        if device == "cpu":
            progress(1, why)
        stems = Path(req["media"]) / load_config_section("reaper")["stems_subdir"] / req["name"]
        build = build_song(Path(req["audio"]), req.get("layout", "4"), stems, req["name"], job / "work", cfg, progress)
        _write_atomic(job / "result.txt", "\n".join(result_lines(build, cfg)) + "\n")
        progress(100, "完了")
    except Exception as e:  # 理由は Reaper の画面に出す。詳しくはログ
        log.write(traceback.format_exc())
        _write_atomic(job / "error.txt", f"{type(e).__name__}: {e}")
    finally:
        log.close()


if __name__ == "__main__":
    main()
