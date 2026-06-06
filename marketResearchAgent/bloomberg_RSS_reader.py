import feedparser
import requests
from bs4 import BeautifulSoup

URLS = [
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.bloomberg.com/technology/news.rss",
    "https://feeds.bloomberg.com/politics/news.rss",
]

# Content type is encoded in the URL path
_CONTENT_TYPE_MAP = {
    "/news/articles/":    "article",
    "/news/videos/":      "video",
    "/news/audio/":       "podcast",
    "/news/newsletters/": "newsletter",
}


def _detect_content_type(url: str) -> str:
    for fragment, label in _CONTENT_TYPE_MAP.items():
        if fragment in url:
            return label
    return "article"


def _clean_html(text: str) -> str:
    """Strip HTML tags and decode entities from description/summary fields."""
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True)


def get_article_text(url: str) -> str:
    """Fetch and extract body text from a Bloomberg article page."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        paragraphs = soup.select(
            "article p, "
            ".body-content p, "
            ".article-body p, "
            "[data-component='lede-text'] p, "
            "[data-component='body-text'] p"
        )
        return " ".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
    except Exception:
        return ""


def fetch_feed(urls: list = URLS, fetch_articles: bool = False, max_items: int = 20) -> str:
    """
    Fetches Bloomberg RSS feeds and returns a plain-text string of headlines + metadata.

    Args:
        urls:            List of RSS feed URLs to pull from.
        fetch_articles:  If True, fetches and appends full article body text (slower).
                         Note: Bloomberg is paywalled; body text may be limited.
        max_items:       Max number of articles to include per feed.

    Returns:
        A formatted string with one article block per item.
    """
    blocks = []

    for url in urls:
        feed = feedparser.parse(url)
        for entry in feed.entries[:max_items]:
            title        = entry.get("title", "").strip()
            link         = entry.get("link", "").strip()
            date         = entry.get("published", "")
            # dc:creator -> feedparser exposes as "author"
            author       = entry.get("author", "").strip()
            # Bloomberg summary fields contain HTML entities; clean them
            summary      = _clean_html(entry.get("summary", ""))
            # Stock-symbol categories (domain="stock-symbol"); may be multiple
            tickers      = [
                t.get("term", "")
                for t in entry.get("tags", [])
                if t.get("term", "")
            ]
            # Thumbnail image URL from media:content
            media        = entry.get("media_content", [{}])
            thumbnail    = media[0].get("url", "") if media else ""
            content_type = _detect_content_type(link)

            lines = [
                f"HEADLINE: {title}",
                f"SOURCE: Bloomberg",
                f"TYPE: {content_type}",
                f"DATE: {date}",
                f"URL: {link}",
            ]
            if author:
                lines.append(f"AUTHOR: {author}")
            if tickers:
                lines.append(f"TICKERS: {', '.join(tickers)}")
            if summary:
                lines.append(f"SUMMARY: {summary}")
            if thumbnail:
                lines.append(f"THUMBNAIL: {thumbnail}")

            if fetch_articles and content_type == "article":
                body = get_article_text(link)
                if body:
                    lines.append(f"BODY: {body}")

            blocks.append("\n".join(lines))

    return "\n\n---\n\n".join(blocks)


if __name__ == "__main__":
    print(fetch_feed())