"""コードの区間を、プロジェクトに入れる形（リージョンと、構成音を塗りつぶした MIDI）にする。"""

from __future__ import annotations

import colorsys

import numpy as np

from a2m.draft_midi import seconds_to_ticks, ticks_to_seconds
from a2m.lvchord import chord_pitch_classes, display_name
from a2m.project_layout import TempoPoint
from a2m.rpp_project import MidiNote, Region

FIFTHS = [0, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10, 5]  # 5度圏の順。近い調の根音が近い色になる


def chord_color(label: str) -> tuple[int, int, int]:
    """同じコードはいつも同じ色。色相は根音（5度圏の順）、明るさと鮮やかさはコードの種類。"""
    import mir_eval

    root, bitmap, _ = mir_eval.chord.encode(label.split("/")[0])
    hue = FIFTHS.index(root) / 12.0
    minor = bool(bitmap[3]) and not bitmap[4]
    seventh = bool(bitmap[10] or bitmap[11])
    sat = 0.45 if seventh else 0.65
    val = 0.62 if minor else 0.92
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return int(r * 255), int(g * 255), int(b * 255)


def snap_segments(segments: list[tuple[float, float, str]], points: list[TempoPoint], tpb: int, snap: int,
                  start_tick: int) -> list[tuple[int, int, str]]:
    """(始まり, 終わり, コード名)（プロジェクトの秒）を、snap tick ごとにそろえる。

    曲の頭（start_tick）より前と、コード無しは捨てる。そろえて長さが0になった区間は消し、同じコードが続けばつなぐ。
    """
    out: list[tuple[int, int, str]] = []
    for a, b, label in segments:
        if label in ("N", "X"):
            continue
        ta, tb = seconds_to_ticks(points, np.array([a, b]), tpb)
        ta = max(int(round(ta / snap)) * snap, start_tick)
        tb = int(round(tb / snap)) * snap
        if tb <= ta:
            continue
        if out and out[-1][2] == label and out[-1][1] >= ta:
            out[-1] = (out[-1][0], max(out[-1][1], tb), label)
            continue
        if out and out[-1][1] > ta:  # 前の区間と重なったら、前の区間の終わりから始める
            ta = out[-1][1]
        if tb > ta:
            out.append((ta, tb, label))
    return out


def fill_notes(segments: list[tuple[int, int, str]], low: int, high: int, velocity: int) -> list[MidiNote]:
    """コードの区間ごとに、構成音の音名にあたる鍵盤を low〜high で全部押さえる（オクターブは問わない）。"""
    notes = []
    for a, b, label in segments:
        pcs = set(chord_pitch_classes(label))
        notes += [MidiNote(a, b, p, velocity) for p in range(low, high + 1) if p % 12 in pcs]
    return notes


def chord_regions(segments: list[tuple[int, int, str]], points: list[TempoPoint], tpb: int) -> list[Region]:
    """コードの区間をリージョンにする（名前は普通のコード表記、色はコードごと）。"""
    out = []
    for a, b, label in segments:
        sa, sb = ticks_to_seconds(points, np.array([a, b]), tpb)
        out.append(Region(float(sa), float(sb), display_name(label), chord_color(label)))
    return out
