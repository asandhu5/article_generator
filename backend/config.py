
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    openai_api_key: str
    host: str
    port: int
    data_dir: Path
    articles_dir: Path

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key) and self.openai_api_key != "your-key-here"


def _load() -> Config:
    data_dir = BASE_DIR / "data"
    return Config(
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        host=os.getenv("HOST", "127.0.0.1").strip(),
        port=_int_env("PORT", 5050),
        data_dir=data_dir,
        articles_dir=data_dir / "articles",
    )


config = _load()
config.articles_dir.mkdir(parents=True, exist_ok=True)
