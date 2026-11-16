"""X-Laben / GS の SysEx を読み書きする。

納品MIDIの SysEx は、ヘッダが X-Laben 用（00 40 04 01 58 12）で、
その後ろは GS と同じ「アドレス3バイト＋データ＋チェックサム」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

GS_HEAD = (0x41, 0x10, 0x42, 0x12)
XL_HEAD = (0x00, 0x40, 0x04, 0x01, 0x58, 0x12)

ADDR_EFX_TYPE = (0x40, 0x03, 0x00)
SYSTEM_BLOCK = (0x40, 0x01)
PART_EFX_OFFSET = 0x22


@dataclass(frozen=True)
class GsParam:
    address: tuple[int, int, int]
    data: tuple[int, ...]


def gs_checksum(address: Sequence[int], data: Sequence[int]) -> int:
    return (128 - (sum(address) + sum(data)) % 128) % 128


def parse_sysex(payload: Iterable[int]) -> GsParam | None:
    """mido の sysex.data（F0/F7 を除いた中身）を読む。GS 形式でなければ None。"""
    d = tuple(payload)
    for head in (XL_HEAD, GS_HEAD):
        n = len(head)
        if d[:n] == head and len(d) >= n + 5:
            return GsParam(address=tuple(d[n : n + 3]), data=tuple(d[n + 3 : -1]))
    return None


def build_xl_sysex(address: Sequence[int], data: Sequence[int]) -> tuple[int, ...]:
    """X-Laben ヘッダの SysEx の中身（F0/F7 を除く）を作る。"""
    return (*XL_HEAD, *address, *data, gs_checksum(address, data))


def part_block_to_channel(block: int) -> int:
    """GS のパート番号（アドレス2バイト目の下位4ビット）を MIDI チャンネル（1始まり）にする。"""
    if block == 0:
        return 10
    return block if block <= 9 else block + 1


def efx_part_channel(param: GsParam) -> int | None:
    """「このパートに EFX をかける」設定なら、そのチャンネル（1始まり）を返す。"""
    a0, a1, a2 = param.address
    if a0 == 0x40 and (a1 >> 4) == 4 and a2 == PART_EFX_OFFSET and param.data and param.data[0] == 1:
        return part_block_to_channel(a1 & 0x0F)
    return None
