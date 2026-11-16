"""プロジェクト直下の .env を読み込む（.gitignore 済み、任意）。"""

from __future__ import annotations

from pathlib import Path


def load_project_dotenv() -> None:
    """`.env` があれば `HF_TOKEN` などを os.environ に載せる。既存の環境変数は上書きしない。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=False)
