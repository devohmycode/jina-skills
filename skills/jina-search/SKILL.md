---
name: jina-search
description: Use when searching the web through Jina (s.jina.ai, svip.jina.ai), when looking for arXiv preprints or SSRN working papers, when searching for images, or when results from Jina search are asked for by name. Replaces the Jina MCP tools search_web, search_arxiv, search_ssrn, search_images and search_jina_blog.
license: Apache-2.0
compatibility: Python 3.9+ (standard library only), network access, a Jina AI API key.
---

# Jina Search

## Running it

The client is `scripts/jina_api.py` in this skill's directory; every jina-* skill ships the same script, so any copy runs every command. In the commands below, `jina-api` stands for `python <this skill's directory>/scripts/jina_api.py`: `python3` on macOS and Linux; on Windows, `python`, with the path written in forward slashes (`C:/Users/...`). When `command -v jina-api` finds a launcher, call `jina-api` as written.

It reads the key from `$JINA_API_KEY`, else `~/.config/jina/api_key`. With no key, ask the user to get one at https://jina.ai and run `jina-api key` (hidden prompt; never put the key on the command line).

## Pick the command

| Need | Command |
|---|---|
| Web | `jina-api search "query"` |
| Several queries at once | `jina-api search "q1" "q2" "q3"` (also `arxiv`, `ssrn`; parallel, grouped by query) |
| arXiv preprints | `jina-api arxiv "late chunking"` |
| SSRN working papers | `jina-api ssrn "minimum wage"` |
| Images | `jina-api images "query" -n 5` (`--download DIR` saves them) |
| Jina's own blog | `jina-api search "site:jina.ai/news topic"` |

Options shared by search, arxiv and ssrn:

| Option | Effect |
|---|---|
| `-n 20` | results per query (default 10, max 100) |
| `--since d` | age limit: `h`, `d`, `w`, `m`, `y` |
| `--newest` | arXiv only: sort by date instead of relevance |
| `--gl fr --hl fr` | country and language (web and images) |
| `--location "Lyon, France"` | search from a place (web and images) |
| `--json` | every field (arXiv: `arxiv_id`, `author`, `citations`) |
| `-o FILE` | write the results to a file |

## Output

```
1. Title
   URL
   date: ... · authors (when known)
   snippet (400 chars max)
```

For arXiv, the command already replaces the engine's cut titles with the full title, exact date and authors, and links to the abstract page.

## Know before you trust a result

- These are search-engine snippets, not page content. Check an exact figure, date or quote on the page itself: `jina-api read URL -q "..."` (jina-read skill).
- Web and SSRN titles are often cut by the engine. `jina-api read URL1 URL2 --max-chars 200` returns each page's full `# Title` line for little output.
- `--since` is a hint passed to the engine: arXiv and SSRN can still return older papers.

## Recent papers on a topic

1. Run 2–3 short, focused queries in one call, in relevance order: `jina-api arxiv "RAG historical archives" "RAG genealogy records" --since y`.
2. Keep the hits that are on topic, then order those by date yourself.

`--newest` re-sorts by date and drops the relevance order: with a broad query it surfaces any recent paper that matches one word. Use it only on a query that is already narrow.
