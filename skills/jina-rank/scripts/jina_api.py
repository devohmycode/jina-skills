#!/usr/bin/env python3
"""jina-api: command-line access to the Jina AI APIs (Reader, Search, Reranker,
Embeddings, PDF floats). Stands in for the Jina MCP server, with the same
endpoints and defaults (github.com/jina-ai/MCP, Apache-2.0).

Python 3.9+, standard library only (numpy speeds up `dedupe` when present).
API key: $JINA_API_KEY, else the file ~/.config/jina/api_key (`jina-api key`).
Run `jina-api <command> -h` for the options of each command.

Part of jina-skills; the copies under skills/*/scripts/ are generated from
src/jina_api.py by tools/sync_scripts.py: edit the source, not a copy.
"""
import argparse
import base64
import concurrent.futures as cf
import datetime as dt
import email.utils
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

READER_URL = "https://r.jina.ai/"
SVIP_URL = "https://svip.jina.ai/"
PDF_URL = "https://svip.jina.ai/extract-pdf"
API_URL = "https://api.jina.ai/v1"
S2_BATCH_URL = ("https://api.semanticscholar.org/graph/v1/paper/batch"
                "?fields=title,publicationDate,authors")
RERANK_MODEL = "jina-reranker-v3.5"
EMBED_MODEL = "jina-embeddings-v5-text-small"
__version__ = "1.0.0"
USER_AGENT = f"jina-api-cli/{__version__}"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
SINCE = {"h": "qdr:h", "d": "qdr:d", "w": "qdr:w", "m": "qdr:m", "y": "qdr:y"}


# ---------------------------------------------------------------- plumbing

def die(msg):
    print(f"jina-api: {msg}", file=sys.stderr)
    sys.exit(1)


def api_key():
    key = os.environ.get("JINA_API_KEY", "").strip()
    if not key:
        path = Path.home() / ".config" / "jina" / "api_key"
        if path.is_file():
            key = path.read_text(encoding="utf-8").strip()
    if not key:
        die("no API key: set JINA_API_KEY, or store it with `jina-api key` "
            "(get one at https://jina.ai)")
    return key


def post(url, body, headers=None, timeout=30):
    h = {"Accept": "application/json", "Content-Type": "application/json",
         "User-Agent": USER_AGENT, "Authorization": f"Bearer {api_key()}"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400].strip()
        raise RuntimeError(f"HTTP {e.code} from {url}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach {url}: {e.reason}") from None
    except TimeoutError:
        raise RuntimeError(f"no answer from {url} after {timeout}s") from None


def download(url, dest, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        Path(dest).write_bytes(resp.read())
    return dest


def parallel(fn, items, workers=5):
    """Run fn over items concurrently, keep input order, never let one
    failure discard the others."""
    def safe(item):
        try:
            return fn(item)
        except RuntimeError as e:
            return e
    with cf.ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(safe, items))


def read_inputs(args_list, file):
    """Strings from the command line, from a file, or from stdin ('-' or a
    pipe). A JSON array is accepted, otherwise one string per line."""
    if args_list and args_list != ["-"]:
        return args_list
    if file:
        text = Path(file).read_text(encoding="utf-8")
    elif args_list == ["-"] or not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        return []
    text = text.strip()
    if text.startswith("["):
        return [str(s) for s in json.loads(text)]
    return [line for line in text.splitlines() if line.strip()]


def tbs(since):
    if not since:
        return None
    return SINCE.get(since, since)


def emit(text, a):
    """Print, or write to -o FILE and print one line saying where."""
    if getattr(a, "output", None):
        Path(a.output).write_text(text, encoding="utf-8")
        print(f"wrote {len(text)} chars to {a.output}")
    else:
        print(text)


def clip(text, limit):
    if limit and len(text) > limit:
        return (text[:limit] + f"\n\n[truncated at {limit} of {len(text)} chars:"
                " pass --max-chars 0 or -o FILE for the rest]")
    return text


# ---------------------------------------------------------------- read

