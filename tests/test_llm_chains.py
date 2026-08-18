import pytest

from backend import llm_chains as chains
from tests.conftest import FakeMessage


def test_compute_word_budgets_scale_with_small_totals():
    # The old flat floors (60/60/80) overshot a 300-word request by ~70%.
    intro, section, conclusion = chains.compute_word_budgets(300, 3)
    total_budget = intro + section * 3 + conclusion
    assert total_budget <= 300 * 1.3  # generous slack, but not a 70% overshoot


def test_compute_word_budgets_scale_with_large_totals():
    intro, section, conclusion = chains.compute_word_budgets(5000, 5)
    assert intro > 100
    assert section > 500
    assert conclusion > 100


def test_tokens_for_words_is_capped_at_model_ceiling():
    assert chains._tokens_for_words(100_000) == chains.MAX_OUTPUT_TOKENS


def test_tokens_for_words_scales_with_length():
    small = chains._tokens_for_words(100)
    large = chains._tokens_for_words(2000)
    assert large > small


def test_assemble_draft_produces_expected_structure():
    outline = chains.ArticleOutline(
        title="My Title",
        intro_points=["a"],
        sections=[chains.OutlineSection(heading="H1", key_points=["a", "b", "c"])],
        conclusion_points=["a"],
    )
    draft = chains.assemble_draft(outline, "Intro text.", ["## H1\nSection text."], "## Conclusion\nEnd text.")
    assert draft.startswith("# My Title")
    assert "Intro text." in draft
    assert "## H1" in draft
    assert "## Conclusion" in draft


def test_trim_context_leaves_short_text_untouched():
    assert chains.trim_context("short text") == "short text"


def test_trim_context_truncates_long_text_from_the_start():
    long_text = "x" * 10_000
    trimmed = chains.trim_context(long_text, max_chars=100)
    assert len(trimmed) < len(long_text)
    assert trimmed.endswith("x" * 100)


def test_generate_outline_rejects_too_few_sections(fake_llm_factory):
    outline = chains.ArticleOutline(
        title="T", intro_points=["a"],
        sections=[chains.OutlineSection(heading="H1", key_points=["a", "b"])],
        conclusion_points=["a"],
    )
    llm = fake_llm_factory(outline=outline)
    with pytest.raises(chains.ArticleGenerationError):
        chains.generate_outline(llm, "topic", "formal", "devs", 900)


def test_generate_outline_accepts_valid_section_count(fake_llm_factory):
    outline = chains.ArticleOutline(
        title="T", intro_points=["a", "b"],
        sections=[chains.OutlineSection(heading=f"H{i}", key_points=["a", "b", "c"]) for i in range(4)],
        conclusion_points=["a", "b"],
    )
    llm = fake_llm_factory(outline=outline)
    result = chains.generate_outline(llm, "topic", "formal", "devs", 900)
    assert result.title == "T"
    assert len(result.sections) == 4


def test_write_intro_passes_word_budget_as_token_budget(fake_llm_factory):
    llm = fake_llm_factory(message_factory=lambda mt: FakeMessage(f"intro (budget={mt})"))
    outline = chains.ArticleOutline(title="T", intro_points=["a"], sections=[], conclusion_points=["a"])
    result = chains.write_intro(llm, outline, "formal", "devs", word_budget=100)
    assert result.text == f"intro (budget={chains._tokens_for_words(100)})"
    assert result.truncated is False


def test_write_section_reports_truncation(fake_llm_factory):
    llm = fake_llm_factory(message_factory=lambda mt: FakeMessage("cut off mid-sen", finish_reason="length"))
    outline = chains.ArticleOutline(title="T", intro_points=["a"], sections=[], conclusion_points=["a"])
    section = chains.OutlineSection(heading="H1", key_points=["a"])
    result = chains.write_section(llm, outline, section, 1, 1, "context", "formal", "devs", word_budget=100)
    assert result.truncated is True


def test_polish_article_returns_clean_result_when_not_truncated(fake_llm_factory):
    llm = fake_llm_factory(message_factory=lambda mt: FakeMessage("polished draft", finish_reason="stop"))
    result = chains.polish_article(llm, "# T\n\noriginal draft", "formal", "devs")
    assert result.text == "polished draft"
    assert result.truncated is False
    assert len(llm.bind_calls) == 1  # no retry needed


def test_polish_article_retries_once_then_succeeds(fake_llm_factory):
    responses = iter([
        FakeMessage("cut off...", finish_reason="length"),
        FakeMessage("full polished draft", finish_reason="stop"),
    ])
    llm = fake_llm_factory(message_factory=lambda mt: next(responses))
    result = chains.polish_article(llm, "# T\n\n" + "word " * 50, "formal", "devs")
    assert result.text == "full polished draft"
    assert result.truncated is False
    assert len(llm.bind_calls) == 2
    assert llm.bind_calls[1] == chains.MAX_OUTPUT_TOKENS  # retry used the model's real ceiling


def test_polish_article_falls_back_to_original_draft_if_still_truncated(fake_llm_factory):
    llm = fake_llm_factory(message_factory=lambda mt: FakeMessage("still cut off", finish_reason="length"))
    draft = "# T\n\n" + "word " * 50
    result = chains.polish_article(llm, draft, "formal", "devs")
    assert result.text == draft  # falls back to the complete, unpolished draft
    assert result.truncated is True  # signals "polish was skipped" to the caller


def test_polish_article_skips_retry_when_first_budget_already_at_ceiling(fake_llm_factory, monkeypatch):
    monkeypatch.setattr(chains, "_tokens_for_words", lambda *a, **k: chains.MAX_OUTPUT_TOKENS)
    draft = "# T\n\nshort draft"
    llm = fake_llm_factory(message_factory=lambda mt: FakeMessage("cut off", finish_reason="length"))
    result = chains.polish_article(llm, draft, "formal", "devs")
    assert len(llm.bind_calls) == 1  # already at the ceiling, no point retrying
    assert result.truncated is True
    assert result.text == draft  # must fall back to the safe original, not the truncated text
