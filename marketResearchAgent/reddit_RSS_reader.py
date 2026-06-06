import feedparser
import requests
from bs4 import BeautifulSoup

URLS = [
    "https://www.reddit.com/r/CryptoCurrency/.rss",
    "https://www.reddit.com/r/CryptoMarkets/.rss",
    "https://www.reddit.com/r/DeFi/.rss",
    "https://www.reddit.com/r/Bitcoin/.rss",
]


def get_post_text(url: str) -> str:
    """Scrape the selftext body from a Reddit post page."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        # Reddit post body lives in <div data-testid="post-container"> paragraphs
        paragraphs = soup.select('[data-testid="post-container"] p')
        return " ".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
    except Exception:
        return ""


def fetch_feed(urls: list = URLS, fetch_articles: bool = False, max_items: int = 20) -> str:
    """
    Fetches Reddit RSS feeds (Atom format) and returns a plain-text string of
    post titles + metadata, mirroring the Yahoo Finance RSS reader output.

    Args:
        urls:            List of Reddit RSS feed URLs to pull from.
        fetch_articles:  If True, fetches and appends scraped post body text (slower).
        max_items:       Max number of posts to include per feed.

    Returns:
        A formatted string with one post block per item.
    """
    blocks = []

    for url in urls:
        feed = feedparser.parse(url)

        # Derive subreddit name from the feed category or fall back to the URL
        subreddit = ""
        if feed.feed.get("tags"):
            subreddit = feed.feed.tags[0].get("label", "")  # e.g. "r/Bitcoin"
        if not subreddit:
            # Extract from URL: ".../r/Bitcoin/.rss" -> "r/Bitcoin"
            parts = url.rstrip("/").split("/")
            try:
                r_idx = parts.index("r")
                subreddit = f"r/{parts[r_idx + 1]}"
            except (ValueError, IndexError):
                subreddit = url

        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            # Reddit Atom feeds store the link in entry.link (feedparser resolves href)
            link = entry.get("link", "").strip()
            author = entry.get("author", "").strip()          # e.g. "/u/username"
            date = entry.get("published", entry.get("updated", ""))

            lines = [
                f"HEADLINE: {title}",
                f"SUBREDDIT: {subreddit}",
                f"AUTHOR: {author}",
                f"DATE: {date}",
                f"URL: {link}",
            ]

            if fetch_articles:
                body = get_post_text(link)
                if body:
                    lines.append(f"BODY: {body}")

            blocks.append("\n".join(lines))

    return "\n\n---\n\n".join(blocks)


if __name__ == "__main__":
    print(fetch_feed())