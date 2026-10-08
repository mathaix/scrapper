"""Modal deployment: `modal deploy modal_app.py`.

Needs Modal secrets: `supabase` (SUPABASE_URL, SUPABASE_KEY), `anthropic` (ANTHROPIC_API_KEY)
and `scraper-auth` (API_TOKEN).
"""
import os

import modal

image = (
    modal.Image.debian_slim()
    .pip_install("fastapi[standard]", "httpx", "supabase", "anthropic", "pydantic")
    .add_local_python_source("scraper", "storage", "web", "fetch", "htmlparse", "signals", "ai", "jobs")
)
app = modal.App("webscraper", image=image)
supabase = modal.Secret.from_name("supabase")
anthropic = modal.Secret.from_name("anthropic")


@app.function(secrets=[supabase, anthropic], timeout=300)
def scrape_and_save(url: str, batch_id: str | None = None, row_id: int | None = None) -> dict:
    from functools import partial

    from ai import classify_with_claude
    from jobs import run_job
    from scraper import scrape
    from storage import save_to_supabase

    return run_job(partial(scrape, classify=classify_with_claude), save_to_supabase, url, batch_id, row_id)


@app.function(secrets=[supabase, anthropic, modal.Secret.from_name("scraper-auth")])
@modal.asgi_app()
def web():
    from functools import partial

    from ai import classify_with_claude
    from scraper import scrape
    from storage import get_batch, queue_batch, save_to_supabase
    from web import create_app

    def spawn(batch_id, jobs):
        for row_id, url in jobs:
            scrape_and_save.spawn(url, batch_id, row_id)

    return create_app(
        partial(scrape, classify=classify_with_claude),
        save_to_supabase,
        token=os.environ.get("API_TOKEN"),
        spawn=spawn,
        queue_batch=queue_batch,
        get_batch=get_batch,
    )
