"""ミックスの初期値（M3）：仕様で決まる値と、過去の納品でよく使われた値。

測った結果（docs/TRIED.md の M3）：当たるのはパン（トラック名の L/R/C）だけ。
音量・リバーブ・コーラスは「いつもの値」と大差ないので、ここでは音色ごとの中央値を「出発点」として出す。
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from typing import Any

from a2m.mix_match import cc_from_db, db_from_cc
from a2m.setup_table import strip_track_prefix

_SIDE = re.compile(r"[_\s\-]([LRC])\d?\s*$")
FIELDS = ("reverb", "chorus", "delay")


def side_from_name(name: str) -> str | None:
    """トラック名の末尾の _L / _R / _C。"""
    m = _SIDE.search(strip_track_prefix(name))
    return m.group(1) if m else None


def _num(x: Any) -> float | None:
    return None if x in ("", "None", None) else float(x)


def _patch(row: dict[str, Any]) -> str:
    return f"{row['bank_msb']}/{row['bank_lsb']}/{row['program']}"


def _family(row: dict[str, Any]) -> str:
    return str((int(row["program"]) - 1) // 8)


def build_house(track_rows: list[dict[str, Any]], song_rows: list[dict[str, Any]], min_count: int) -> dict[str, Any]:
    """過去の納品から、よく使われた値の表を作る。対象は上モノ（役割 other / se）。"""
    rows = [r for r in track_rows if r["role"] in ("other", "se")]
    pan: dict[str, list[float]] = defaultdict(list)
    by_patch: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_family: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    overall: dict[str, list[float]] = defaultdict(list)
    efx: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        side = side_from_name(r["name"])
        if side and _num(r["pan"]) is not None:
            pan[side].append(_num(r["pan"]))
        values = {f: _num(r[f]) for f in FIELDS}
        vol, expr = _num(r["volume"]), _num(r["expression_median"])
        if vol and expr:
            values["level_db"] = db_from_cc(vol, 100.0) + db_from_cc(expr, 100.0)
        for f, v in values.items():
            if v is not None:
                by_patch[_patch(r)][f].append(v)
                by_family[_family(r)][f].append(v)
                overall[f].append(v)
        efx[_patch(r)].append(1 if str(r["efx_on"]) == "True" else 0)

    def med(groups: dict[str, dict[str, list[float]]]) -> dict[str, dict[str, float]]:
        return {k: {f: statistics.median(v) for f, v in fs.items() if len(v) >= min_count} for k, fs in groups.items()}

    sys_keys = sorted(k for k in song_rows[0] if k.startswith("sys_")) if song_rows else []
    system = {}
    for k in sys_keys:
        vals = [int(s[k]) for s in song_rows if s[k] not in ("", None)]
        if vals:
            system[k[4:]] = Counter(vals).most_common(1)[0][0]
    return {
        "pan": {s: statistics.median(v) for s, v in pan.items()},
        "patch": med(by_patch),
        "family": med(by_family),
        "overall": {f: statistics.median(v) for f, v in overall.items()},
        "efx_rate": {k: sum(v) / len(v) for k, v in efx.items() if len(v) >= min_count},
        "system": system,
        "efx_type": Counter(s["efx_type"] for s in song_rows if s.get("efx_type")).most_common(1)[0][0] if song_rows else "",
    }


def _lookup(house: dict[str, Any], row: dict[str, Any], field: str) -> tuple[float, str]:
    for table, key, label in (("patch", _patch(row), "同じ音色"), ("family", _family(row), "同じ系統")):
        v = house[table].get(key, {}).get(field)
        if v is not None:
            return v, label
    return house["overall"][field], "全体"


def suggest_track(row: dict[str, Any], house: dict[str, Any], rules: dict[str, Any], efx_min_rate: float) -> dict[str, Any]:
    """1トラックぶんの初期値。各値に、どこから来たか（仕様 / 過去の値）を付ける。"""
    role = row["role"]
    out: dict[str, Any] = {"track": row["name"], "role": role}

    def put(key: str, value: float, source: str) -> None:
        out[key] = int(round(value))
        out[key + "_from"] = source

    if role == "vocal" or role == "vocal2":
        v = rules["vocal"]
        put("volume", v["volume"], "仕様")
        put("pan", v["pan"], "仕様")
        put("reverb", (v["reverb_min"] + v["reverb_max"]) / 2, "仕様（20〜40）")
        out["velocity"] = v["velocity"]
        return out
    put("volume", rules["volume_default"], "仕様")
    if role == "chorus":
        put("expression", rules["chorus"]["expression"], "仕様")
        out["velocity"] = rules["chorus"]["velocity"]
        return out
    if role == "bass":
        b = rules["bass"]
        put("pan", b["pan"], "仕様")
        put("reverb", b["reverb"], "仕様")
        put("chorus", b["chorus"], "仕様")
        out["velocity"] = b["velocity"]
        return out
    if role in ("drums", "sub_drums"):
        return out
    side = side_from_name(row["name"])
    if side and side in house["pan"]:
        put("pan", house["pan"][side], f"名前の _{side}")
    else:
        put("pan", 64, "名前に左右が無い")
    level, src = _lookup(house, row, "level_db")
    put("expression", cc_from_db(level, 100), f"過去の値（{src}）")
    for f in FIELDS:
        v, src = _lookup(house, row, f)
        put(f, v, f"過去の値（{src}）")
    rate = house["efx_rate"].get(_patch(row))
    if rate is not None and rate >= efx_min_rate:
        out["efx"] = "OD1/OD2"
        out["efx_from"] = f"この音色の {rate:.0%} で使用"
    return out
