# Chronoghost News

Static digest served by GitHub Pages at https://chronoghost.github.io/news/.

- `index.html` is generated. Don't hand-edit stories; change `scripts/template.html` (design, filters, reading-size panel) or `scripts/build_news.py` (sources and rules) instead.
- `.github/workflows/refresh.yml` rebuilds the page at 05:00 and 17:00 UTC, commits it to `main` as github-actions[bot] if anything changed, and deploys it to Pages. Run it by hand from the Actions tab with "Run workflow".
- Build locally: `python3 scripts/build_news.py` (Python 3.9+, no dependencies), which writes `index.html`.

Sources: BBC NI, BBC UK, BBC Tech, Ars Technica (AI), TechCrunch, The Verge, Reuters (UK, via Google News), Financial Times (Technology), OpenAI, Anthropic, xAI, Google DeepMind. Headlines, links, images and summaries come straight from the feeds.
