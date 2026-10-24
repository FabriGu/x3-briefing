"""Daily briefing generator for the X3 e-ink reader.

Pulls the newest links from a friend's reading list (kickingkeys/2026-reads)
plus the top Hacker News stories, asks Claude for a short summary and a
longer deep-dive of each, and writes briefing.json. The x3-link phone app
serves that file to the device over BLE, one section at a time.

Everything but the Claude call is stdlib. Output is folded to ASCII: the
device's Adafruit fonts only have glyphs 0x20-0x7E.

  python3 briefing.py              # real run (needs ANTHROPIC_API_KEY)
  python3 briefing.py --dry-run    # placeholder text, no API call
"""

import argparse
import datetime
import html.parser
import json
import re
import sys
import unicodedata
import urllib.request

READS_URL = "https://raw.githubusercontent.com/kickingkeys/2026-reads/main/README.md"
HN_TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"

N_READS = 5
N_HN = 3
MAX_TOPICS = 8          # device list holds 8
MAX_SUMMARY = 200       # two 9 pt lines on the list screen
MAX_DEEP = 3500         # + title stays under the 4096 B wire cap
MAX_ARTICLE_CHARS = 12000
MODEL = "claude-opus-5-5"

LINK = re.compile(r"^- \[(.+?)\]\((https?://[^)\s]+)\)")


# --- Sources -------------------------------------------------------------------

def parse_reads(md, n):
    """Newest n (title, url). Months run Jan..Dec and links are appended at the
    end of their month, so document order is chronological."""
    links = [m.groups() for m in map(LINK.match, md.splitlines()) if m]
    return [tuple(l) for l in reversed(links[-n:])] if n else []


def parse_hn(ids, items, n):
    """First n link stories (jobs and text posts skipped), in rank order."""
    out = []
    for i in ids:
        it = items.get(i) or {}
        if it.get("type") == "story" and it.get("url"):
            out.append((it["title"], it["url"]))
            if len(out) == n:
                break
    return out


# --- Text ----------------------------------------------------------------------

class _Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "aside", "noscript", "svg"}

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def html_to_text(page):
    p = _Text()
    p.feed(page)
    return " ".join(" ".join(p.parts).split())[:MAX_ARTICLE_CHARS]


_TYPO = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                       "–": "-", "—": "-", "…": "...", " ": " ",
                       "\t": " "})


def fold_ascii(s):
    s = unicodedata.normalize("NFKD", s.translate(_TYPO))
    return "".join(c for c in s if c == "\n" or " " <= c <= "~")


# --- Briefing ------------------------------------------------------------------

def build_briefing(sources, llm, fetch, date):
    """sources: [(name, [(title, url)])]. llm(title, text) -> {summary, deep}.
    A failed fetch still gets summarized from the title; a failed llm call
    leaves a title-only topic. Never raises for one bad link."""
    topics = []
    for name, links in sources:
        for title, url in links:
            if len(topics) == MAX_TOPICS:
                break
            try:
                text = html_to_text(fetch(url))
            except Exception as e:  # dead link, paywall, timeout
                print(f"fetch failed {url}: {e}", file=sys.stderr)
                text = ""
            try:
                out = llm(title, text)
                summary, deep = out["summary"], out["deep"]
            except Exception as e:
                print(f"llm failed {title!r}: {e}", file=sys.stderr)
                summary, deep = "", f"No summary today. Read it at {url}"
            topics.append({
                "title": fold_ascii(title),
                "source": name,
                "url": url,
                "summary": fold_ascii(summary)[:MAX_SUMMARY].rstrip(),
                "deep": fold_ascii(deep)[:MAX_DEEP].rstrip(),
            })
    return {"generated": date, "topics": topics}


# --- I/O (not unit-tested: thin wrappers) ---------------------------------------

def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "x3-briefing/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def claude_llm():
    import anthropic  # only the real run needs the SDK

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from the environment
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}, "deep": {"type": "string"}},
        "required": ["summary", "deep"],
        "additionalProperties": False,
    }

    def llm(title, text):
        prompt = (
            "You write a morning briefing read on a small black-and-white e-ink screen.\n"
            f"Article title: {title}\n"
            f"Article text (may be empty or partial):\n{text or '(unavailable)'}\n\n"
            "Return JSON with:\n"
            "- summary: one or two plain sentences, under 180 characters, saying what it "
            "is and why it matters.\n"
            "- deep: a 300-450 word explanation for a curious reader: the core idea, the "
            "key details or evidence, and what is interesting or contested. Plain prose, "
            "paragraphs separated by a blank line, no markdown, no bullet points.\n"
            "If the text is unavailable, work from the title and say you only had the title."
        )
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("refused")
        return json.loads(next(b.text for b in resp.content if b.type == "text"))

    return llm


def dry_llm(title, text):
    return {"summary": f"(dry run) {title}",
            "deep": f"(dry run) Placeholder deep-dive for {title}.\n\n{text[:600]}"}


def gather():
    sources = [("2026-reads", parse_reads(http_get(READS_URL), N_READS))]
    try:
        ids = json.loads(http_get(HN_TOP))[:30]
        items = {i: json.loads(http_get(HN_ITEM.format(i))) for i in ids[:10]}
        sources.append(("Hacker News", parse_hn(ids, items, N_HN)))
    except Exception as e:
        print(f"hn failed: {e}", file=sys.stderr)
    return sources


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="briefing.json")
    args = ap.parse_args()
    llm = dry_llm if args.dry_run else claude_llm()
    doc = build_briefing(gather(), llm, http_get, datetime.date.today().isoformat())
    if not doc["topics"]:
        sys.exit("no topics - keeping the previous briefing.json")
    with open(args.out, "w") as f:
        json.dump(doc, f, indent=1)
    print(f"wrote {args.out}: {len(doc['topics'])} topics")


if __name__ == "__main__":
    main()
