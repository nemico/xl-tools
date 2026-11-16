"""第6.3b節: Phoenix 書き出し設定の比較。"""

from __future__ import annotations

from dataclasses import dataclass

# 仕様 6.3b の A〜D（config は変更しない）
RENDER_OPT_PRESETS: dict[str, list[str]] = {
    "A": ["-t", "10"],
    "B": ["-t", "1000"],
    "C": ["-t", "5000"],
    "D": ["-t", "5000", "-o", "-m", "512"],
}

DEFAULT_COMPARE_SONGS: tuple[str, str] = (
    "260907-Body & Soul (Badly in Love Remix by TeddyLoid)",
    "260818-YOROSHIKU!!",
)


@dataclass(frozen=True)
class RenderOptPreset:
    name: str
    args: list[str]

    @classmethod
    def all_presets(cls) -> list[RenderOptPreset]:
        return [cls(name=k, args=v) for k, v in RENDER_OPT_PRESETS.items()]
