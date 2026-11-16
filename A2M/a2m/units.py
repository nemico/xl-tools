"""音高の単位変換（MIDI 番号 ⇔ Hz）。"""

from __future__ import annotations

import math

A4_HZ = 440.0
A4_MIDI = 69


def midi_to_hz(midi_number: float) -> float:
    """MIDI ノート番号（実音）を Hz に変換する。"""
    return A4_HZ * (2.0 ** ((midi_number - A4_MIDI) / 12.0))


def hz_to_midi(hz: float) -> float:
    """Hz を MIDI ノート番号（実音、連続値）に変換する。"""
    if hz <= 0.0:
        raise ValueError("hz must be positive")
    return A4_MIDI + 12.0 * math.log2(hz / A4_HZ)
