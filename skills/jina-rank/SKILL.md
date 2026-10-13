---
name: jina-rank
description: Use when a list of texts (search results, passages, candidate records, titles) must be sorted by relevance to a query, or when near-duplicates must be dropped from a list of strings, with Jina Reranker or Jina embeddings. Replaces the Jina MCP tools sort_by_relevance and deduplicate_strings.
license: Apache-2.0
compatibility: Python 3.9+ (standard library only; numpy speeds up dedupe), network access, a Jina AI API key.
---

# Jina Rank

## Running it

The client is `scripts/jina_api.py` in this skill's directory; every jina-* skill ships the same script, so any copy runs every command. In the commands below, `jina-api` stands for `python <this skill's directory>/scripts/jina_api.py`: `python3` on macOS and Linux; on Windows, `python`, with the path written in forward slashes (`C:/Users/...`). When `command -v jina-api` finds a launcher, call `jina-api` as written.

It reads the key from `$JINA_API_KEY`, else `~/.config/jina/api_key`. With no key, ask the user to get one at https://jina.ai and run `jina-api key` (hidden prompt; never put the key on the command line).

## Pick the command

| Need | Command |
|---|---|
| Sort by relevance | `jina-api rerank "query" "doc 1" "doc 2" "doc 3"` |
| Many documents | `jina-api rerank "query" --file docs.txt` (one per line), or pipe a JSON array |
| Keep the best N | add `--top 5` |
| Drop near-duplicates | `jina-api dedupe --file list.txt` (count chosen automatically) |
| Keep exactly K distinct items | `jina-api dedupe --file list.txt -k 10` |

`--json` gives machine-readable output, `-o FILE` writes it to a file.

## Output

```
rerank:  1. [0] 0.321  text...     rank, [original index], score, text
dedupe:  kept 4 of 5               then [original index] text, in selection order
```

## Reading the numbers

- Rerank scores are relative and can be negative: use the order, never a fixed threshold.
- One rerank call takes up to 1024 documents (model jina-reranker-v3.5).
- Dedupe's automatic count is conservative: it keeps paraphrases that are not very close. Pass `-k` when you know how many you want.
- To get the passage of a page that answers a question, do not chunk and rerank yourself: `jina-api read URL -q "question"` (jina-read skill) does it in one call.