def read_one(url, a):
    q = (a.question or "").strip()
    note = None
    if q and not a.ocr:
        try:
            d = post(SVIP_URL, {"q": q, "url": url, "topk": a.topk,
                                "chunk_size": a.chunk_size}, timeout=60)
            first = (d.get("results") or [{}])[0]
            if first.get("snippet"):
                return {"url": url, "title": first.get("title"), "question": q,
                        "passages": first["snippet"]}
            note = "no passage matched the question; full page follows"
        except RuntimeError as e:
            note = f"passage extraction failed ({e}); full page follows"

    headers = {}
    if not a.keep_links:
        headers["X-Md-Link-Style"] = "discarded"
    if a.links:
        headers["X-With-Links-Summary"] = "all"
    if a.images:
        headers["X-With-Images-Summary"] = "true"
    else:
        headers["X-Retain-Images"] = "none"
    if a.ocr:
        headers["X-Respond-With"] = "jina-ocr-v1"
        if a.page and a.page > 1:
            headers["X-Page"] = str(a.page)
    if a.format != "markdown":
        headers["X-Return-Format"] = a.format
    if a.selector:
        headers["X-Target-Selector"] = a.selector
    if a.remove:
        headers["X-Remove-Selector"] = a.remove
    if a.wait_for:
        headers["X-Wait-For-Selector"] = a.wait_for
    if a.no_cache:
        headers["X-No-Cache"] = "true"

    d = post(READER_URL, {"url": url}, headers, timeout=120 if a.ocr else 45)
    data = d.get("data") or {}
    out = {"url": data.get("url") or url, "title": data.get("title"),
           "published": data.get("publishedTime"),
           "tokens": (data.get("usage") or {}).get("tokens"),
           "content": data.get("content") or data.get("text") or data.get("html") or ""}
    if note:
        out["note"] = note
    if a.links and data.get("links"):
        links = data["links"]
        out["links"] = (list(links.items()) if isinstance(links, dict)
                        else [tuple(x) for x in links])
    if a.images and data.get("images"):
        out["images"] = data["images"]
    return out


def format_read(r, a):
    lines = [f"# {r.get('title') or '(no title)'}", f"URL: {r['url']}"]
    if r.get("published"):
        lines.append(f"Published: {r['published']}")
    if r.get("note"):
        lines.append(f"Note: {r['note']}")
    if "passages" in r:
        lines += [f"Question: {r['question']}", "", r["passages"]]
        return "\n".join(lines)
    if r.get("tokens"):
        lines.append(f"Tokens: {r['tokens']}")
    lines += ["", clip(r["content"], a.max_chars)]
    if r.get("links"):
        lines += ["", "## Links"] + [f"- {t} <{u}>" for t, u in r["links"]]
    if r.get("images"):
        imgs = r["images"]
        items = imgs.items() if isinstance(imgs, dict) else enumerate(imgs)
        lines += ["", "## Images"] + [f"- {k}: {v}" for k, v in items]
    return "\n".join(lines)


def cmd_read(a):
    urls = list(dict.fromkeys(a.url))
    results = parallel(lambda u: read_one(u, a), urls)
    if a.json:
        emit(json.dumps([r if isinstance(r, dict) else {"error": str(r)}
                         for r in results], ensure_ascii=False, indent=1), a)
        return
    blocks = [format_read(r, a) if isinstance(r, dict) else f"ERROR {u}: {r}"
              for u, r in zip(urls, results)]
    emit("\n\n=====\n\n".join(blocks), a)
    if all(not isinstance(r, dict) for r in results):
        sys.exit(1)


# ---------------------------------------------------------------- search

def search_body(q, a, extra):
    body = {"q": q, "num": a.num}
    if tbs(a.since):
        body["tbs"] = tbs(a.since)
    for k in ("gl", "hl", "location"):
        if getattr(a, k, None):
            body[k] = getattr(a, k)
    body.update(extra)
    return body


