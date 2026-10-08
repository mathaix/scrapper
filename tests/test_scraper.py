import http.server
import threading

import pytest

from fetch import Fetcher
from htmlparse import parse
from scraper import scrape


@pytest.fixture
def site():
    html = (
        b'<title> Hi </title><meta property="og:site_name" content="Acme">'
        b'<script src="https://js.hs-scripts.com/1.js"></script><a href="/a">a</a><a href="http://x.org/">x</a><a href="mailto:a@b">m</a>'
    )

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *a):
            pass

    s = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()


def local_scrape(url, **kw):
    return scrape(url, allow_private=True, **kw)


def test_scrape(site):
    r = local_scrape(site + "/")
    assert r["title"] == "Hi"
    assert r["links"] == [site + "/a", "http://x.org/"]
    assert r["domain"] == "127.0.0.1" and r["status"] == "ok"


def test_bad_scheme():
    with pytest.raises(Exception, match="http"):
        scrape("file:///etc/passwd")


def test_links_deduplicated():
    page = parse(
        '<a href="http://X.com#a">1</a><a href="http://x.com/#b">2</a><a href="HTTP://x.com/">3</a>'
        '<a href="http://x.com">4</a><a href="http://x.com/p#q">5</a><a href="http://x.com/p">6</a>',
        "http://base/",
    )
    assert page.links == ["http://x.com/", "http://x.com/p"]
