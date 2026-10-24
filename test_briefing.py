"""Unit tests for briefing.py. Run: python3 -m unittest test_briefing -v"""

import json
import pathlib
import unittest

import briefing

FIX = pathlib.Path(__file__).parent / "fixtures"


class ParseReads(unittest.TestCase):
    def setUp(self):
        self.md = (FIX / "reads.md").read_text()

    def test_newest_first_across_months(self):
        got = briefing.parse_reads(self.md, 4)
        self.assertEqual([t for t, _ in got], [
            "Automating AI model research with evolution",
            "LLM-based Evolution as a Universal Optimizer",
            "Beating ARC-AGI-2 with Code Evolution",
            "LLM-as-a-Verifier: A General-Purpose Verification Framework",
        ])
        self.assertEqual(got[0][1], "https://imbue.com/blog/2026-07-20-imbue-catalyst-nanochat")

    def test_n_larger_than_list(self):
        self.assertEqual(len(briefing.parse_reads(self.md, 50)), 7)

    def test_ignores_non_link_lines(self):
        md = "# T\n\nWebsite: <https://x.y>\n## May\n- plain bullet\n- [A](https://a.example)\n"
        self.assertEqual(briefing.parse_reads(md, 5), [("A", "https://a.example")])


class ParseHn(unittest.TestCase):
    def test_keeps_url_stories_in_rank_order(self):
        items = {
            1: {"type": "story", "title": "Ask HN: no url"},
            2: {"type": "story", "title": "Two", "url": "https://two.example"},
            3: {"type": "job", "title": "Hiring", "url": "https://job.example"},
            4: {"type": "story", "title": "Four", "url": "https://four.example"},
            5: {"type": "story", "title": "Five", "url": "https://five.example"},
        }
        got = briefing.parse_hn([1, 2, 3, 4, 5, 99], items, 2)
        self.assertEqual(got, [("Two", "https://two.example"), ("Four", "https://four.example")])


class HtmlToText(unittest.TestCase):
    def test_strips_noise_and_collapses_whitespace(self):
        html = ("<html><head><style>p{}</style><script>var x=1</script></head><body>"
                "<nav>Home | About</nav><h1>Title</h1>\n\n<p>Hello   <b>world</b>.</p>"
                "<footer>(c) site</footer></body></html>")
        self.assertEqual(briefing.html_to_text(html), "Title Hello world .")

    def test_caps_length(self):
        self.assertEqual(len(briefing.html_to_text("<p>" + "a " * 20000 + "</p>")),
                         briefing.MAX_ARTICLE_CHARS)


class FoldAscii(unittest.TestCase):
    def test_folds_typography_and_accents(self):
        self.assertEqual(briefing.fold_ascii("“Café” — it’s naïve…"),
                         "\"Cafe\" - it's naive...")

    def test_drops_the_rest(self):
        self.assertEqual(briefing.fold_ascii("ok \U0001F600 中 done"), "ok   done")

    def test_keeps_newlines_drops_tabs(self):
        self.assertEqual(briefing.fold_ascii("a\tb\nc"), "a b\nc")


class BuildBriefing(unittest.TestCase):
    def setUp(self):
        self.sources = [
            ("2026-reads", [("Read ‘One’", "https://r1.example"),
                            ("Read Two", "https://r2.example")]),
            ("Hacker News", [("HN One", "https://h1.example")]),
        ]
        self.pages = {"https://r1.example": "<p>Article one body.</p>",
                      "https://h1.example": "<p>HN body.</p>"}

    def fetch(self, url):
        if url not in self.pages:
            raise OSError("boom")
        return self.pages[url]

    def test_shape_limits_and_ascii(self):
        def llm(title, text):
            return {"summary": "s’" * 300, "deep": "d" * 9000}

        doc = briefing.build_briefing(self.sources, llm, self.fetch, "2026-10-24")
        self.assertEqual(doc["generated"], "2026-10-24")
        self.assertEqual([t["title"] for t in doc["topics"]], ["Read 'One'", "Read Two", "HN One"])
        self.assertEqual(doc["topics"][2]["source"], "Hacker News")
        for t in doc["topics"]:
            self.assertLessEqual(len(t["summary"]), briefing.MAX_SUMMARY)
            self.assertLessEqual(len(t["deep"]), briefing.MAX_DEEP)
            json.dumps(t).encode("ascii")  # all-ASCII after folding

    def test_failed_fetch_and_llm_errors_degrade_not_crash(self):
        calls = []

        def llm(title, text):
            calls.append(title)
            if title == "HN One":
                raise RuntimeError("api down")
            return {"summary": "ok", "deep": "deep ok"}

        doc = briefing.build_briefing(self.sources, llm, self.fetch, "d")
        r2 = doc["topics"][1]   # fetch failed -> llm sees title only, still summarized
        self.assertEqual(r2["summary"], "ok")
        hn = doc["topics"][2]   # llm failed -> title-only topic
        self.assertEqual(hn["summary"], "")
        self.assertIn("https://h1.example", hn["deep"])
        self.assertEqual(len(calls), 3)

    def test_caps_topic_count(self):
        many = [("Many", [(f"T{i}", f"https://t{i}.example") for i in range(20)])]
        doc = briefing.build_briefing(many, lambda t, x: {"summary": "s", "deep": "d"},
                                      lambda u: "", "d")
        self.assertEqual(len(doc["topics"]), briefing.MAX_TOPICS)


if __name__ == "__main__":
    unittest.main()
