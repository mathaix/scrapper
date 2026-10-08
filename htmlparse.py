"""Single-pass HTML parser collecting everything the scraper and signals need."""
import json
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse


def normalize(url: str) -> str:
    """Drop the fragment, lowercase scheme + host, empty path -> '/'."""
    p = urlparse(urldefrag(url).url)
    return p._replace(scheme=p.scheme.lower(), netloc=p.netloc.lower(), path=p.path or "/").geturl()


class Page(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base = base
        self.title = ""
        self.links = []  # normalized, de-duplicated
        self.anchors = []  # (absolute url, text)
        self.meta = {}  # name/property -> content
        self.canonical = None
        self.feeds = []
        self.assets = []  # <script src> and <link href>
        self.jsonld = []  # parsed JSON-LD blocks
        self.headings = []
        self.text = []
        self._in_title = False
        self._skip = 0
        self._ld = None
        self._heading = None
        self._anchor = None

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "a" and a.get("href"):
            try:
                url = urljoin(self.base, a["href"])
                ok = urlparse(url).scheme in ("http", "https")
            except ValueError:
                ok = False
            if ok:
                n = normalize(url)
                if n not in self.links:
                    self.links.append(n)
                self._anchor = [url, ""]
                self.anchors.append(self._anchor)
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and "content" in a:
                self.meta.setdefault(key, a["content"].strip())
        elif tag == "link" and a.get("href"):
            rels = a.get("rel", "").lower().split()
            href = urljoin(self.base, a["href"])
            self.assets.append(href)
            if "canonical" in rels:
                self.canonical = href
            if "alternate" in rels and a.get("type", "").lower() in ("application/rss+xml", "application/atom+xml"):
                self.feeds.append(href)
        elif tag == "script":
            self._skip += 1
            if a.get("src"):
                self.assets.append(urljoin(self.base, a["src"]))
            if a.get("type", "").lower() == "application/ld+json":
                self._ld = ""
        elif tag == "style":
            self._skip += 1
        elif tag in ("h1", "h2", "h3"):
            self._heading = ""

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "a":
            self._anchor = None
        elif tag == "script":
            self._skip = max(0, self._skip - 1)
            if self._ld is not None:
                try:
                    self.jsonld.append(json.loads(self._ld))
                except ValueError:
                    pass
                self._ld = None
        elif tag == "style":
            self._skip = max(0, self._skip - 1)
        elif tag in ("h1", "h2", "h3") and self._heading is not None:
            if self._heading.strip():
                self.headings.append(" ".join(self._heading.split()))
            self._heading = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._ld is not None:
            self._ld += data
        elif not self._skip:
            self.text.append(data)
            if self._heading is not None:
                self._heading += data
            if self._anchor is not None:
                self._anchor[1] += data


def parse(html: str, base: str) -> Page:
    page = Page(base)
    page.feed(html)
    page.close()
    page.title = page.title.strip()
    return page