def format_hits(query, hits, show_query):
    lines = [f"## {query}"] if show_query else []
    if not hits:
        lines.append("(no results)")
    for i, h in enumerate(hits, 1):
        title = h.get("title") or "(no title)"
        url = h.get("url") or h.get("link") or ""
        lines.append(f"{i}. {title}\n   {url}")
        meta = [str(h[k]) for k in ("date", "publishedTime") if h.get(k)][:1]
        if h.get("author"):
            meta.append(str(h["author"]))
        if meta:
            lines.append("   date: " + " · ".join(meta))
        snippet = h.get("snippet") or h.get("description") or ""
        if snippet:
            lines.append("   " + " ".join(snippet.split())[:400])
    return "\n".join(lines)


def arxiv_id(hit):
    if hit.get("arxiv_id"):
        return re.sub(r"v\d+$", "", str(hit["arxiv_id"]))
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", hit.get("url") or "")
    return m[1] if m else None


def authors(names):
    names = [n for n in names if n]
    return ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")


def arxiv_abs_record(aid):
    """Title, date and authors from the citation_* meta tags of an arXiv
    abstract page."""
    try:
        req = urllib.request.Request(f"https://arxiv.org/abs/{aid}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            page = resp.read(500_000).decode("utf-8", "replace")
    except Exception as e:
        raise RuntimeError(str(e)) from None
    meta = lambda name: [html.unescape(v) for v in
                         re.findall(rf'<meta name="citation_{name}" content="([^"]*)"', page)]
    title = (meta("title") or [""])[0]
    if not title:
        raise RuntimeError(f"no citation_title on arxiv.org/abs/{aid}")
    # citation_author is "Last, First"; Semantic Scholar gives "First Last".
    names = [" ".join(reversed(n.split(", ", 1))) for n in meta("author")]
    return {"title": title, "date": (meta("date") or [""])[0].replace("/", "-"),
            "author": authors(names)}


def complete_arxiv(results, newest):
    """The search engine cuts arXiv titles and often gives only a year. Fill
    in the full title, date and authors from Semantic Scholar (one batch call,
    no key), then from the arXiv abstract pages for whatever it lacks. (The
    arXiv API itself rate-limits hard.)"""
    hits = [h for r in results if isinstance(r, list) for h in r if arxiv_id(h)]
    ids = list(dict.fromkeys(arxiv_id(h) for h in hits))[:100]
    records = {}
    if ids:
        try:
            req = urllib.request.Request(
                S2_BATCH_URL, data=json.dumps({"ids": [f"ARXIV:{i}" for i in ids]}).encode(),
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}, method="POST")
            with urllib.request.urlopen(req, timeout=20) as resp:
                for aid, rec in zip(ids, json.loads(resp.read().decode("utf-8"))):
                    if rec and rec.get("title"):
                        records[aid] = {"title": rec["title"], "date": rec.get("publicationDate") or "",
                                        "author": authors(x.get("name") for x in rec.get("authors") or [])}
        except Exception:
            pass
        missing = [i for i in ids if i not in records]
        if missing:
            for aid, rec in zip(missing, parallel(arxiv_abs_record, missing, workers=3)):
                if isinstance(rec, dict):
                    records[aid] = rec
        if len(records) < len(ids):
            print(f"jina-api: full title not found for {len(ids) - len(records)} of {len(ids)} papers",
                  file=sys.stderr)
        for h in hits:
            rec = records.get(arxiv_id(h))
            if rec:
                h.update(rec, arxiv_id=arxiv_id(h), url=f"https://arxiv.org/abs/{arxiv_id(h)}")
    if newest:
        for r in results:
            if isinstance(r, list):
                r.sort(key=date_key, reverse=True)


def date_key(hit):
    raw = str(hit.get("date") or "")
    d = parse_date(raw)
    if d:
        return d.isoformat()
    m = re.search(r"(?:19|20)\d{2}", raw)
    return m[0] if m else ""


