
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

from backend import llm_chains as chains
from backend.config import config
from backend.export import markdown_to_docx_bytes
from backend.store import ArticleMeta, ArticleStore, new_id, now_iso

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("article_generator")

app = Flask(__name__)
store = ArticleStore(config.articles_dir)
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="article-worker")

MIN_WORD_COUNT = 300
MAX_WORD_COUNT = 5000


@app.route("/")
def index():
    has_demo = any(a.get("source") == "demo" for a in store.list_articles())
    return render_template("setup.html", config=config, has_demo=has_demo, active_page="setup")


@app.route("/api/generate", methods=["POST"])
def api_generate():

    payload = request.get_json(force=True, silent=True) or {}
    topic = (payload.get("topic") or "").strip()[:300]
    audience = (payload.get("audience") or "").strip()[:200]
    tone = payload.get("tone") or "formal"
    model = payload.get("model") or chains.SUPPORTED_MODELS[0]

    if not topic:
        return jsonify({"error": "Please enter a topic."}), 400
    if not audience:
        return jsonify({"error": "Please enter a target audience."}), 400
    if tone not in ("formal", "casual", "technical"):
        return jsonify({"error": "Invalid tone."}), 400
    if model not in chains.SUPPORTED_MODELS:
        return jsonify({"error": "Invalid model."}), 400

    try:
        word_count = int(payload.get("word_count") or 900)
    except (TypeError, ValueError):
        word_count = 900
    word_count = max(MIN_WORD_COUNT, min(word_count, MAX_WORD_COUNT))

    try:
        temperature = float(payload.get("temperature", 0.7))
    except (TypeError, ValueError):
        temperature = 0.7
    temperature = max(0.0, min(temperature, 1.5))

    article_id = new_id()
    meta = ArticleMeta(
        id=article_id,
        topic=topic,
        tone=tone,
        audience=audience,
        word_count=word_count,
        model=model,
        created_at=now_iso(),
        status="generating",
        source="live",
        current_step="Starting...",
    )
    store.create(meta)

    executor.submit(_run_pipeline, article_id, topic, tone, audience, word_count, model, temperature)

    return jsonify({"article_id": article_id})


@app.route("/generate/<article_id>")
def generate_page(article_id):
    meta = store.get_meta(article_id)
    if not meta:
        return redirect(url_for("index"))
    if meta["status"] == "ready":
        return redirect(url_for("article", article_id=article_id))
    return render_template("generating.html", article=meta, active_page="setup")


@app.route("/api/status/<article_id>")
def api_status(article_id):
    meta = store.get_meta(article_id)
    if not meta:
        return jsonify({"error": "Unknown article"}), 404
    return jsonify(meta)


@app.route("/article/<article_id>")
def article(article_id):
    meta = store.get_meta(article_id)
    if not meta:
        return redirect(url_for("index"))
    if meta["status"] != "ready":
        return redirect(url_for("generate_page", article_id=article_id))
    markdown_text = store.load_markdown(article_id) or ""
    return render_template("article.html", article=meta, markdown_text=markdown_text, active_page="article")


@app.route("/history")
def history():
    return render_template("history.html", articles=store.list_articles(), active_page="history")


@app.route("/demo")
def demo():
    demo_articles = [a for a in store.list_articles() if a.get("source") == "demo"]
    if not demo_articles:
        return redirect(url_for("index"))
    return redirect(url_for("article", article_id=demo_articles[0]["id"]))


@app.route("/api/export/<article_id>/md")
def export_markdown(article_id):
    markdown_text = store.load_markdown(article_id)
    if markdown_text is None:
        return jsonify({"error": "Article not available yet"}), 404
    meta = store.get_meta(article_id) or {}
    filename = _safe_filename(meta.get("title") or meta.get("topic") or "article") + ".md"
    return Response(
        markdown_text,
        mimetype="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/export/<article_id>/docx")
