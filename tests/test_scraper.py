import http.server
import threading

import pytest

from scraper import scrape


@pytest.fixture
def site():
    html = b'<title> Hi </title><a href="/a">a</a><a href="http://x.org/">x</a><a href="mailto:a@b">m</a>'

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
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


def test_scrape(site):
    r = scrape(site + "/")
    assert r["title"] == "Hi"
    assert r["links"] == [site + "/a", "http://x.org/"]


def test_bad_scheme():
    with pytest.raises(ValueError):
        scrape("file:///etc/passwd")
