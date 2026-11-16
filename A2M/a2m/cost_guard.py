"""編集コストが負なら止める。打点数を超えたら記録する。0 で止めない。"""

from __future__ import annotations


class NegativeCostError(RuntimeError):
    """編集コストが 0 未満。0 に切り上げて通さない。"""


def guard_cost(cost: int, n_gt: int, where: str, overflows: list | None = None) -> int:
    if cost < 0:
        raise NegativeCostError(f"{where}: cost={cost}")
    if cost > n_gt and overflows is not None:
        overflows.append((where, cost, n_gt))
    return cost
