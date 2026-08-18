import dataclasses
import time

import pytest

import app as app_module
from backend.store import ArticleStore
from scripts.seed_demo_data import seed_if_needed
from tests.conftest import FakeLLM, FakeMessage


@pytest.fixture
def client():
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


@pytest.fixture
def unconfigured_openai(monkeypatch):
    fake_config = dataclasses.replace(app_module.config, openai_api_key="")
    monkeypatch.setattr(app_module, "config", fake_config)
    return fake_config


@pytest.fixture
def seeded_demo_article(tmp_path, monkeypatch):
    temp_store = ArticleStore(tmp_path / "articles")
    monkeypatch.setattr(app_module, "store", temp_store)
    seeded = seed_if_needed(temp_store)
    assert seeded == 1
    article_id = temp_store.list_articles()[0]["id"]
    return article_id


def test_index_page_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"AI Article Generator" in resp.data


def test_history_page_loads_empty(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "store", ArticleStore(tmp_path / "articles"))
    resp = client.get("/history")
    assert resp.status_code == 200
    assert b"No articles yet" in resp.data


def test_unknown_article_redirects_to_index(client):
    assert client.get("/article/does-not-exist").status_code == 302
    assert client.get("/generate/does-not-exist").status_code == 302


def test_api_generate_without_openai_config_returns_400(client, unconfigured_openai):
    resp = client.post("/api/generate", json={"topic": "AI", "audience": "engineers"})
    assert resp.status_code == 400
    assert "OpenAI isn't configured" in resp.get_json()["error"]


def test_api_generate_validates_required_fields(client, monkeypatch):
    fake_config = dataclasses.replace(app_module.config, openai_api_key="sk-test")
    monkeypatch.setattr(app_module, "config", fake_config)
    resp = client.post("/api/generate", json={"topic": "", "audience": "engineers"})
    assert resp.status_code == 400
    assert "topic" in resp.get_json()["error"].lower()


def test_article_renders_for_seeded_demo(client, seeded_demo_article):
    resp = client.get(f"/article/{seeded_demo_article}")
    assert resp.status_code == 200
    assert b"article-prose" in resp.data
    assert b"Sample Article" in resp.data


def test_history_lists_seeded_demo(client, seeded_demo_article):
    resp = client.get("/history")
    assert resp.status_code == 200
    assert b"Edge AI" in resp.data


def test_export_markdown(client, seeded_demo_article):
    resp = client.get(f"/api/export/{seeded_demo_article}/md")
    assert resp.status_code == 200
    assert resp.data.startswith(b"# The Quiet Rise of Edge AI")


def test_export_docx(client, seeded_demo_article):
    resp = client.get(f"/api/export/{seeded_demo_article}/docx")
    assert resp.status_code == 200
    assert resp.mimetype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_demo_route_redirects_to_the_demo_article(client, seeded_demo_article):
    resp = client.get("/demo")
    assert resp.status_code == 302
    assert f"/article/{seeded_demo_article}" in resp.headers["Location"]


def test_full_pipeline_runs_end_to_end_with_a_fake_llm(client, monkeypatch, tmp_path):
    """Drives /api/generate through the real background pipeline -- real
    budget math, real outline validation, real assembly -- with only the
    network-calling LLM itself swapped for a deterministic fake.
    """
    temp_store = ArticleStore(tmp_path / "articles")
    monkeypatch.setattr(app_module, "store", temp_store)
    monkeypatch.setattr(app_module, "config", dataclasses.replace(app_module.config, openai_api_key="sk-test"))

    outline = app_module.chains.ArticleOutline(
        title="A Fully Fake Article",
        intro_points=["point a", "point b"],
        sections=[
            app_module.chains.OutlineSection(heading="First Section", key_points=["a", "b", "c"]),
            app_module.chains.OutlineSection(heading="Second Section", key_points=["a", "b", "c"]),
            app_module.chains.OutlineSection(heading="Third Section", key_points=["a", "b", "c"]),
        ],
        conclusion_points=["wrap up"],
    )
    fake_llm = FakeLLM(
        outline=outline,
        message_factory=lambda mt: FakeMessage("Generated paragraph text. " * 20, finish_reason="stop"),
    )
    monkeypatch.setattr(app_module.chains, "get_llm", lambda **kwargs: fake_llm)

    resp = client.post(
        "/api/generate",
        json={"topic": "Testing pipelines", "audience": "engineers", "tone": "formal", "word_count": 500, "model": "gpt-4o-mini"},
    )
    assert resp.status_code == 200
    article_id = resp.get_json()["article_id"]

    deadline = time.monotonic() + 5
    meta = temp_store.get_meta(article_id)
    while meta["status"] == "generating" and time.monotonic() < deadline:
        time.sleep(0.05)
        meta = temp_store.get_meta(article_id)

    assert meta["status"] == "ready", meta.get("error")
    assert meta["title"] == "A Fully Fake Article"
    assert meta["generated_words"] > 0

    # The final saved text is whatever the (fake) polish step returned --
    # by design, polish always has the last word on the saved output, same
    # as the real pipeline. What this test actually verifies is that the
    # outline/section/assembly plumbing ran for real: the captured title
    # came from the real outline object, and every step's fake response
    # made it into the pipeline (evidenced by the word count and the step
    # count below), not that a content-blind fake preserved structure a
    # real, prompt-following LLM would.
    markdown_text = temp_store.load_markdown(article_id)
    assert "Generated paragraph text." in markdown_text
    # outline + intro + 3 sections + conclusion + polish = 7 LLM calls minimum
    assert len(fake_llm.bind_calls) >= 7

