#!/usr/bin/env python3
"""Fetch recent papers from arXiv focused on LLM serving / inference efficiency.

Uses only the Python standard library so it runs anywhere (incl. GitHub Actions)
with no extra dependencies. Results are written to data/papers.json which the
static frontend consumes.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ARXIV_API = "http://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"

# --- Translation (English -> Chinese) -----------------------------------
# Uses Google's public translate endpoint (no API key, stdlib only). Results
# are cached in papers.json so we only translate papers we haven't seen before.
TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
TRANSLATE_TARGET = "zh-CN"
# Max characters per translate request (endpoint is a GET, keep URLs sane).
TRANSLATE_CHUNK = 1800
# Set env SKIP_TRANSLATE=1 to skip translation (faster local runs).
SKIP_TRANSLATE = os.environ.get("SKIP_TRANSLATE") == "1"

# arXiv categories we care about (systems + ML for LLMs).
CATEGORIES = ["cs.LG", "cs.CL", "cs.DC", "cs.AR", "cs.PF", "cs.AI"]

# How many days back to keep. Papers older than this are dropped.
MAX_AGE_DAYS = 45
# Max papers to keep in the final feed.
MAX_PAPERS = 120
# Per-query result cap.
PER_QUERY = 60

# Topic -> list of (compiled regex) keyword patterns. A paper matches a topic
# if any pattern is found in its title or abstract. The first matched topic
# (in declaration order) is treated as the primary topic.
TOPIC_KEYWORDS = {
    "LLM Serving": [
        r"\bllm serving\b", r"\bserving system", r"\binference server",
        r"\bserving throughput", r"\brequest scheduling", r"\bcontinuous batching",
        r"\bdisaggregat", r"\bprefill", r"\bdecoding throughput", r"\bgoodput",
        r"\bvllm\b", r"\bsglang\b", r"\btensorrt-?llm\b", r"\bpagedattention\b",
    ],
    "KV Cache": [
        r"\bkv[ -]?cache", r"\bkey-value cache", r"\bcache compression",
        r"\bcache eviction", r"\bpaged attention\b", r"\battention sink",
    ],
    "Quantization": [
        r"\bquantiz", r"\blow-?bit\b", r"\bint4\b", r"\bint8\b", r"\bfp8\b",
        r"\bw4a16\b", r"\bgptq\b", r"\bawq\b", r"\bsmoothquant\b", r"\b4-?bit\b",
    ],
    "Speculative Decoding": [
        r"\bspeculative decoding\b", r"\bdraft model", r"\bmedusa\b",
        r"\beagle\b", r"\blookahead decoding\b", r"\bself-?speculative\b",
        r"\bparallel decoding\b",
    ],
    "Sparsity / MoE": [
        r"\bmixture of experts\b", r"\bmixture-of-experts\b", r"\bmoe\b",
        r"\bexpert parallel", r"\bsparse activation", r"\bpruning\b",
        r"\bsparse attention\b",
    ],
    "Long Context": [
        r"\blong context\b", r"\blong-?context\b", r"\bcontext window",
        r"\bcontext length", r"\bextrapolat", r"\bringattention\b",
        r"\bring attention\b",
    ],
    "Attention & Kernels": [
        r"\bflashattention\b", r"\bflash attention\b", r"\bkernel fusion\b",
        r"\bcuda kernel", r"\bgpu kernel", r"\blinear attention\b",
        r"\bstate space model", r"\bmamba\b",
    ],
    "Efficient Inference": [
        r"\binference efficien", r"\befficient inference\b", r"\blatency",
        r"\bthroughput", r"\bmemory-?efficient", r"\bacceleration\b",
        r"\bfaster inference\b", r"\bcost-?efficient", r"\bedge inference\b",
    ],
    "Training Efficiency": [
        r"\befficient training\b", r"\bdistributed training\b",
        r"\bparameter-?efficient\b", r"\blora\b", r"\bfine-?tuning\b",
    ],
}

# Boolean search queries sent to arXiv (title/abstract search).
SEARCH_TERMS = [
    "LLM serving", "inference efficiency", "KV cache", "speculative decoding",
    "PagedAttention", "continuous batching", "quantization large language model",
    "mixture of experts inference", "long context inference", "FlashAttention",
    "disaggregated inference", "LLM inference acceleration",
]

COMPILED_TOPICS = {
    topic: [re.compile(p, re.IGNORECASE) for p in pats]
    for topic, pats in TOPIC_KEYWORDS.items()
}


def build_query(term: str) -> str:
    cat_filter = "+OR+".join(f"cat:{c}" for c in CATEGORIES)
    # Search term in title OR abstract, restricted to relevant categories.
    escaped = urllib.parse.quote(term)
    search = f"(ti:%22{escaped}%22+OR+abs:%22{escaped}%22)+AND+({cat_filter})"
    params = (
        f"search_query={search}"
        f"&sortBy=submittedDate&sortOrder=descending"
        f"&start=0&max_results={PER_QUERY}"
    )
    return f"{ARXIV_API}?{params}"


def fetch(url: str, retries: int = 3) -> str:
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "paper-radar/1.0 (arxiv feed)"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def parse_entries(xml_text: str):
    root = ET.fromstring(xml_text)
    for entry in root.findall(f"{ATOM}entry"):
        raw_id = entry.findtext(f"{ATOM}id", "").strip()
        # e.g. http://arxiv.org/abs/2401.12345v2 -> 2401.12345
        m = re.search(r"arxiv\.org/abs/([^v]+)", raw_id)
        arxiv_id = m.group(1) if m else raw_id

        title = clean_text(entry.findtext(f"{ATOM}title", ""))
        summary = clean_text(entry.findtext(f"{ATOM}summary", ""))
        published = entry.findtext(f"{ATOM}published", "").strip()
        updated = entry.findtext(f"{ATOM}updated", "").strip()

        authors = [
            a.findtext(f"{ATOM}name", "").strip()
            for a in entry.findall(f"{ATOM}author")
        ]

        categories = []
        for c in entry.findall(f"{ATOM}category"):
            term = c.get("term")
            if term:
                categories.append(term)

        pdf_url = ""
        abs_url = raw_id
        for link in entry.findall(f"{ATOM}link"):
            if link.get("title") == "pdf":
                pdf_url = link.get("href", "")
            elif link.get("rel") == "alternate":
                abs_url = link.get("href", abs_url)

        yield {
            "id": arxiv_id,
            "title": title,
            "authors": authors,
            "summary": summary,
            "published": published,
            "updated": updated,
            "categories": categories,
            "abs_url": abs_url,
            "pdf_url": pdf_url,
        }


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _chunk_text(text: str, limit: int):
    """Split text into <=limit char chunks, preferring sentence boundaries."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts = re.split(r"(?<=[.!?])\s+", text)
    chunks, cur = [], ""
    for p in parts:
        if not p:
            continue
        if len(cur) + len(p) + 1 > limit and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = (cur + " " + p).strip() if cur else p
    if cur:
        chunks.append(cur)
    # A single sentence may still exceed the limit; hard-split those.
    out = []
    for c in chunks:
        while len(c) > limit:
            out.append(c[:limit])
            c = c[limit:]
        if c:
            out.append(c)
    return out