def run_search(a, extra):
    queries = list(dict.fromkeys(a.query))
    results = parallel(lambda q: post(SVIP_URL, search_body(q, a, extra)).get("results") or [],
                       queries)
    if extra.get("domain") == "arxiv":
        complete_arxiv(results, a.newest)
    if a.json:
        emit(json.dumps({q: (r if isinstance(r, list) else {"error": str(r)})
                         for q, r in zip(queries, results)}, ensure_ascii=False, indent=1), a)
        return
    blocks = [format_hits(q, r, len(queries) > 1) if isinstance(r, list)
              else f"ERROR search '{q}': {r}" for q, r in zip(queries, results)]
    emit("\n\n".join(blocks), a)
    if all(not isinstance(r, list) for r in results):
        sys.exit(1)


def cmd_search(a):
    run_search(a, {})


def cmd_arxiv(a):
    run_search(a, {"domain": "arxiv"})


def cmd_ssrn(a):
    run_search(a, {"domain": "ssrn"})


def cmd_images(a):
    try:
        d = post(SVIP_URL, search_body(a.query, a, {"type": "images"}))
    except RuntimeError as e:
        die(str(e))
    hits = (d.get("results") or [])[:a.num]
    if a.json:
        emit(json.dumps(hits, ensure_ascii=False, indent=1), a)
        return
    lines = []
    if a.download:
        Path(a.download).mkdir(parents=True, exist_ok=True)
    for i, h in enumerate(hits, 1):
        img = h.get("imageUrl") or h.get("image") or ""
        lines.append(f"{i}. {h.get('title') or '(no title)'}\n   image: {img}"
                     f"\n   page:  {h.get('url') or h.get('link') or ''}")
        if a.download and img:
            ext = os.path.splitext(urllib.parse.urlparse(img).path)[1][:5] or ".jpg"
            dest = Path(a.download) / f"{i:02d}{ext}"
            try:
                download(img, dest, timeout=15)
                lines.append(f"   saved: {dest}")
            except Exception as e:  # hotlink protection, SVG, timeouts...
                lines.append(f"   not saved: {e}")
    emit("\n".join(lines) or "(no results)", a)


# ---------------------------------------------------------------- rank

def cmd_rerank(a):
    docs = read_inputs(a.documents, a.file)
    if not docs:
        die("no documents: pass them as arguments, with --file, or on stdin")
    try:
        d = post(f"{API_URL}/rerank", {"model": RERANK_MODEL, "query": a.query,
                                       "top_n": a.top or len(docs), "documents": docs})
    except RuntimeError as e:
        die(str(e))
    res = d.get("results") or []
    if a.json:
        emit(json.dumps([{"index": r["index"], "score": r.get("relevance_score"),
                          "text": docs[r["index"]]} for r in res],
                        ensure_ascii=False, indent=1), a)
        return
    emit("\n".join(f"{n}. [{r['index']}] {r.get('relevance_score', 0):.3f}  "
                   f"{' '.join(docs[r['index']].split())[:300]}"
                   for n, r in enumerate(res, 1)), a)


def embed(strings):
    d = post(f"{API_URL}/embeddings", {"model": EMBED_MODEL, "task": "text-matching",
                                       "input": strings}, timeout=90)
    return [item["embedding"] for item in d["data"]]


def select_diverse(vectors, k=None, threshold=1e-2):
    """Greedy facility-location selection over cosine similarity, as in the
    MCP's deduplicate_strings: each pick maximises how much of the whole set
    is covered. Without k, stop when the coverage gain drops below
    `threshold`, and drop the element that produced that small gain."""
    try:
        import numpy as np
    except ImportError:
        np = None
    n = len(vectors)
    if np is not None:
        e = np.asarray(vectors, dtype=float)
        e /= np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-12)
        sim = np.clip(e @ e.T, 0, None)
        cover = np.zeros(n)
        gain_of = lambda: np.maximum(sim, cover).sum(axis=1) - cover.sum()
    else:
        norm = [sum(x * x for x in v) ** 0.5 or 1e-12 for v in vectors]
        sim = [[max(0.0, sum(x * y for x, y in zip(vectors[i], vectors[j])) / (norm[i] * norm[j]))
                for j in range(n)] for i in range(n)]
        cover = [0.0] * n
        gain_of = lambda: [sum(max(s, c) for s, c in zip(row, cover)) - sum(cover) for row in sim]
    picked, values = [], []
    limit = n if k is None else min(k, n)
    while len(picked) < limit:
        gains = list(gain_of())
        for p in picked:
            gains[p] = float("-inf")
        best = max(range(n), key=lambda i: gains[i])
        picked.append(best)
        if np is not None:
            cover = np.maximum(cover, sim[best])
            values.append(float(cover.sum()) / n)
        else:
            cover = [max(c, s) for c, s in zip(cover, sim[best])]
            values.append(sum(cover) / n)
        if k is None and len(values) >= 2 and values[-1] - values[-2] < threshold:
            picked.pop()
            break
    return picked


