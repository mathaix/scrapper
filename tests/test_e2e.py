import os
import socket
import threading
import time
from functools import partial

import uvicorn
from playwright.sync_api import sync_playwright

from jobs import run_job
from scraper import scrape
from tests.test_scraper import site  # noqa: F401
from tests.test_web import Store, fake_scrape
from web import create_app

AUTH_TOKEN = "s3cret"


def serve(app):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server, port


def test_user_scrapes_single_and_batch(site):  # noqa: F811
    store = Store()
    ai = {"industry": "Software", "services": [], "icp_summary": "Teams", "b2b_or_b2c": "b2b",
          "size_estimate": "11-50", "confidence": 0.8}

    def spawn(batch_id, jobs):
        for row_id, url in jobs:
            run_job(fake_scrape, store.save, url, batch_id, row_id)

    app = create_app(partial(scrape, allow_private=True, classify=lambda t: ai), store.save, token=AUTH_TOKEN,
                     spawn=spawn, queue_batch=store.queue, get_batch=store.get)
    server, port = serve(app)
    demo = os.environ.get("DEMO_DIR")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(record_video_dir=demo) if demo else b.new_context()
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.fill("#token", AUTH_TOKEN)
            page.fill("#url", site + "/")
            page.click("#go")
            page.wait_for_function("document.getElementById('status').textContent.includes('Saved')")
            assert 'Saved "Hi" with 2 links' in page.inner_text("#status")
            assert page.inner_text("#r-name") == "Acme"
            assert "hubspot" in page.inner_text("#r-tech")
            assert "AI summary: Software" in page.inner_text("#result")
            assert page.locator("#r-tech").count() == 1
            if demo:
                page.screenshot(path=os.path.join(demo, "single.png"))

            page.fill("#batch", "a.example\nbad.example")
            page.click("#batch-go")
            page.wait_for_function("document.querySelectorAll('#batch-table tbody tr').length === 2")
            page.wait_for_function("document.getElementById('batch-status').textContent.includes('queued 0')")
            rows = page.inner_text("#batch-table tbody")
            assert "a.example" in rows and "hubspot" in rows and "Couldn't resolve bad.example" in rows
            if demo:
                page.screenshot(path=os.path.join(demo, "batch.png"), full_page=True)
            ctx.close()
            b.close()
    finally:
        server.should_exit = True
    assert any(r.get("title") == "Hi" for r in store.rows.values())
