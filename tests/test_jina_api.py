"""Offline tests for src/jina_api.py: nothing here touches the network.

    python -m unittest discover -s tests
"""
import datetime as dt
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import jina_api as j  # noqa: E402


def args(**kw):
    return types.SimpleNamespace(**kw)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ParseDate(unittest.TestCase):
    def test_formats(self):
        cases = {
            "2024-08-22T17:06:17.000+02:00": "2024-08-22T17:06:17+02:00",
            "2024-08-22T15:06:17Z": "2024-08-22T15:06:17+00:00",
            "Mon, 12 Oct 2026 01:36:05 GMT": "2026-10-12T01:36:05+00:00",
            "May 21, 2026": "2026-05-21T00:00:00+00:00",
            "2026/03/23": "2026-03-23T00:00:00+00:00",
            "published on 2025.01.31 by us": "2025-01-31T00:00:00+00:00",
        }
        for raw, iso in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(j.parse_date(raw).isoformat(), iso)

    def test_implausible_dates_are_dropped(self):
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat()
        for raw in ("1990-01-01", future, "", "not a date"):
            with self.subTest(raw=raw):
                self.assertIsNone(j.parse_date(raw))


class PageDates(unittest.TestCase):
    HTML = """<html><head>
      <meta property="article:published_time" content="2024-08-22T17:06:17+02:00">
      <meta property='article:modified_time' content='2024-10-06T16:29:02+02:00'>
      <script type="application/ld+json">{"@graph": [{"datePublished": "2024-08-23", "dateModified": "2024-10-01"}]}</script>
      </head><body><time datetime="2023-01-01">old</time></body></html>"""

    def test_metadata_beats_the_http_header(self):
        signals = [("http:Last-Modified", "last-modified", "Mon, 12 Oct 2026 01:36:05 GMT")]
        signals += j.date_signals(self.HTML)
        picked = j.pick_dates(signals)
        self.assertEqual(picked["published"][0].isoformat(), "2024-08-22T17:06:17+02:00")
        self.assertEqual(picked["modified"][0].isoformat(), "2024-10-06T16:29:02+02:00")
        self.assertEqual(picked["modified"][1], "meta:article:modified_time")

    def test_header_alone_is_low_confidence(self):
        picked = j.pick_dates([("http:Last-Modified", "last-modified", "Mon, 12 Oct 2026 01:36:05 GMT")])
        self.assertIn("low confidence", picked["modified"][2])
        self.assertIsNone(picked["published"])

    def test_time_tag_is_the_last_resort(self):
        picked = j.pick_dates(j.date_signals('<time datetime="2022-05-01">x</time>'))
        self.assertEqual(picked["published"][1], "earliest <time> tag")


class Dedupe(unittest.TestCase):
    # two near-identical vectors, one apart
    VECTORS = [[1.0, 0.0, 0.0], [0.99, 0.05, 0.0], [0.0, 0.0, 1.0]]

    def test_automatic_count_drops_the_near_duplicate(self):
        keep = j.select_diverse(self.VECTORS)
        self.assertEqual(len(keep), 2)
        self.assertIn(2, keep)

    def test_explicit_k(self):
        self.assertEqual(len(j.select_diverse(self.VECTORS, k=1)), 1)
        self.assertEqual(sorted(j.select_diverse(self.VECTORS, k=3)), [0, 1, 2])

    def test_pure_python_path_matches(self):
        expected = j.select_diverse(self.VECTORS)
        with mock.patch.dict(sys.modules, {"numpy": None}):
            self.assertEqual(j.select_diverse(self.VECTORS), expected)


class Inputs(unittest.TestCase):
    def test_lines_and_json_from_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            lines, arr = Path(d, "l.txt"), Path(d, "a.json")
            lines.write_text("one\n\ntwo\n", encoding="utf-8")
            arr.write_text('["a, b", "c"]', encoding="utf-8")
            self.assertEqual(j.read_inputs([], str(lines)), ["one", "two"])
            self.assertEqual(j.read_inputs([], str(arr)), ["a, b", "c"])

    def test_arguments_win(self):
        self.assertEqual(j.read_inputs(["x", "y"], None), ["x", "y"])

    def test_since(self):
        self.assertEqual(j.tbs("w"), "qdr:w")
        self.assertEqual(j.tbs("qdr:m"), "qdr:m")
        self.assertIsNone(j.tbs(None))

    def test_clip(self):
        self.assertEqual(j.clip("abc", 0), "abc")
        self.assertTrue(j.clip("abcdef", 3).startswith("abc\n\n[truncated at 3 of 6"))


