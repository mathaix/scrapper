"""Scrape a webpage: title and links (as in Modal's webscraper example)."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx


class _Parser(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base, self.links, self.title, self._in_title = base, [], "", False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                url = urljoin(self.base, href)
                if urlparse(url).scheme in ("http", "https") and url not in self.links:
                    self.links.append(url)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def scrape(url: str) -> dict:
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https://")
    resp = httpx.get(url, follow_redirects=True, timeout=20)
    resp.raise_for_status()
    parser = _Parser(str(resp.url))
    parser.feed(resp.text)
    return {"url": url, "title": parser.title.strip(), "links": parser.links}
