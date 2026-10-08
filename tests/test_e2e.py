import os
import socket
import threading
import time

import uvicorn
from playwright.sync_api import sync_playwright

from scraper import scrape
from web import create_app
from tests.test_scraper import site  # noqa: F401


def test_user_scrapes_page(site):  # noqa: F811
    saved = []
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(create_app(scrape, saved.append), port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    demo = os.environ.get("DEMO_DIR")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(record_video_dir=demo) if demo else b.new_context()
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.fill("#url", site + "/")
            page.click("#go")
            page.wait_for_function("document.getElementById('status').textContent.includes('Saved')")
            assert 'Saved "Hi" with 2 links' in page.inner_text("#status")
            if demo:
                page.screenshot(path=os.path.join(demo, "scraped.png"))
            ctx.close()
            b.close()
    finally:
        server.should_exit = True
    assert saved and saved[0]["title"] == "Hi"
