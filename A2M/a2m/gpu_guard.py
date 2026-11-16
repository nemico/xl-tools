"""GPU の電力上限と、温度で止める条件。定格は呼び出し側が nvidia-smi から渡す。"""

from __future__ import annotations


def power_cap_watts(rated_watts: float, ratio: float) -> int:
    return int(round(rated_watts * ratio))


def hold_for_temperature(temp_c: float, *, pause_c: float, resume_c: float, holding: bool) -> bool:
    """80℃を超えたら止める。70℃まで下がったら再開する。"""
    if temp_c > pause_c:
        return True
    if holding and temp_c > resume_c:
        return True
    return False