def export_docx(article_id):
    markdown_text = store.load_markdown(article_id)
    if markdown_text is None:
        return jsonify({"error": "Article not available yet"}), 404
    meta = store.get_meta(article_id) or {}
    filename = _safe_filename(meta.get("title") or meta.get("topic") or "article") + ".docx"
    docx_bytes = markdown_to_docx_bytes(markdown_text)
    return Response(
        docx_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _safe_filename(text: str) -> str:
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in text).strip("_")
    return safe or "article"

def _run_pipeline(article_id: str, topic: str, tone: str, audience: str, word_count: int, model: str, temperature: float) -> None:
    started = time.monotonic()
    try:
        llm = chains.get_llm(model=model, temperature=temperature, api_key=config.openai_api_key)

        store.update(article_id, current_step="Generating outline...", step_index=0, step_total=0)
        outline = chains.generate_outline(llm, topic, tone, audience, word_count)
        store.save_outline(article_id, outline.model_dump())

        total_sections = len(outline.sections)
        step_total = total_sections + 4  # intro + sections + conclusion + polish + further reading
        store.update(article_id, title=outline.title, step_index=1, step_total=step_total, current_step="Writing introduction...")

        intro_budget, section_budget, conclusion_budget = chains.compute_word_budgets(word_count, total_sections)

        intro_result = chains.write_intro(llm, outline, tone, audience, intro_budget)
        previous_context = intro_result.text

        section_texts: list[str] = []
        for i, section in enumerate(outline.sections, start=1):
            store.update(
                article_id,
                step_index=1 + i,
                current_step=f"Writing section {i} of {total_sections}: {section.heading}",
            )
            section_result = chains.write_section(
                llm, outline, section, i, total_sections,
                chains.trim_context(previous_context), tone, audience, section_budget,
            )
            section_texts.append(section_result.text)
            previous_context = previous_context + "\n\n" + section_result.text

        store.update(article_id, step_index=2 + total_sections, current_step="Writing conclusion...")
        conclusion_result = chains.write_conclusion(
            llm, outline, chains.trim_context(previous_context), tone, audience, conclusion_budget
        )

        draft = chains.assemble_draft(outline, intro_result.text, section_texts, conclusion_result.text)

        store.update(article_id, step_index=3 + total_sections, current_step="Polishing for coherence and tone...")
        polish_result = chains.polish_article(llm, draft, tone, audience)

        final_article = polish_result.text

        store.update(article_id, step_index=4 + total_sections, current_step="Suggesting further reading...")
        try:
            further_reading_result = chains.generate_further_reading(llm, outline, topic, tone, audience)
            if further_reading_result.text:
                final_article = final_article.rstrip() + "\n\n" + further_reading_result.text.strip() + "\n"
        except chains.ArticleGenerationError:
            logger.warning("Further-reading suggestions failed for article %s; continuing without them", article_id)

        store.save_markdown(article_id, final_article)
        store.update(
            article_id,
            status="ready",
            current_step="Done",
            step_index=step_total,
            generated_words=chains.count_words(final_article),
            generation_seconds=round(time.monotonic() - started, 1),
            polish_skipped=polish_result.truncated,
        )
        logger.info("Article %s ready (%d words, %.1fs)", article_id, chains.count_words(final_article), time.monotonic() - started)

    except chains.ArticleGenerationError as exc:
        store.update(article_id, status="error", error=str(exc))
    except Exception as exc:  # noqa: BLE001 -- background worker must never crash silently
        logger.exception("Unhandled failure generating article %s", article_id)
        store.update(article_id, status="error", error=f"Unexpected error: {exc}")


def _ensure_demo_seeded() -> None:
    from scripts.seed_demo_data import seed_if_needed
    try:
        seeded = seed_if_needed(store)
        if seeded:
            logger.info("Seeded %d demo article(s)", seeded)
    except Exception:
        logger.exception("Demo data seeding skipped due to an error")


if __name__ == "__main__":
    _ensure_demo_seeded()

    if not config.openai_configured:
        logger.warning("OPENAI_API_KEY is not set -- live generation is disabled until you add it to .env.")

    logger.info("AI Article Generator running at http://%s:%s", config.host, config.port)
    app.run(host=config.host, port=config.port, debug=False, threaded=True)
