"""プール候補のうち、基準を下回らない一番良い削減率を選ぶ。"""

from __future__ import annotations


def choose_best(reductions: dict[str, float], floor: float) -> str:
    """削減率が高い名前を返す。floor 未満は採らない。全部未満なら pool16。"""
    eligible = {name: rate for name, rate in reductions.items() if rate >= floor}
    if not eligible:
        return "pool16"
    return max(eligible, key=lambda name: (eligible[name], name))