def translate_text(text: str) -> str:
    """Translate English text to Chinese. Returns "" on failure."""
    text = clean_text(text)
    if not text:
        return ""
    pieces = []
    for chunk in _chunk_text(text, TRANSLATE_CHUNK):
        params = urllib.parse.urlencode(
            {
                "client": "gtx",
                "sl": "en",
                "tl": TRANSLATE_TARGET,
                "dt": "t",
                "q": chunk,
            }
        )
        url = f"{TRANSLATE_URL}?{params}"
        raw = fetch(url)
        data = json.loads(raw)
        # data[0] is a list of [translated, original, ...] segments.
        seg = "".join(part[0] for part in data[0] if part and part[0])
        pieces.append(seg)
        time.sleep(0.2)
    return "".join(pieces).strip()


def safe_translate(text: str) -> str:
    try:
        return translate_text(text)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] translate failed: {e}", file=sys.stderr)
        return ""


def load_translation_cache(path: Path) -> dict:
    """Map arxiv id -> {title_zh, summary_zh} from a previous run."""
    try:
        old = json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    cache = {}
    for p in old.get("papers", []):
        pid = p.get("id")
        if pid and (p.get("title_zh") or p.get("summary_zh")):
            cache[pid] = {
                "title_zh": p.get("title_zh", ""),
                "summary_zh": p.get("summary_zh", ""),
            }
    return cache


def translate_papers(papers: list, cache: dict):
    total = len(papers)
    done = 0
    for i, p in enumerate(papers, 1):
        prev = cache.get(p["id"])
        if prev and prev.get("title_zh") and prev.get("summary_zh"):
            p["title_zh"] = prev["title_zh"]
            p["summary_zh"] = prev["summary_zh"]
            continue
        if SKIP_TRANSLATE:
            p["title_zh"] = ""
            p["summary_zh"] = ""
            continue
        print(f"[translate] {i}/{total} {p['id']}", file=sys.stderr)
        p["title_zh"] = safe_translate(p["title"])
        p["summary_zh"] = safe_translate(p["summary"])
        done += 1
    print(f"[translate] translated {done} new, reused {total - done}",
          file=sys.stderr)


def classify(paper: dict):
    text = f"{paper['title']} {paper['summary']}"
    topics = []
    for topic, patterns in COMPILED_TOPICS.items():
        if any(p.search(text) for p in patterns):
            topics.append(topic)
    return topics


def parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return datetime.now(timezone.utc)


def main():
    seen = {}
    for term in SEARCH_TERMS:
        url = build_query(term)
        print(f"[fetch] {term!r}", file=sys.stderr)
        try:
            xml_text = fetch(url)
        except RuntimeError as e:
            print(f"[warn] {e}", file=sys.stderr)
            continue
        count = 0
        for paper in parse_entries(xml_text):
            if paper["id"] not in seen:
                seen[paper["id"]] = paper
                count += 1
        print(f"[fetch]   +{count} new (total {len(seen)})", file=sys.stderr)
        # Be polite to the arXiv API.
        time.sleep(3)

    now = datetime.now(timezone.utc)
    papers = []
    for paper in seen.values():
        pub = parse_date(paper["published"])
        age_days = (now - pub).days
        if age_days > MAX_AGE_DAYS:
            continue
        topics = classify(paper)
        if not topics:
            # Skip papers that don't match any of our efficiency topics.
            continue
        paper["topics"] = topics
        paper["primary_topic"] = topics[0]
        paper["age_days"] = age_days
        papers.append(paper)

    # Newest first.
    papers.sort(key=lambda p: p["published"], reverse=True)
    papers = papers[:MAX_PAPERS]

    # Translate to Chinese (reusing previously translated papers).
    out_path = Path(__file__).parent / "data" / "papers.json"
    cache = load_translation_cache(out_path)
    translate_papers(papers, cache)

    # Topic counts for the filter UI.
    topic_counts = {}
    for p in papers:
        for t in p["topics"]:
            topic_counts[t] = topic_counts.get(t, 0) + 1

    out = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(papers),
        "topics": [
            {"name": t, "count": topic_counts.get(t, 0)}
            for t in TOPIC_KEYWORDS
            if topic_counts.get(t, 0) > 0
        ],
        "papers": papers,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"[done] wrote {len(papers)} papers to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
