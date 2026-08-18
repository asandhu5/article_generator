# AI Article Generator

Turns a topic, tone, and target audience into a complete, structured, polished
article -- using a five-step LangChain pipeline instead of one giant
completion, so the result reads like a planned piece of writing rather than a
single unstructured wall of text.

<p>
  <img alt="status" src="https://img.shields.io/badge/status-working-34d399">
  <img alt="python" src="https://img.shields.io/badge/python-3.11-6d8cff">
</p>

## What it does

1. **Outline** -- GPT plans a title, intro, 3-5 non-overlapping body sections, and a conclusion as structured data (not prose), so the shape of the article is decided before any of it is written.
2. **Write** -- the introduction is written first, then each section in order, each one given everything written before it as context so later sections don't repeat earlier ones.
3. **Polish** -- a final editorial pass smooths transitions, enforces one consistent tone, and removes repetition across the whole draft.
4. **Read & export** -- the finished article renders in a clean reading view with a table of contents, and downloads as Markdown or a real `.docx` (proper headings, nested bold/italic, tables, code blocks, and clickable hyperlinks -- not just plain text dumped into a Word file).

You watch real progress as it happens -- "Writing section 2 of 4: ..." -- because each step is a separate, visible call, not one opaque black box.

## Quickstart

```bash
source .venv/bin/activate
python app.py
```

Open **http://127.0.0.1:5050** (not 5000 -- see Troubleshooting). The first
run seeds one hand-written sample article into your history, so click **"View
a Sample Article Instead"** to see the reading view and exports immediately,
no API key needed.

To generate real articles, add your key to `.env`:

### Getting an OpenAI API key

1. Sign up / log in at **https://platform.openai.com**.
2. Go to **https://platform.openai.com/api-keys** → "Create new secret key".
3. Copy it into `OPENAI_API_KEY` in `.env`.
4. You'll need billing set up on the account (Settings → Billing) -- article generation isn't available on OpenAI's free tier. Costs are small: a ~900-word article with `gpt-4o-mini` (the default) typically costs well under a cent in API usage.

**Never commit `.env` or paste your key anywhere else.** `.gitignore` already excludes it.

## Architecture

```
app.py                   Flask routes + background generation worker
backend/
  config.py               .env loading & validation
  llm_chains.py             The 5-step LangChain pipeline (outline/intro/section/conclusion/polish)
  export.py                  Markdown -> DOCX via a real markdown-it-py token walk
  store.py                    JSON-file article persistence (no DB needed)
templates/                Jinja2 pages (setup, generating, article, history)
static/                  CSS + vanilla JS (marked.js via CDN for the reading view)
scripts/seed_demo_data.py   Seeds one sample article from tests/fixtures/
tests/                   pytest suite
```

**Progress, for real**: unlike a single blocking API call, this pipeline runs
in a background thread and reports its *actual* current step ("Writing
section 2 of 4: Why the Cloud Stopped Being Enough") to the browser via
polling -- not a fake animated spinner standing in for one opaque call.

## Running tests

```bash
source .venv/bin/activate
pytest -v
```

Fully offline -- the OpenAI client is mocked throughout, and the DOCX export
tests round-trip real Markdown through the converter and inspect the
resulting document structure (paragraph styles, table cells, hyperlink XML),
not just "did it crash."

## What changed from the original version

This replaces an earlier Streamlit version of the same idea. Concretely fixed:

- **The polish step could silently truncate long articles.** It reused the same fixed token budget as every other (much smaller) generation step, so a full-length draft being re-emitted in one pass would get cut off mid-sentence with no warning, no error -- just a chopped article. Now every step gets a budget sized to what it's actually generating, the polish step detects truncation via the API's own `finish_reason`, retries once at the model's real output ceiling, and falls back to showing you the complete (if unpolished) draft rather than a silently truncated one.
- **The Markdown → DOCX converter was a hand-rolled regex line parser** with no support for code blocks, links, blockquotes, tables, or correctly nested bold/italic. Replaced with a real walk over `markdown-it-py`'s parse tree (a spec-compliant CommonMark/GFM parser), so all of that now round-trips correctly into Word -- including real clickable hyperlinks, not plain text.
- **Word-count budgets used flat floors** (e.g. a fixed 80-word section minimum) that structurally overshot short requested lengths. Floors now scale with the requested total.
- **Progress was step-granularity at best**, with four blocking `chain.invoke()` calls and no visibility into what was actually happening. The app now runs generation in a background thread and reports its real current step to the browser as it happens.
- **Failures were swallowed into a generic string with no server-side record.** Every failure path now logs before converting to a user-facing message.
- Small thing: `requirements.txt` no longer lists dependencies the code doesn't import.

## Troubleshooting

- **Can't reach the app / port already in use** -- this app defaults to port `5050` specifically to dodge macOS's AirPlay Receiver, which silently claims port 5000 on most Macs. If `5050` is also taken, set a different `PORT` in `.env`.
- **"OpenAI isn't configured yet" banner** -- `OPENAI_API_KEY` is missing from `.env`, or the app was started before you added it (restart after editing `.env`).
- **401 / authentication error** -- the key is wrong, revoked, or the account has no billing configured.
- **"Generation failed" with a rate-limit message** -- you've hit OpenAI's rate limit or run out of quota; wait a moment or check billing at platform.openai.com.
- **The polish-skipped banner shows up** -- this means your requested word count was long enough that even a generous retry budget couldn't get a full re-polish through in one completion. The article itself is complete either way; try a shorter word count if you want the polished pass.
