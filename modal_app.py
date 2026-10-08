"""Modal deployment: `modal deploy modal_app.py`.

Needs a Modal secret named `supabase` with SUPABASE_URL and SUPABASE_KEY.
"""
import modal

image = modal.Image.debian_slim().pip_install("fastapi[standard]", "httpx", "supabase").add_local_python_source(
    "scraper", "storage", "web"
)
app = modal.App("webscraper", image=image)


@app.function(secrets=[modal.Secret.from_name("supabase")])
def scrape_and_save(url: str) -> dict:
    from scraper import scrape
    from storage import save_to_supabase

    record = scrape(url)
    save_to_supabase(record)
    return record


@app.function(secrets=[modal.Secret.from_name("supabase")])
@modal.asgi_app()
def web():
    from scraper import scrape
    from storage import save_to_supabase
    from web import create_app

    return create_app(scrape, save_to_supabase)