def cmd_dedupe(a):
    strings = read_inputs(a.strings, a.file)
    if not strings:
        die("no strings: pass them as arguments, with --file, or on stdin")
    if a.k is not None and not 1 <= a.k <= len(strings):
        die(f"-k must be between 1 and {len(strings)}")
    try:
        keep = select_diverse(embed(strings), a.k)
    except RuntimeError as e:
        die(str(e))
    if a.json:
        emit(json.dumps([{"index": i, "text": strings[i]} for i in keep],
                        ensure_ascii=False, indent=1), a)
        return
    emit("\n".join([f"kept {len(keep)} of {len(strings)}"] +
                   [f"[{i}] {strings[i]}" for i in keep]), a)


# ---------------------------------------------------------------- see

def slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:50] or "page"


def cmd_screenshot(a):
    fmt = "pageshot" if a.full else "screenshot"
    try:
        d = post(READER_URL, {"url": a.url}, {"X-Return-Format": fmt}, timeout=90)
    except RuntimeError as e:
        die(str(e))
    data = d.get("data") or {}
    img = data.get("screenshotUrl") or data.get("pageshotUrl")
    if not img:
        die("the API returned no screenshot URL")
    if a.url_only:
        print(img)
        return
    dest = a.output or os.path.join(
        tempfile.gettempdir(), f"jina-{fmt}-{slug(urllib.parse.urlparse(a.url).netloc)}-{int(time.time())}.png")
    try:
        download(img, dest, timeout=60)
    except Exception as e:
        die(f"screenshot taken but not downloaded ({e}); URL: {img}")
    print(dest)


def cmd_pdf(a):
    if not (a.arxiv or a.url):
        die("give --arxiv ID or --url PDF_URL")
    body = {"max_edge": a.max_edge}
    if a.arxiv:
        body["id"] = a.arxiv
    if a.url:
        body["url"] = a.url
    if a.type:
        body["type"] = a.type
    try:
        d = post(PDF_URL, body, timeout=180)
    except RuntimeError as e:
        die(str(e))
    floats = d.get("floats") or []
    meta = d.get("meta") or {}
    out_dir = Path(a.out or os.path.join(tempfile.gettempdir(),
                                         f"jina-pdf-{slug(a.arxiv or a.url)}"))
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"{len(floats)} floats, {meta.get('num_pages', '?')} pages -> {out_dir}"]
    for i, f in enumerate(floats, 1):
        name = f"{i:02d}-{f.get('type', 'float')}-{slug(str(f.get('number', i)))}-p{f.get('page', 0)}.png"
        (out_dir / name).write_bytes(base64.b64decode(f.get("image", "")))
        caption = " ".join(str(f.get("caption") or "").split())[:200]
        lines.append(f"{name}  {f.get('width')}x{f.get('height')}  {caption}")
    print("\n".join(lines))


# ---------------------------------------------------------------- date

PUBLISHED_KEYS = {"article:published_time", "og:published_time", "datepublished",
                  "datecreated", "uploaddate", "pubdate", "publishdate", "publish-date",
                  "date", "dc.date", "dc.date.issued", "dc.date.created", "dcterms.created",
                  "dcterms.issued", "citation_publication_date", "citation_date",
                  "sailthru.date", "parsely-pub-date", "article.published"}
