#!/usr/bin/env python3
"""Chronoghost News digest builder.

Fetches the 12 source feeds, merges them into one newest-first timeline and
writes a self-contained static index.html in the existing Chronoghost design.
Standard library only, so it runs anywhere (cron, a python:3-alpine container).

Lives in scripts/ of chronoghost/news next to template.html (the page shell:
design, filter chips, reading-size admin panel and scripts). Only the stories,
timestamps and counts are generated; everything else comes from the template.

Usage: python3 scripts/build_news.py [output.html]   (default: ../index.html)
"""
import concurrent.futures as cf
import datetime as dt
import email.utils
import html
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36 ChronoghostNews/2"
MAX_STORIES = 100
MAX_AGE_DAYS = 60
AI_RE = re.compile(r"\b(AI|A\.I\.|artificial intelligence|OpenAI|ChatGPT|GPT-?\d|Anthropic|Claude|Gemini|DeepMind|xAI|Grok|LLMs?|chatbots?|machine learning|neural|Copilot|Nvidia|data cent(?:er|re)s?|agentic|agents?)\b", re.I)

# key, badge, colour, placeholder label, base tags, add 'ai' tag on keyword match, default topic, max items, kind, url
SOURCES = [
    ("bbc_tech", "BBC Tech", "#ff7a7a", "BBC", "ai tech uk", False, "Tech", 6, "rss", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("bbc_ni", "BBC NI", "#3dd6b3", "NI", "ni uk", False, "Northern Ireland", 13, "rss", "https://feeds.bbci.co.uk/news/northern_ireland/rss.xml"),
    ("bbc_uk", "BBC UK", "#ff5d5d", "BBC", "uk", False, "UK", 12, "rss", "https://feeds.bbci.co.uk/news/uk/rss.xml"),
    ("ars", "Ars Technica", "#ff8a4c", "ARS", "ai tech", False, "AI", 7, "rss", "https://arstechnica.com/ai/feed/"),
    ("tc", "TechCrunch", "#48c78e", "TC", "tech", True, "Tech", 5, "rss", "https://techcrunch.com/feed/"),
    ("verge", "The Verge", "#5b8cff", "VG", "tech", True, "Tech", 5, "atom", "https://www.theverge.com/rss/index.xml"),
    ("reuters", "Reuters", "#f5c542", "REU", "uk", True, "UK", 5, "rss", "https://news.google.com/rss/search?q=site:reuters.com/world/uk+when:1d&hl=en-GB&gl=GB&ceid=GB:en"),
    ("ft", "Financial Times", "#fff1a8", "FT", "tech uk", True, "Tech", 5, "rss", "https://www.ft.com/technology?format=rss"),
    ("openai", "OpenAI", "#3ecf8e", "OA", "ai", False, "AI", 8, "rss", "https://openai.com/news/rss.xml"),
    ("anthropic", "Anthropic", "#e0a36b", "AN", "ai", False, "Announcements", 8, "anthropic", "https://www.anthropic.com/news"),
    ("xai", "xAI", "#b08cff", "xAI", "ai", False, "Product", 6, "xai", "https://x.ai/news"),
    ("deepmind", "Google DeepMind", "#4285f4", "DM", "ai", False, "AI", 5, "rss", "https://deepmind.google/blog/rss.xml"),
]
NO_IMAGE = {"reuters", "ft", "openai"}  # paywall/redirect/bot-blocked pages: placeholder tile, as before

NS = {"media": "http://search.yahoo.com/mrss/", "atom": "http://www.w3.org/2005/Atom"}


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        import gzip
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def clip(s, n=220):
    s = strip_tags(s)
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except Exception:
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            for fmt in ("%b %d, %Y", "%B %d, %Y"):
                try:
                    d = dt.datetime.strptime(s, fmt).replace(hour=12)
                    break
                except Exception:
                    d = None
            if d is None:
                return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def parse_rss(text):
    root = ET.fromstring(text.encode("utf-8"))
    out = []
    for it in root.iter("item"):
        title = strip_tags(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        desc = it.findtext("description") or ""
        date = parse_date(it.findtext("pubDate"))
        img = None
        for tag in ("media:thumbnail", "media:content"):
            el = it.find(tag, NS)
            if el is not None and el.get("url"):
                img = el.get("url")
                break
        if not img:
            enc = it.find("enclosure")
            if enc is not None and (enc.get("type") or "").startswith("image"):
                img = enc.get("url")
        cats = [strip_tags(c.text) for c in it.findall("category") if c.text and strip_tags(c.text)]
        out.append(dict(title=title, link=link, desc=desc, date=date, img=img, cats=cats))
    return out


def parse_atom(text):
    root = ET.fromstring(text.encode("utf-8"))
    out = []
    for e in root.findall("atom:entry", NS):
        link = ""
        for l in e.findall("atom:link", NS):
            if l.get("rel", "alternate") == "alternate":
                link = l.get("href")
        content = e.findtext("atom:content", default="", namespaces=NS) or e.findtext("atom:summary", default="", namespaces=NS)
        m = re.search(r'<img[^>]+src="([^"]+)"', content)
        out.append(dict(title=strip_tags(e.findtext("atom:title", default="", namespaces=NS)), link=link,
                        desc=e.findtext("atom:summary", default="", namespaces=NS) or content,
                        date=parse_date(e.findtext("atom:published", default="", namespaces=NS) or e.findtext("atom:updated", default="", namespaces=NS)),
                        img=html.unescape(m.group(1)) if m else None,
                        cats=[c.get("term") for c in e.findall("atom:category", NS) if c.get("term")]))
    return out


def parse_anthropic(text):
    """anthropic.com has no RSS: read the 'All news' table on /news."""
    out = []
    for m in re.finditer(r'title-link" href="(/news/[^"]+)".*?<h3[^>]*>(.*?)</h3>.*?__description".*?<p>(.*?)</p>.*?__category".*?>([^<]*)</a>.*?<time>([^<]+)</time>', text, re.S):
        href, title, desc, cat, date = m.groups()
        out.append(dict(title=strip_tags(title), link="https://www.anthropic.com" + href, desc=desc,
                        date=parse_date(date), img=None, cats=[strip_tags(cat)]))
    return out


def parse_xai(text):
    """x.ai has no RSS: read the cards on /news (title, optional summary, date)."""
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>", "", text, flags=re.S)
    out, seen = [], set()
    for m in re.finditer(r'<a [^>]*href="(/news/[a-z0-9-]+)"[^>]*>(.*?)</a>', t, re.S):
        href, inner = m.groups()
        if href in seen:
            continue
        txt = strip_tags(inner)
        dm = re.search(r"([A-Z][a-z]{2} \d{1,2}, 20\d\d)", txt)
        if not dm:
            continue
        seen.add(href)
        h = re.search(r"<h[1-4][^>]*>(.*?)</h[1-4]>", inner, re.S)
        title = strip_tags(h.group(1)) if h else ""
        p = re.search(r"<p[^>]*>(.*?)</p>", inner, re.S)
        cat = re.search(r"([A-Z][a-z]+) · " + re.escape(dm.group(1)), txt)
        if not title:
            continue
        out.append(dict(title=title, link="https://x.ai" + href, desc=p.group(1) if p else "",
                        date=parse_date(dm.group(1)), img=None, cats=[cat.group(1)] if cat else []))
    return out


def og_image(url):
    try:
        page = get(url, timeout=12)[:300000]
    except Exception:
        return None
    for pat in (r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"'):
        m = re.search(pat, page)
        if m:
            u = html.unescape(m.group(1))
            return u if u.startswith("http") else None
    return None


def fetch_source(src):
    key, badge, col, ph, tags, ai_kw, topic, n, kind, url = src
    try:
        text = get(url)
        items = {"rss": parse_rss, "atom": parse_atom, "anthropic": parse_anthropic, "xai": parse_xai}[kind](text)
    except Exception as e:
        return src, [], f"ERROR {e}"
    items = [i for i in items if i["title"] and i["link"] and i["date"]]
    if key == "reuters":
        for i in items:
            i["title"] = re.sub(r"\s+-\s+Reuters$", "", i["title"])
            i["desc"] = ""
    if key.startswith("bbc"):
        for i in items:  # BBC thumbs are 240px; the 976px rendition of the same image exists
            if i["img"]:
                i["img"] = re.sub(r"/ace/standard/\d+/", "/ace/standard/976/", i["img"])
    items.sort(key=lambda i: i["date"], reverse=True)
    return src, items[:n], "ok"


def esc(s):
    return html.escape(s or "", quote=True)


def rel_time(d, now):
    mins = int((now - d).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 1)}m ago"
    if mins < 24 * 60:
        return f"{mins // 60}h ago"
    if mins < 48 * 60:
        return "Yesterday"
    return d.astimezone(LONDON).strftime("%-d %b")


def media(item, ph, size_cls=""):
    if item.get("img"):
        return (f'<div class="media"><img src="{esc(item["img"])}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" '
                f'onerror="this.parentElement.classList.add(\'is-fallback\')"><div class="ph" aria-hidden="true"><span>{esc(ph)}</span></div></div>')
    return f'<div class="media no-img"><div class="ph" aria-hidden="true"><span>{esc(ph)}</span></div></div>'


def meta(item, now, with_topic=True):
    t = f'<span class="topic">{esc(item["topic"])}</span>' if with_topic and item["topic"] else ""
    return (f'<div class="meta"><span class="badge">{esc(item["badge"])}</span>{t}<span class="dot">·</span>'
            f'<time>{rel_time(item["date"], now)}</time></div>')


def main(out_path):
    now = dt.datetime.now(dt.timezone.utc)
    stories, report = [], []
    with cf.ThreadPoolExecutor(12) as ex:
        for src, items, status in ex.map(fetch_source, SOURCES):
            key, badge, col, ph, tags, ai_kw, topic, *_ = src
            report.append((badge, status, len(items), items[0]["date"] if items else None))
            for i in items:
                if (now - i["date"]).days > MAX_AGE_DAYS:
                    continue
                t = tags
                if ai_kw and "ai" not in t.split() and AI_RE.search(i["title"] + " " + strip_tags(i["desc"])):
                    t = "ai " + t
                cat = next((c for c in i["cats"] if len(c) < 28 and not c.lower().startswith(("http", "uncategor"))), None)
                stories.append(dict(i, key=key, badge=badge, col=col, ph=ph, tags=t, topic=cat or topic,
                                    why=clip(i["desc"])))
    # og:image for sources whose feeds carry no image
    need = [s for s in stories if not s.get("img") and s["key"] not in NO_IMAGE]
    with cf.ThreadPoolExecutor(12) as ex:
        for s, img in zip(need, ex.map(lambda s: og_image(s["link"]), need)):
            s["img"] = img
    stories.sort(key=lambda s: s["date"], reverse=True)
    # de-duplicate: BBC often files one story in both the UK and NI feeds (and repeats items)
    seen, uniq = set(), []
    for s in stories:
        k1 = re.sub(r"\W+", "", s["title"].lower())
        k2 = s["link"].split("?")[0]
        if k1 in seen or k2 in seen:
            continue
        seen.update((k1, k2))
        uniq.append(s)
    stories = uniq[:MAX_STORIES]
    ok_sources = sum(1 for r in report if r[1] == "ok" and r[2])
    if ok_sources < 8 or len(stories) < 30:  # never publish a half-empty page
        for b, st, n, d in report:
            print(f"{b:16} {st} {n}", file=sys.stderr)
        sys.exit(f"refusing to write: only {ok_sources} sources / {len(stories)} stories")

    # Stage: hero = newest BBC Tech with image; side = newest BBC NI + newest Ars, as the original layout
    def pick(keys):
        for s in stories:
            if s["key"] in keys and s.get("img") and not s.get("_stage"):
                s["_stage"] = True
                return s
    hero = pick({"bbc_tech"}) or pick({"ars", "verge", "tc"})
    side = [x for x in (pick({"bbc_ni"}), pick({"ars"}) or pick({"openai", "anthropic", "deepmind", "xai"})) if x]
    feed = [s for s in stories if not s.get("_stage")]

    local_now = now.astimezone(LONDON)
    long_date = local_now.strftime("%A %-d %B %Y")
    clock = local_now.strftime("%-I:%M%p").lower()
    tz = local_now.tzname()

    parts = []
    if hero:
        parts.append(f'''
        <article class="hero" data-tags="{hero["tags"]}" style="--c:{hero["col"]}">
          <a class="hero-link" href="{esc(hero["link"])}" target="_blank" rel="noopener">
            {media(hero, hero["ph"])}
            <div class="hero-body">
              {meta(hero, now)}
              <h2>{esc(hero["title"])}</h2>
              <p class="snip">{esc(hero["why"])}</p>
            </div>
          </a>
        </article>''')
    parts.append('    <div class="side">')
    for s in side:
        parts.append(f'''
        <article class="feature" data-tags="{s["tags"]}" style="--c:{s["col"]}">
          <a href="{esc(s["link"])}" target="_blank" rel="noopener">
            {media(s, s["ph"])}
            <div class="f-body">
              {meta(s, now, with_topic=False)}
              <h3>{esc(s["title"])}</h3>
              <p class="why">{esc(s["why"])}</p>
            </div>
          </a>
        </article>''')
    parts.append("    </div>")
    stage_html = "\n".join(parts)

    def day_label(d):
        age_h = (now - d).total_seconds() / 3600
        if age_h < 24:
            return "Today"
        if age_h < 48:
            return "Yesterday"
        return d.astimezone(LONDON).strftime("%A %-d %B")

    blocks, cur = [], None
    for s in feed:
        lbl = day_label(s["date"])
        if lbl != cur:
            if cur is not None:
                blocks.append("</div></section>")
            blocks.append(f'<section class="day-block"><h2 class="day">{lbl}</h2><div class="feed">')
            cur = lbl
        why = f'\n          <p class="why">{esc(s["why"])}</p>' if s["why"] else ""
        blocks.append(f'''
    <article class="row" data-tags="{s["tags"]}" style="--c:{s["col"]}">
      <a href="{esc(s["link"])}" target="_blank" rel="noopener">
        {media(s, s["ph"])}
        <div class="r-body">
          {meta(s, now)}
          <h3>{esc(s["title"])}</h3>{why}
        </div>
      </a>
    </article>''')
    if cur is not None:
        blocks.append("</div></section>")

    counts = {}
    for s in stories:
        counts[s["badge"]] = counts.get(s["badge"], 0) + 1
    n_sources = len(counts)
    src_line = " · ".join(f"{b} {c}" for b, c in counts.items())

    tpl = open(TEMPLATE, encoding="utf-8").read()
    page = (tpl.replace("{{TITLE_DATE}}", local_now.strftime("%-d %b %Y"))
               .replace("{{COUNT}}", str(len(stories))).replace("{{NSRC}}", str(n_sources))
               .replace("{{LONG_DATE}}", long_date).replace("{{CLOCK}}", clock).replace("{{TZ}}", tz)
                              .replace("{{STAGE}}", stage_html).replace("{{FEED}}", "\n".join(blocks))
               .replace("{{SOURCES}}", esc(src_line)).replace("{{MAX}}", str(MAX_STORIES)))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    for b, st, n, d in report:
        print(f"{b:16} {st:6} {n:3} newest={d.astimezone(LONDON).strftime('%d %b %H:%M') if d else '-'}", file=sys.stderr)
    print(f"wrote {out_path}: {len(stories)} stories from {n_sources} sources", file=sys.stderr)


import os
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html")

if __name__ == "__main__":
    default_out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.html")
    main(sys.argv[1] if len(sys.argv) > 1 else default_out)
