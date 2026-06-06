import feedparser
import requests
from bs4 import BeautifulSoup

URLS = [
    "https://finance.yahoo.com/news/rssindex"
]

def get_article_text(url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        paragraphs = soup.select("article p, .caas-body p")
        return " ".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
    except Exception:
        return ""

def fetch_feed(urls: list = URLS, fetch_articles: bool = False, max_items: int = 20) -> str:
    """
    Fetches Yahoo Finance RSS and returns a plain-text string of headlines + metadata.
    
    Args:
        urls:            List of RSS feed URLs to pull from.
        fetch_articles:  If True, fetches and appends full article body text (slower).
        max_items:       Max number of articles to include per feed.

    Returns:
        A formatted string with one article block per item.
    """
    blocks = []

    for url in urls:
        feed = feedparser.parse(url)
        for entry in feed.entries[:max_items]:
            title   = entry.get("title", "").strip()
            link    = entry.get("link", "").strip()
            source  = entry.get("source", {}).get("value", "Yahoo Finance")
            date    = entry.get("published", "")

            lines = [
                f"HEADLINE: {title}",
                f"SOURCE: {source}",
                f"DATE: {date}",
                f"URL: {link}",
            ]

            if fetch_articles:
                body = get_article_text(link)
                if body:
                    lines.append(f"BODY: {body}")

            blocks.append("\n".join(lines))

    return "\n\n---\n\n".join(blocks)


if __name__ == "__main__":
    print(fetch_feed())