MODIFIED_KEYS = {"article:modified_time", "og:updated_time", "datemodified",
                 "last-modified", "dcterms.modified", "dc.date.modified", "revised",
                 "article.updated", "lastmod"}


def parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            d = None
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y"):
            if d:
                break
            try:
                d = dt.datetime.strptime(value, fmt)
            except ValueError:
                pass
        if d is None:
            m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", value)
            if not m:
                return None
            try:
                d = dt.datetime(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:
                return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    now = dt.datetime.now(dt.timezone.utc)
    return d if dt.datetime(1995, 1, 1, tzinfo=dt.timezone.utc) <= d <= now + dt.timedelta(days=1) else None


def walk_jsonld(node, found):
    if isinstance(node, dict):
        for k, v in node.items():
            if k.lower() in ("datepublished", "datemodified", "datecreated", "uploaddate") and isinstance(v, str):
                found.append((f"json-ld:{k}", k.lower(), v))
            else:
                walk_jsonld(v, found)
    elif isinstance(node, list):
        for item in node:
            walk_jsonld(item, found)


def date_signals(html_text):
    """Every date the page states about itself: (source, key, raw value)."""
    signals = []
    for tag in re.findall(r"<meta\b[^>]*>", html_text, re.I):
        attrs = {k.lower(): v for k, _, v in
                 re.findall(r'([\w:.-]+)\s*=\s*(["\'])(.*?)\2', tag, re.S)}
        key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
               or attrs.get("http-equiv") or "").lower()
        if key in PUBLISHED_KEYS | MODIFIED_KEYS and attrs.get("content"):
            signals.append((f"meta:{key}", key, attrs["content"]))
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
                            html_text, re.I | re.S):
        try:
            walk_jsonld(json.loads(block.strip()), signals)
        except ValueError:
            pass
    for value in re.findall(r'<time\b[^>]*\bdatetime\s*=\s*["\']([^"\']+)', html_text, re.I)[:10]:
        signals.append(("html:<time>", "time", value))
    return signals


def pick_dates(signals):
    """Earliest publication date and latest modification date, each as
    (datetime, source, note). The page's own metadata beats the HTTP header,
    which many servers set to the time of the request."""
    parsed = [(src, key, parse_date(raw)) for src, key, raw in signals]
    parsed = [p for p in parsed if p[2]]
    strong = lambda p: not p[0].startswith(("http:", "html:"))
    pub = [p for p in parsed if p[1] in PUBLISHED_KEYS]
    mod = [p for p in parsed if p[1] in MODIFIED_KEYS]
    times = [p for p in parsed if p[1] == "time"]
    out = {"published": None, "modified": None}
    if pub:
        best = min(pub, key=lambda p: p[2])
        out["published"] = (best[2], best[0], "")
    elif times:
        best = min(times, key=lambda p: p[2])
        out["published"] = (best[2], "earliest <time> tag", "low confidence")
    if mod:
        best = max([p for p in mod if strong(p)] or mod, key=lambda p: p[2])
        note = "" if strong(best) else "low confidence: servers often send the fetch time"
        out["modified"] = (best[2], best[0], note)
    return out


