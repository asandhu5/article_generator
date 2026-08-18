"""JSON-file-backed persistence for generated articles.

Same rationale as any single-user local tool: no database needed. Each
article gets a folder under data/articles/<id>/ holding the final Markdown
and a metadata JSON; a flat index.json lists them all for the history view.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_LOCK = threading.Lock()


@dataclass
class ArticleMeta:
    id: str
    topic: str
    tone: str
    audience: str
    word_count: int
    model: str
    created_at: str
    status: str = "generating"  # generating -> ready | error
    source: str = "live"  # live | demo
    title: str = ""
    current_step: str = "Starting..."
    step_index: int = 0
    step_total: int = 0
    generated_words: int = 0
    generation_seconds: float = 0.0
    polish_skipped: bool = False
    error: Optional[str] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class ArticleStore:
    def __init__(self, articles_dir: Path):
        self.dir = articles_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir.parent / "index.json"

    def _read_index(self) -> dict[str, dict]:
        if not self.index_path.exists():
            return {}
        try:
            return json.loads(self.index_path.read_text())
        except json.JSONDecodeError:
            return {}

    def _write_index(self, index: dict[str, dict]) -> None:
        self.index_path.write_text(json.dumps(index, indent=2))

    def list_articles(self) -> list[dict]:
        index = self._read_index()
        return sorted(index.values(), key=lambda a: a.get("created_at", ""), reverse=True)

    def create(self, meta: ArticleMeta) -> None:
        with _LOCK:
            (self.dir / meta.id).mkdir(parents=True, exist_ok=True)
            index = self._read_index()
            index[meta.id] = asdict(meta)
            self._write_index(index)

    def update(self, article_id: str, **fields) -> None:
        with _LOCK:
            index = self._read_index()
            if article_id in index:
                index[article_id].update(fields)
                self._write_index(index)

    def get_meta(self, article_id: str) -> Optional[dict]:
        return self._read_index().get(article_id)

    def save_markdown(self, article_id: str, markdown_text: str) -> None:
        (self.dir / article_id / "article.md").write_text(markdown_text)

    def load_markdown(self, article_id: str) -> Optional[str]:
        path = self.dir / article_id / "article.md"
        return path.read_text() if path.exists() else None

    def save_outline(self, article_id: str, outline_dict: dict) -> None:
        (self.dir / article_id / "outline.json").write_text(json.dumps(outline_dict, indent=2))
