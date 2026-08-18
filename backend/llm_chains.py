
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIError, AuthenticationError, RateLimitError
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SUPPORTED_MODELS = ["gpt-4o-mini", "gpt-4o"]
MIN_SECTIONS = 3
MAX_SECTIONS = 5

MAX_OUTPUT_TOKENS = 16384


class ArticleGenerationError(Exception):
    pass


class OutlineSection(BaseModel):
    heading: str = Field(description="The heading/title of this section")
    key_points: List[str] = Field(description="3 to 5 bullet points describing what this section must cover")


class ArticleOutline(BaseModel):
    title: str = Field(description="A compelling, specific title for the article")
    intro_points: List[str] = Field(description="2 to 4 bullet points describing what the introduction must cover")
    sections: List[OutlineSection] = Field(description="Between 3 and 5 main body sections of the article, in logical order")
    conclusion_points: List[str] = Field(description="2 to 4 bullet points describing what the conclusion must cover")


@dataclass
class GenerationResult:
    text: str
    truncated: bool


def _handle_api_errors(func: Callable) -> Callable:
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except AuthenticationError as exc:
            logger.warning("OpenAI authentication failed: %s", exc)
            raise ArticleGenerationError(
                "Authentication with OpenAI failed. Check that OPENAI_API_KEY in your .env file is correct and active."
            ) from exc
        except RateLimitError as exc:
            logger.warning("OpenAI rate limit/quota exceeded: %s", exc)
            raise ArticleGenerationError(
                "OpenAI rate limit or quota was exceeded. Wait a moment and try again, or check your plan and billing details."
            ) from exc
        except APIConnectionError as exc:
            logger.warning("Could not reach OpenAI: %s", exc)
            raise ArticleGenerationError("Could not reach the OpenAI API. Check your internet connection and try again.") from exc
        except APIError as exc:
            logger.exception("OpenAI API error")
            raise ArticleGenerationError(f"OpenAI API returned an error: {exc}") from exc
        except ArticleGenerationError:
            raise
        except Exception as exc:  # noqa: BLE001 -- last resort, but always logged first
            logger.exception("Unexpected error during article generation")
            raise ArticleGenerationError(f"Unexpected error during generation: {exc}") from exc

    return wrapper


def get_llm(model: str, temperature: float, api_key: str) -> ChatOpenAI:
    if model not in SUPPORTED_MODELS:
        raise ArticleGenerationError(f"Unsupported model: {model}")
    if not api_key:
        raise ArticleGenerationError("No OpenAI API key configured. Copy .env.example to .env and set OPENAI_API_KEY.")
    return ChatOpenAI(model=model, temperature=temperature, api_key=api_key, timeout=180, max_retries=2)


def compute_word_budgets(word_count: int, num_sections: int) -> tuple[int, int, int]:
    """Split a requested total word count into (intro, per_section, conclusion) budgets.

    Floors scale with the requested total (instead of flat constants) so a
    short 300-word article doesn't get budgets that structurally overshoot
    its own target by 70%+, as the original fixed floors (60/60/80) did.
    """
    intro_budget = max(round(word_count * 0.05), round(word_count * 0.12))
    conclusion_budget = max(round(word_count * 0.05), round(word_count * 0.10))
    remaining = max(word_count - intro_budget - conclusion_budget, num_sections * round(word_count * 0.05))
    per_section_budget = max(round(word_count * 0.05), remaining // num_sections)
    return intro_budget, per_section_budget, conclusion_budget


def count_words(text: str) -> int:
    return len(text.split())


def _tokens_for_words(words: int, margin: int = 200) -> int:
    """A generous words->tokens estimate (English averages ~0.75 words/token)."""
    return min(int(words / 0.75) + margin, MAX_OUTPUT_TOKENS)


def _invoke(llm: ChatOpenAI, prompt: ChatPromptTemplate, variables: dict, max_tokens: int) -> GenerationResult:
    bound_llm = llm.bind(max_tokens=max_tokens)
    chain = prompt | bound_llm
    message = chain.invoke(variables)
    truncated = message.response_metadata.get("finish_reason") == "length"
    if truncated:
        logger.warning("Generation step truncated at max_tokens=%d (prompt vars: %s)", max_tokens, list(variables.keys()))
    return GenerationResult(text=(message.content or "").strip(), truncated=truncated)


OUTLINE_SYSTEM_PROMPT = """You are an expert content strategist who plans well-structured, \
engaging articles. You always produce outlines with a clear narrative arc: an introduction \
that hooks the reader, 3 to 5 body sections that build logically on each other without \
overlapping in content, and a conclusion that ties everything together. Every section's key \
points must be distinct from every other section's key points."""

outline_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", OUTLINE_SYSTEM_PROMPT),
        (
            "human",
            "Topic: {topic}\nTone: {tone}\nTarget audience: {audience}\n"
            "Target total word count: {word_count}\n\n"
            "Create a detailed, non-overlapping outline for this article.",
        ),
    ]
)