def cmd_date(a):
    signals = []
    html_text = ""
    try:
        req = urllib.request.Request(a.url, headers={"User-Agent": BROWSER_UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.headers.get("Last-Modified"):
                signals.append(("http:Last-Modified", "last-modified", resp.headers["Last-Modified"]))
            html_text = resp.read(3_000_000).decode("utf-8", "replace")
    except Exception as e:
        print(f"direct fetch failed ({e}); asking Jina Reader for the page metadata")
        try:
            d = post(READER_URL, {"url": a.url}, {"X-Retain-Images": "none"}, timeout=45)
            pt = (d.get("data") or {}).get("publishedTime")
            if pt:
                signals.append(("reader:publishedTime", "datepublished", pt))
        except RuntimeError as e2:
            die(str(e2))
    signals += date_signals(html_text)
    picked = pick_dates(signals)

    print(f"URL: {a.url}")
    for label, value in (("Published:", picked["published"]), ("Modified: ", picked["modified"])):
        if value:
            when, source, note = value
            print(f"{label} {when.isoformat()}  ({source}{', ' + note if note else ''})")
    if not any(picked.values()):
        print("No date found in headers, meta tags, JSON-LD or <time> tags.")
    if a.all and signals:
        print("Signals:")
        for src, _, raw in signals:
            print(f"  {src}: {raw}")


# ---------------------------------------------------------------- setup

def config_dir():
    return Path.home() / ".config" / "jina"


def cmd_key(a):
    """Store the API key without it passing through argv or shell history."""
    path = config_dir() / "api_key"
    if a.show_path:
        print(path)
        return
    if sys.stdin.isatty():
        import getpass
        key = getpass.getpass("Jina API key (input hidden): ").strip()
    else:
        key = sys.stdin.read().strip()
    if not key:
        die("no key given: pipe it in, or run this at a terminal")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    print(f"key saved to {path} ({len(key)} chars)")


def cmd_install(a):
    """Write `jina-api` launchers that call this very script."""
    script = Path(__file__).resolve().as_posix()
    target = Path(a.dir).expanduser() if a.dir else Path.home() / ".local" / "bin"
    target.mkdir(parents=True, exist_ok=True)
    written = []
    if os.name == "nt":
        sh = f'#!/usr/bin/env bash\nexec python "{script}" "$@"\n'
        (target / "jina-api.cmd").write_text(f'@python "{Path(script)}" %*\n', encoding="utf-8")
        written.append(target / "jina-api.cmd")
    else:
        sh = f'#!/bin/sh\nexec python3 "{script}" "$@"\n'
    launcher = target / "jina-api"
    launcher.write_text(sh, encoding="utf-8", newline="\n")
    launcher.chmod(0o755)
    written.insert(0, launcher)
    for w in written:
        print(f"wrote {w}")
    on_path = any(Path(p).expanduser().resolve() == target.resolve()
                  for p in os.environ.get("PATH", "").split(os.pathsep) if p)
    if not on_path:
        print(f"note: {target} is not on PATH; add it, or call the launcher by its full path")


# ---------------------------------------------------------------- cli

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="jina-api", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def out_opts(sp, json_flag=True):
        sp.add_argument("-o", "--output", help="write the result to this file instead of stdout")
        if json_flag:
            sp.add_argument("--json", action="store_true", help="raw JSON output")

    r = sub.add_parser("read", help="URL or PDF -> markdown, or only the passages answering --question")
    r.add_argument("url", nargs="+", help="one or more URLs (read in parallel)")
    r.add_argument("-q", "--question", help="return only the passages answering this question")
    r.add_argument("--topk", type=int, default=1, help="passages to return with --question (default 1)")
    r.add_argument("--chunk-size", type=int, default=100, help="passage size in words with --question (default 100)")
    r.add_argument("--links", action="store_true", help="append every link on the page")
    r.add_argument("--images", action="store_true", help="append every image on the page")
    r.add_argument("--keep-links", action="store_true", help="keep inline link URLs in the markdown")
    r.add_argument("--ocr", action="store_true", help="read the rendered page with jina-ocr-v1 (scans; ~40x cost, one page per call)")
    r.add_argument("--page", type=int, help="page to OCR, 1-based (with --ocr)")
    r.add_argument("--format", choices=["markdown", "text", "html"], default="markdown")
    r.add_argument("--selector", help="CSS selector: keep only this part of the page")
    r.add_argument("--remove", help="CSS selector: drop these elements")
    r.add_argument("--wait-for", help="CSS selector to wait for before reading (JS pages)")
    r.add_argument("--no-cache", action="store_true", help="bypass Jina's cache")
    r.add_argument("--max-chars", type=int, default=40000, help="truncate each page (default 40000, 0 = no limit)")
    out_opts(r)
    r.set_defaults(func=cmd_read)

    def search_parser(name, func, help_text, geo=True, newest=False):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("query", nargs="+", help="one or more queries (run in parallel)")
        s.add_argument("-n", "--num", type=int, default=10, help="results per query (default 10, max 100)")
        s.add_argument("--since", help="age limit: h, d, w, m, y (or a raw qdr:... value)")
        if newest:
            s.add_argument("--newest", action="store_true", help="sort results by date, newest first")
        if geo:
            s.add_argument("--gl", help="country code, e.g. fr")
            s.add_argument("--hl", help="interface language, e.g. fr")
            s.add_argument("--location", help="search from this location, e.g. 'Lyon, France'")
        out_opts(s)
        s.set_defaults(func=func)

    search_parser("search", cmd_search, "web search: titles, URLs, snippets")
    search_parser("arxiv", cmd_arxiv, "search arXiv preprints (full titles, dates, authors from the arXiv API)",
                  geo=False, newest=True)
    search_parser("ssrn", cmd_ssrn, "search SSRN working papers", geo=False)

    i = sub.add_parser("images", help="image search: image and page URLs")
    i.add_argument("query")
    i.add_argument("-n", "--num", type=int, default=10)
    i.add_argument("--since")
    i.add_argument("--gl")
    i.add_argument("--hl")
    i.add_argument("--location")
    i.add_argument("--download", metavar="DIR", help="also save the images into DIR")
    out_opts(i)
    i.set_defaults(func=cmd_images)

    rr = sub.add_parser("rerank", help="sort documents by relevance to a query")
    rr.add_argument("query")
    rr.add_argument("documents", nargs="*", help="documents, or '-' / --file / a pipe (one per line, or a JSON array)")
    rr.add_argument("-f", "--file")
    rr.add_argument("--top", type=int, help="keep only the N best")
    out_opts(rr)
    rr.set_defaults(func=cmd_rerank)

    dd = sub.add_parser("dedupe", help="keep the semantically distinct strings, drop near-duplicates")
    dd.add_argument("strings", nargs="*", help="strings, or '-' / --file / a pipe (one per line, or a JSON array)")
    dd.add_argument("-f", "--file")
    dd.add_argument("-k", type=int, help="how many to keep (default: chosen automatically)")
    out_opts(dd)
    dd.set_defaults(func=cmd_dedupe)

    sc = sub.add_parser("screenshot", help="screenshot a page to a PNG file; prints its path")
    sc.add_argument("url")
    sc.add_argument("--full", action="store_true", help="whole page instead of the first screen")
    sc.add_argument("--url-only", action="store_true", help="print the image URL, download nothing")
    sc.add_argument("-o", "--output", help="PNG path (default: system temp dir)")
    sc.set_defaults(func=cmd_screenshot)

    pd = sub.add_parser("pdf", help="extract figures, tables and equations from a PDF as PNG files")
    pd.add_argument("--arxiv", help="arXiv id, e.g. 2301.12345")
    pd.add_argument("--url", help="PDF URL")
    pd.add_argument("--type", help="comma list: figure,table,equation (default all)")
    pd.add_argument("--max-edge", type=int, default=1024, help="longest image edge in px")
    pd.add_argument("--out", help="output directory (default: system temp dir)")
    pd.set_defaults(func=cmd_pdf)

    da = sub.add_parser("date", help="guess when a page was published and last modified")
    da.add_argument("url")
    da.add_argument("--all", action="store_true", help="list every date signal found")
    da.set_defaults(func=cmd_date)

    k = sub.add_parser("key", help="store the API key in ~/.config/jina/api_key (read from stdin or a hidden prompt)")
    k.add_argument("--show-path", action="store_true", help="print where the key is stored, change nothing")
    k.set_defaults(func=cmd_key)

    ins = sub.add_parser("install", help="write `jina-api` launchers for this script (default dir: ~/.local/bin)")
    ins.add_argument("--dir", help="where to write the launchers")
    ins.set_defaults(func=cmd_install)

    p.add_argument("--version", action="version", version=f"jina-api {__version__}")
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
