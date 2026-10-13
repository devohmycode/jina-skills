---
name: jina-read
description: Use when reading a web page or PDF through Jina Reader, when only the passage answering one question is needed from a long page, or when a page needs OCR, a screenshot, its publication date, or its figures and tables extracted. Replaces the Jina MCP tools read_url, capture_screenshot_url, guess_datetime_url and extract_pdf.
license: Apache-2.0
compatibility: Python 3.9+ (standard library only), network access, a Jina AI API key.
---

# Jina Reader

## Running it

The client is `scripts/jina_api.py` in this skill's directory; every jina-* skill ships the same script, so any copy runs every command. In the commands below, `jina-api` stands for `python <this skill's directory>/scripts/jina_api.py`: `python3` on macOS and Linux; on Windows, `python`, with the path written in forward slashes (`C:/Users/...`). When `command -v jina-api` finds a launcher, call `jina-api` as written.

It reads the key from `$JINA_API_KEY`, else `~/.config/jina/api_key`. With no key, ask the user to get one at https://jina.ai and run `jina-api key` (hidden prompt; never put the key on the command line).

## Pick the command

| Need | Command |
|---|---|
| A fact from a page or PDF | `jina-api read URL -q "question"` — returns only the best passage |
| More passages, more context | add `--topk 3`, `--chunk-size 200` (words) |
| Whole page as markdown | `jina-api read URL` (cut at 40 000 chars; `-o page.md` keeps it on disk) |
| Several pages | `jina-api read URL1 URL2 URL3` (parallel; works with `-q` too) |
| One part of the page | `--selector "article"`, `--remove "nav,footer"` |
| Page filled in by JavaScript | `--wait-for ".results"` |
| Links or images on the page | `--links`, `--images` |
| Scan, image-only PDF, formulas | `--ocr --page N` (one page per call, ~40x the cost) |
| Screenshot | `jina-api screenshot URL` prints a PNG path (system temp dir; `-o shot.png` to choose); open it with Read. `--full` = whole page |
| Publication / update date | `jina-api date URL` (`--all` lists every signal) |
| Figures, tables, equations | `jina-api pdf --arxiv 2409.04701` or `--url PDF_URL` (`--type table`): PNG files + captions |

`--json` gives the raw fields. `jina-api read -h` lists every option.

## Reading a page: the order

1. Need a fact? `-q` first: one call, a few hundred characters, instead of a 40 000-char page or a home-made chunk-and-rerank.
2. `-q` strips tables and code blocks before ranking. A figure in a table or a command in a code block needs a plain read, narrowed with `--selector`.
3. If `-q` finds nothing, the full page comes back with a `Note:` line saying so.
4. `--ocr` only after a plain read returned empty or garbled text.

## Output

```
# Title
URL: ...
Published: ...        (when Jina knows it)
Question: ...         (with -q, then the passages)
```

Several URLs are separated by `=====`. A failed URL prints `ERROR <url>: <reason>` and the others still come back; exit code 1 only when every URL failed.

`jina-api date` prints `Published:` and `Modified:` lines, each followed by its source (JSON-LD, meta tag, `<time>` tag or HTTP header). A date taken only from the HTTP header is flagged low confidence: many servers send the time of the request.
