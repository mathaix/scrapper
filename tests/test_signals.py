import http.server
import json
import threading
from datetime import date, timedelta

import httpx
import pytest

from fetch import Fetcher
from scraper import scrape

TODAY = date.today()
RECENT = [(TODAY - timedelta(days=d)).isoformat() for d in (3, 40)]
OLD = (TODAY - timedelta(days=200)).isoformat()

HOME = """<html><head><title>Acme Inc</title>
<meta name="description" content="Acme makes anvils">
<meta property="og:site_name" content="Acme"><meta property="og:image" content="/og.png">
<link rel="canonical" href="https://acme.example/">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"Acme Corp",
"logo":"/logo.png","sameAs":["https://www.linkedin.com/company/acme","https://twitter.com/acme"]}</script>
<script src="https://js.hs-scripts.com/123.js"></script>
<script src="https://www.google-analytics.com/analytics.js"></script>
</head><body><h1>Anvils for everyone</h1><p>We sell anvils.</p>
<a href="https://boards.greenhouse.io/acme">Careers</a>
<a href="https://github.com/acme">GitHub</a></body></html>"""

SITEMAP = f"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://acme.example/blog/one</loc><lastmod>{RECENT[0]}</lastmod></url>
<url><loc>https://acme.example/blog/two</loc><lastmod>{RECENT[1]}T10:00:00Z</lastmod></url>
<url><loc>https://acme.example/blog/old</loc><lastmod>{OLD}</lastmod></url>
<url><loc>https://acme.example/about</loc><lastmod>{TODAY.isoformat()}</lastmod></url></urlset>"""


@pytest.fixture
def fixture_site():
    pages = {"/": ("text/html", HOME), "/sitemap.xml": ("application/xml", SITEMAP)}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in pages:
                self.send_response(404)
                self.end_headers()
                return
            ctype, body = pages[self.path]
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass

    s = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()


def make_fetcher():
    real = httpx.HTTPTransport()

    class Router(httpx.BaseTransport):
        def handle_request(self, request):
            if request.url.host == "boards-api.greenhouse.io":
                assert request.url.path == "/v1/boards/acme/jobs"
                return httpx.Response(200, json={"jobs": [{"id": 1}, {"id": 2}, {"id": 3}]})
            return real.handle_request(request)

    return Fetcher(allow_private=True, transport=Router())


def test_fixture_site_signals(fixture_site):
    r = scrape(fixture_site + "/", fetcher=make_fetcher())
    assert r["signals"] == {
        "profile": {
            "name": "Acme Corp",
            "description": "Acme makes anvils",
            "logo": fixture_site + "/logo.png",
            "canonical_url": "https://acme.example/",
            "socials": {"linkedin": "https://www.linkedin.com/company/acme", "x": "https://twitter.com/acme",
                        "github": "https://github.com/acme", "facebook": None, "instagram": None, "youtube": None},
            "jsonld_types": ["Organization"],
        },
        "tech": ["google-analytics", "hubspot"],
        "hiring": {"careers_url": "https://boards.greenhouse.io/acme", "ats": "greenhouse", "open_roles": 3},
        "activity": {"latest_post_date": RECENT[0], "posts_last_90d": 2, "source": "sitemap"},
    }


def test_rss_fallback_and_lever_page():
    from signals import _feed_dates

    rss = "<rss><channel><item><pubDate>Tue, 10 Jun 2025 08:00:00 GMT</pubDate></item></channel></rss>"
    assert _feed_dates(rss) == [date(2025, 6, 10)]
    atom = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><updated>2025-01-02T00:00:00Z</updated></entry></feed>'
    assert _feed_dates(atom) == [date(2025, 1, 2)]