@_handle_api_errors
def generate_outline(llm: ChatOpenAI, topic: str, tone: str, audience: str, word_count: int) -> ArticleOutline:
    structured_llm = llm.bind(max_tokens=2000).with_structured_output(ArticleOutline)
    chain = outline_prompt | structured_llm
    outline: ArticleOutline = chain.invoke(
        {"topic": topic, "tone": tone, "audience": audience, "word_count": word_count}
    )
    if not outline.sections or not (MIN_SECTIONS <= len(outline.sections) <= MAX_SECTIONS):
        raise ArticleGenerationError("The model returned an outline with an invalid number of sections. Please try again.")
    return outline


INTRO_SYSTEM_PROMPT = """You are a skilled article writer. You write introductions that hook \
the reader immediately, set clear expectations for the article, and match the requested tone \
and audience precisely. You write only the introduction body text, never a heading."""

intro_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", INTRO_SYSTEM_PROMPT),
        (
            "human",
            "Article title: {title}\nTone: {tone}\nAudience: {audience}\n"
            "The introduction must cover:\n{intro_points}\n\n"
            "Write an engaging introduction of approximately {word_budget} words. "
            "Return only the introduction paragraph(s) as plain Markdown text, with no heading.",
        ),
    ]
)


@_handle_api_errors
def write_intro(llm: ChatOpenAI, outline: ArticleOutline, tone: str, audience: str, word_budget: int) -> GenerationResult:
    return _invoke(
        llm,
        intro_prompt,
        {
            "title": outline.title,
            "tone": tone,
            "audience": audience,
            "intro_points": "\n".join(f"- {p}" for p in outline.intro_points),
            "word_budget": word_budget,
        },
        max_tokens=_tokens_for_words(word_budget),
    )


SECTION_SYSTEM_PROMPT = """You are a skilled long-form article writer. You maintain a \
consistent tone and voice across an entire article, and you never repeat ideas, phrases, or \
facts that have already appeared earlier in the piece. You write flowing, well-organized \
paragraphs, not bullet lists, unless bullet lists are clearly the best format for the content."""

section_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SECTION_SYSTEM_PROMPT),
        (
            "human",
            "Article title: {title}\nOverall tone: {tone}\nTarget audience: {audience}\n\n"
            "This is section {section_number} of {total_sections}, titled \"{heading}\".\n"
            "Key points this section must cover:\n{key_points}\n\n"
            "Here is what has already been written earlier in the article, for context and "
            "continuity only. Do not repeat this content:\n---\n{previous_context}\n---\n\n"
            "Write the full content for this section in Markdown. Start with the heading "
            "\"## {heading}\" followed by approximately {word_budget} words of flowing "
            "paragraphs. Maintain the {tone} tone and do not repeat information already covered above.",
        ),
    ]
)


@_handle_api_errors
def write_section(
    llm: ChatOpenAI,
    outline: ArticleOutline,
    section: OutlineSection,
    section_number: int,
    total_sections: int,
    previous_context: str,
    tone: str,
    audience: str,
    word_budget: int,
) -> GenerationResult:
    return _invoke(
        llm,
        section_prompt,
        {
            "title": outline.title,
            "tone": tone,
            "audience": audience,
            "section_number": section_number,
            "total_sections": total_sections,
            "heading": section.heading,
            "key_points": "\n".join(f"- {p}" for p in section.key_points),
            "previous_context": previous_context or "(nothing yet, this is the first section)",
            "word_budget": word_budget,
        },
        max_tokens=_tokens_for_words(word_budget),
    )


