# jina-skills

Jina AI for coding agents, as three skills and one dependency-free Python client.

A stand-in for the [Jina MCP server](https://github.com/jina-ai/MCP): same endpoints, same models, no server to run. An MCP server puts its tool list in the agent's context in every session; a skill costs one line of description until the agent needs it.

| Skill | Use it to | Replaces (Jina MCP tools) |
|---|---|---|
| `jina-read` | read a page or PDF as markdown, or only the passage answering a question; OCR, screenshots, publication dates, figures and tables of a PDF | `read_url`, `capture_screenshot_url`, `guess_datetime_url`, `extract_pdf` |
| `jina-search` | search the web, arXiv, SSRN, images | `search_web`, `search_arxiv`, `search_ssrn`, `search_images`, `search_jina_blog` |
| `jina-rank` | sort texts by relevance to a query, drop near-duplicates | `sort_by_relevance`, `deduplicate_strings` |

## Install

```sh
npx skills add devohmycode/jina-skills -g          # all three, for your agents
npx skills add devohmycode/jina-skills -g --skill jina-read
```

From a local copy, give a path: `npx skills add ./jina-skills -g`. A bare `npx skills add jina-skills` is read as a git source: it works from the parent folder once the folder is a git repository, and installs the last commit, not uncommitted changes.

Each skill carries its own copy of the client (`scripts/jina_api.py`), so any one of them installs and runs alone. Requirements: Python 3.9+ and network access. Nothing to `pip install`; numpy, when present, speeds up `dedupe`.

## API key

Get a key at [jina.ai](https://jina.ai), then either set `JINA_API_KEY`, or store it once:

```sh
python ~/.agents/skills/jina-read/scripts/jina_api.py key
```

It asks with a hidden prompt (or reads stdin when piped) and writes `~/.config/jina/api_key`. The key never goes on the command line.

## Optional: a `jina-api` command

```sh
python ~/.agents/skills/jina-read/scripts/jina_api.py install
```

writes `jina-api` into `~/.local/bin` (plus `jina-api.cmd` on Windows), pointing at the installed script. The skills call `jina-api` directly when it is on PATH, and `python <skill>/scripts/jina_api.py` otherwise.

## Commands

| Command | Does |
|---|---|
| `jina-api read URL [URL...] [-q "question"]` | page or PDF to markdown; with `-q`, only the best passages |
| `jina-api search / arxiv / ssrn "query" [...]` | search; several queries run in parallel |
| `jina-api images "query"` | image search, `--download DIR` to save them |
| `jina-api rerank "query" docs...` | sort documents by relevance (`--file`, stdin, JSON array) |
| `jina-api dedupe strings...` | keep the semantically distinct strings |
| `jina-api screenshot URL` | PNG of the first screen, `--full` for the whole page |
| `jina-api pdf --arxiv ID` | figures, tables, equations as PNG files |
| `jina-api date URL` | publication and modification dates, with their source |
| `jina-api key`, `jina-api install` | setup, see above |

`jina-api <command> -h` lists every option.

## How it differs from the MCP server

- **Files, not base64.** Images, screenshots and PDF figures are saved as files and their paths printed. Nothing big lands in the agent's context unasked.
- **Compact text output.** `--json` gives the raw fields.
- **Full arXiv records.** The search engine cuts titles; `arxiv` completes titles, dates and authors from Semantic Scholar, then from the arXiv abstract pages.
- **`date` is lighter** than `guess_datetime_url`: JSON-LD, meta tags, `<time>` tags and the HTTP header, without feeds, sitemaps or git metadata.
- **Dropped:** `primer` (the agent already knows the date). `search_jina_blog` needs a server-side key; use `search "site:jina.ai/news ..."`.

## Development

```sh
python tools/sync_scripts.py            # after editing src/jina_api.py: refresh the copies in skills/*/scripts/
python -m unittest discover -s tests    # offline tests; they also fail when a copy is stale
```

Edit `src/jina_api.py`, never a copy. CI runs both commands on Ubuntu and Windows with Python 3.9 and 3.13.

## License

Apache-2.0. Derived from [jina-ai/MCP](https://github.com/jina-ai/MCP) (Apache-2.0); see [NOTICE](NOTICE). Not affiliated with Jina AI.
