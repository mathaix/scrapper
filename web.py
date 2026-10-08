"""Web page where a user enters a URL to scrape; results go to the database."""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

PAGE = """<!doctype html><title>Scraper</title>
<h1>Web scraper</h1>
<form id="f"><input id="url" name="url" placeholder="https://example.com" size="50">
<button id="go">Scrape</button></form>
<p id="status"></p>
<script>
document.getElementById('f').onsubmit = async (e) => {
  e.preventDefault();
  const s = document.getElementById('status');
  s.textContent = 'Scraping...';
  const r = await fetch('/scrape', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url: document.getElementById('url').value})});
  const d = await r.json();
  s.textContent = r.ok ? `Saved "${d.title}" with ${d.links.length} links` : `Error: ${d.detail}`;
};
</script>"""


class ScrapeRequest(BaseModel):
    url: str


def create_app(scrape, save) -> FastAPI:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE

    @app.post("/scrape")
    def do_scrape(req: ScrapeRequest):
        from fastapi import HTTPException

        try:
            record = scrape(req.url.strip())
            save(record)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        return record

    return app