CONCLUSION_SYSTEM_PROMPT = """You are a skilled article writer who crafts conclusions that \
reinforce the article's key ideas in fresh language, never by copying earlier sentences, and \
that leave the reader with a clear, memorable takeaway."""

conclusion_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", CONCLUSION_SYSTEM_PROMPT),
        (
            "human",
            "Article title: {title}\nTone: {tone}\nAudience: {audience}\n"
            "The conclusion must cover:\n{conclusion_points}\n\n"
            "Here is the article written so far, for context only. Do not repeat its "
            "sentences verbatim:\n---\n{previous_context}\n---\n\n"
            "Write a strong conclusion of approximately {word_budget} words. Start with the "
            "heading \"## Conclusion\". Return only Markdown.",
        ),
    ]
)


@_handle_api_errors
def write_conclusion(
    llm: ChatOpenAI, outline: ArticleOutline, previous_context: str, tone: str, audience: str, word_budget: int
) -> GenerationResult:
    return _invoke(
        llm,
        conclusion_prompt,
        {
            "title": outline.title,
            "tone": tone,
            "audience": audience,
            "conclusion_points": "\n".join(f"- {p}" for p in outline.conclusion_points),
            "previous_context": previous_context,
            "word_budget": word_budget,
        },
        max_tokens=_tokens_for_words(word_budget),
    )


POLISH_SYSTEM_PROMPT = """You are a meticulous senior editor. You review full article drafts \
and improve them without changing their structure or meaning. You fix awkward transitions \
between sections, enforce a single consistent tone throughout, remove any repeated ideas, \
facts, or phrases, and tighten wordy sentences. You never remove entire sections or drastically \
shorten the article."""

polish_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", POLISH_SYSTEM_PROMPT),
        (
            "human",
            "Target tone: {tone}\nTarget audience: {audience}\n\n"
            "Here is a full draft article in Markdown:\n---\n{draft}\n---\n\n"
            "Edit this article for coherence, consistent {tone} tone, smooth transitions "
            "between sections, and remove any repeated ideas or phrases. Preserve the "
            "Markdown structure exactly: a single \"#\" title heading and \"##\" section "
            "headings. Return only the final, polished article in Markdown, with no "
            "commentary before or after it.",
        ),
    ]
)


@_handle_api_errors
def polish_article(llm: ChatOpenAI, draft: str, tone: str, audience: str) -> GenerationResult:
    """Polish the full draft. Falls back to the unpolished draft (never truncated,
    since it's just a copy of already-generated text) if even a generous budget
    isn't enough to have the model safely re-emit the whole thing.
    """
    draft_words = count_words(draft)
    budget = _tokens_for_words(draft_words, margin=400)

    result = _invoke(llm, polish_prompt, {"tone": tone, "audience": audience, "draft": draft}, max_tokens=budget)
    if not result.truncated:
        return result

    if budget < MAX_OUTPUT_TOKENS:
        logger.warning("Polish pass truncated at %d tokens; retrying once at the model's output ceiling", budget)
        result = _invoke(llm, polish_prompt, {"tone": tone, "audience": audience, "draft": draft}, max_tokens=MAX_OUTPUT_TOKENS)
        if not result.truncated:
            return result

    logger.warning("Polish pass still truncated at the output ceiling; returning the unpolished draft instead")
    return GenerationResult(text=draft, truncated=True)


def assemble_draft(outline: ArticleOutline, intro: str, section_texts: List[str], conclusion: str) -> str:
    parts = [f"# {outline.title}", "", intro, ""]
    for text in section_texts:
        parts.append(text)
        parts.append("")
    parts.append(conclusion)
    return "\n".join(parts).strip() + "\n"


def trim_context(text: str, max_chars: int = 6000) -> str:
    if len(text) <= max_chars:
        return text
    return "...\n" + text[-max_chars:]