class Search(unittest.TestCase):
    def test_arxiv_id(self):
        self.assertEqual(j.arxiv_id({"arxiv_id": "2409.04701v3"}), "2409.04701")
        self.assertEqual(j.arxiv_id({"url": "https://arxiv.org/pdf/2603.22633"}), "2603.22633")
        self.assertIsNone(j.arxiv_id({"url": "https://example.com"}))

    def test_search_body(self):
        a = args(num=5, since="d", gl="fr", hl=None, location=None)
        self.assertEqual(j.search_body("q", a, {"domain": "arxiv"}),
                         {"q": "q", "num": 5, "tbs": "qdr:d", "gl": "fr", "domain": "arxiv"})

    def test_format_hits(self):
        out = j.format_hits("q", [{"title": "T", "url": "U", "date": "2026-01-02",
                                   "author": "A, B", "snippet": "  some\n text "}], True)
        self.assertEqual(out, "## q\n1. T\n   U\n   date: 2026-01-02 · A, B\n   some text")

    def test_complete_arxiv_from_semantic_scholar(self):
        results = [[{"title": "Cut ti", "url": "https://arxiv.org/pdf/2603.22633", "date": "2026"},
                    {"title": "Other", "url": "https://arxiv.org/pdf/2409.04701", "date": "2024"}]]
        s2 = [{"title": "Full Title", "publicationDate": "2026-03-23",
               "authors": [{"name": n} for n in ("Ann", "Bob", "Cy", "Di")]},
              {"title": "Late Chunking", "publicationDate": "2024-09-07", "authors": [{"name": "Mo"}]}]
        with mock.patch.object(j.urllib.request, "urlopen",
                               return_value=FakeResponse(json.dumps(s2).encode())):
            j.complete_arxiv(results, newest=True)
        first, second = results[0]
        self.assertEqual(first["title"], "Full Title")
        self.assertEqual(first["author"], "Ann, Bob, Cy et al.")
        self.assertEqual(first["url"], "https://arxiv.org/abs/2603.22633")
        self.assertEqual(second["date"], "2024-09-07")

    def test_abs_page_fallback_flips_author_names(self):
        page = ('<meta name="citation_title" content="A &amp; B" />'
                '<meta name="citation_author" content="Doe, Jane" />'
                '<meta name="citation_date" content="2026/03/23" />')
        with mock.patch.object(j.urllib.request, "urlopen", return_value=FakeResponse(page.encode())):
            rec = j.arxiv_abs_record("2603.22633")
        self.assertEqual(rec, {"title": "A & B", "date": "2026-03-23", "author": "Jane Doe"})


class Setup(unittest.TestCase):
    def test_install_writes_launchers_that_call_this_script(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("sys.stdout", new=io.StringIO()):
            j.cmd_install(args(dir=d))
            text = Path(d, "jina-api").read_text(encoding="utf-8")
            self.assertIn(Path(j.__file__).resolve().as_posix(), text)
            if os.name == "nt":
                self.assertTrue(Path(d, "jina-api.cmd").is_file())

    def test_key_from_stdin(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(j, "config_dir", return_value=Path(d)), \
                mock.patch("sys.stdin", new=io.StringIO("jina_secret\n")), \
                mock.patch("sys.stdout", new=io.StringIO()) as out:
            j.cmd_key(args(show_path=False))
            self.assertEqual(Path(d, "api_key").read_text(encoding="utf-8").strip(), "jina_secret")
            self.assertNotIn("jina_secret", out.getvalue())

    def test_missing_key_explains_what_to_do(self):
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if k != "JINA_API_KEY"}
            env.update(HOME=d, USERPROFILE=d)
            p = subprocess.run([sys.executable, str(ROOT / "src" / "jina_api.py"), "search", "x"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 1)
            self.assertIn("jina-api key", p.stderr)


class Copies(unittest.TestCase):
    def test_every_skill_carries_the_same_script(self):
        p = subprocess.run([sys.executable, str(ROOT / "tools" / "sync_scripts.py"), "--check"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_skill_frontmatter(self):
        for skill in sorted((ROOT / "skills").iterdir()):
            with self.subTest(skill=skill.name):
                head = (skill / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
                fields = dict(line.split(": ", 1) for line in head.strip().splitlines())
                self.assertEqual(fields["name"], skill.name)
                self.assertTrue(fields["description"].startswith("Use when"))
                self.assertLessEqual(len(fields["description"]), 1024)


if __name__ == "__main__":
    unittest.main()
