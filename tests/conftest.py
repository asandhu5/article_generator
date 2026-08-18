import sys
from pathlib import Path
from typing import Callable, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class FakeMessage:
    """Stand-in for langchain's AIMessage: needs .content and .response_metadata."""

    def __init__(self, content: str, finish_reason: str = "stop"):
        self.content = content
        self.response_metadata = {"finish_reason": finish_reason}


class _BoundFake:
    """Returned by FakeLLM.bind(); coercible into a Runnable because it's callable."""

    def __init__(self, parent: "FakeLLM", max_tokens: Optional[int]):
        self._parent = parent
        self.max_tokens = max_tokens

    def __call__(self, prompt_value, **kwargs):
        return self._parent.message_factory(self.max_tokens)

    def with_structured_output(self, schema):
        return lambda _prompt_value, **kwargs: self._parent.outline


class FakeLLM:
    """Minimal stand-in for ChatOpenAI, just enough to drive llm_chains.py's
    LCEL pipelines (`prompt | llm.bind(...)`) without any real network call.

    A LangChain prompt's `__or__` coerces its right-hand side into a Runnable;
    a plain callable is one of the types LangChain accepts for that, which is
    what makes this work without subclassing any LangChain base class.
    """

    def __init__(self, message_factory: Optional[Callable[[Optional[int]], FakeMessage]] = None, outline=None):
        self.message_factory = message_factory or (lambda _mt: FakeMessage("generated text"))
        self.outline = outline
        self.bind_calls: list[Optional[int]] = []

    def bind(self, **kwargs):
        max_tokens = kwargs.get("max_tokens")
        self.bind_calls.append(max_tokens)
        return _BoundFake(self, max_tokens)


@pytest.fixture
def fake_llm_factory():
    return FakeLLM


@pytest.fixture
def sample_article_markdown() -> str:
    return (FIXTURES_DIR / "sample_article.md").read_text()
