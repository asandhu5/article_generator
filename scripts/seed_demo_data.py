
from __future__ import annotations

import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.config import config  # noqa: E402
from backend.llm_chains import count_words  # noqa: E402
from backend.store import ArticleMeta, ArticleStore, now_iso  # noqa: E402

logger = logging.getLogger(__name__)

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sample_article.md"


def seed_if_needed(store: ArticleStore, fixture_path: Path = FIXTURE_PATH) -> int:
    if any(a.get("source") == "demo" for a in store.list_articles()):
        return 0
    if not fixture_path.exists():
        return 0

    markdown_text = fixture_path.read_text()
    title = markdown_text.splitlines()[0].lstrip("#").strip()
    section_count = markdown_text.count("\n## ")

    article_id = "sample-edge-ai"
    meta = ArticleMeta(
        id=article_id,
        topic="The rise of on-device (edge) AI",
        tone="formal",
        audience="software engineers and technical readers",
        word_count=count_words(markdown_text),
        model="gpt-4o-mini",
        created_at=now_iso(),
        status="ready",
        source="demo",
        title=title,
        current_step="Done",
        step_index=section_count + 3,
        step_total=section_count + 3,
        generated_words=count_words(markdown_text),
        generation_seconds=38.2,
        polish_skipped=False,
    )
    store.create(meta)
    store.save_markdown(article_id, markdown_text)
    logger.info("Seeded demo article %s (%s)", article_id, title)
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _store = ArticleStore(config.articles_dir)
    count = seed_if_needed(_store)
    print(f"Seeded {count} demo article(s) into {_store.dir}")
