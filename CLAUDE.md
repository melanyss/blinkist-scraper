# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Python CLI tool that scrapes book summaries and audio from Blinkist using Selenium 4 (with selenium-wire for request interception) and generates HTML, EPUB, Markdown, and PDF output files. Requires Google Chrome installed; chromedriver is auto-installed.

## Running the Scraper

```bash
# Install dependencies
pip install -r requirements.txt

# Credentials: use .env file (recommended), env vars, or CLI flags
cp .env.example .env  # then edit with your credentials

# Basic usage (requires Blinkist premium account)
python blinkistscraper --email you@example.com --password yourpass
# or with .env / BLINKIST_EMAIL + BLINKIST_PASSWORD env vars:
python blinkistscraper

# Scrape a single book
python blinkistscraper --book https://www.blinkist.com/en/books/SLUG

# Scrape free daily book (no premium needed)
python blinkistscraper --daily-book

# Process existing JSON dumps without scraping
python blinkistscraper --no-scrape

# With audio download and concatenation (requires ffmpeg)
python blinkistscraper --audio --concat-audio

# Markdown output (ideal for AI/LLM consumption)
python blinkistscraper --no-scrape --create-markdown

# PDF generation (weasyprint preferred, wkhtmltopdf fallback)
python blinkistscraper --create-pdf
```

## Regenerating requirements.txt

When `pyproject.toml` dependencies change, run `python pyproject_parse.py` to regenerate `requirements.txt` from the Poetry caret version constraints.

## Architecture

The entry point is `blinkistscraper/__main__.py` which orchestrates the entire pipeline:

- **`scraper.py`** — Selenium 4/selenium-wire browser automation: login (with cookie persistence via JSON), category/book discovery, book metadata scraping (from Blinkist API v4), audio downloading (intercepting audio endpoint requests via selenium-wire, then using urllib to fetch chapter audio files as `.m4a`)
- **`generator.py`** — Output file generation: HTML (via template substitution from `templates/`), EPUB (via ebooklib), Markdown (with HTML-to-markdown conversion), PDF (via weasyprint or wkhtmltopdf fallback), and audio concatenation/tagging (via ffmpeg subprocess)
- **`utils.py`** — Path/filename helpers: sanitizes names, builds file paths under `books/{category}/{author} - {title}/`, reads JSON dumps
- **`logger.py`** — Colored logging setup using colorama

## Key Data Flow

1. Scraper logs into Blinkist, discovers books (by category, URL list, or single URL)
2. For each book: fetches metadata from `api.blinkist.com/v4/books/{id}`, scrapes chapter content from reader page, dumps JSON to `dump/` folder
3. Optionally downloads audio by intercepting the first audio request's headers and replaying them for each chapter
4. Generator produces output files from book JSON + HTML templates (`templates/book.html`, `templates/chapter.html`) using `{key}` token replacement

## Output Directories (gitignored)

- `books/` — Generated output files organized by `{category}/{author} - {title}/`
- `dump/` — Raw JSON metadata per book (`{slug}.json`)
- `logs/` — ChromeDriver logs

## Dependencies

- Python ^3.9
- `selenium` ^4.15 + `selenium-wire` ^5.1 — browser automation and request interception
- `chromedriver-autoinstaller` — auto-downloads matching ChromeDriver
- `ebooklib` — EPUB generation
- `requests` — HTTP requests for cover images and API calls
- `python-dotenv` — loads credentials from `.env` files
- `colorama` — colored terminal output
- `weasyprint` (optional) — PDF generation (install with `pip install weasyprint`)
- Dev: `black` (formatter), `rope` (refactoring)

## Conventions

- Modules use relative imports without package prefix (e.g., `import scraper`, not `from blinkistscraper import scraper`)
- Book metadata is passed around as plain dicts (from JSON API response), not dataclasses
- File paths longer than 260 chars get Windows long-path prefix (`\\?\`)
- The `bin/ublock/` directory contains a pre-packaged uBlock Chrome extension for ad blocking during scraping
