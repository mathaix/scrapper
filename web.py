"""Web app: scrape one company or a batch of domains; results go to the database."""
import csv
import hmac
import io
import logging
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from fetch import FetchError
from scraper import normalize_input

log = logging.getLogger(__name__)
MAX_BATCH = 500

PAGE = """<!doctype html><meta charset="utf-8"><title>Scraper</title>
<style>
body{font:15px system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem}
.chip{display:inline-block;background:#e8f0fe;border-radius:1rem;padding:.1rem .6rem;margin:.1rem}
#result{border:1px solid #ccc;border-radius:.5rem;padding:0 1rem;margin:1rem 0}
table{border-collapse:collapse;width:100%} td,th{border:1px solid #ccc;padding:.3rem;text-align:left}
</style>
<h1>Web scraper</h1>
<p><label>API token <input id="token" type="password" size="40"></label></p>
<h2>Single company</h2>
<form id="f"><input id="url" name="url" placeholder="claramap.com or https://example.com" size="50">
<button id="go">Scrape</button></form>
<p id="status"></p>
<div id="result" hidden></div>
<h2>Batch</h2>
<form id="bf"><textarea id="batch" rows="6" cols="50" placeholder="One domain or URL per line (max 500)"></textarea><br>
<label>or CSV (first column) <input type="file" id="csv" accept=".csv,text/csv,text/plain"></label>
<button id="batch-go">Scrape batch</button></form>
<p id="batch-status"></p>
<table id="batch-table" hidden><thead><tr><th>Domain</th><th>Status</th><th>Name</th><th>Tech</th>
<th>Hiring</th><th>Latest post</th><th>Industry</th></tr></thead><tbody></tbody></table>
<script>
const $ = (id) => document.getElementById(id);
const token = $('token');
token.value = localStorage.getItem('token') || '';
token.oninput = () => localStorage.setItem('token', token.value);
const headers = () => ({'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token.value});
function el(tag, text, cls, parent) {
  const e = document.createElement(tag);
  if (text != null) e.textContent = text;
  if (cls) e.className = cls;
  if (parent) parent.appendChild(e);
  return e;
}
async function call(path, opts) {
  const r = await fetch(path, opts);
  let d = {};
  try { d = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(r.status === 401 ? 'Missing or invalid API token' : (d.detail || 'Request failed'));
  return d;
}
function renderResult(d) {
  const box = $('result'), s = d.signals || {}, p = s.profile || {}, h = s.hiring || {}, a = s.activity || {};
  box.replaceChildren();
  el('h3', p.name || d.title || d.domain, null, box).id = 'r-name';
  if (p.description) el('p', p.description, null, box);
  const soc = el('p', 'Social: ', null, box);
  Object.entries(p.socials || {}).filter(([k, v]) => v && /^https?:/.test(v)).forEach(([k, v]) => {
    const l = el('a', k, null, soc); l.href = v; soc.append(' ');
  });
  const t = el('p', 'Tech: ', null, box); t.id = 'r-tech';
  (s.tech || []).forEach((x) => el('span', x, 'chip', t));
  if (!(s.tech || []).length) t.append('none detected');
  el('p', 'Hiring: ' + (h.careers_url ? `${h.careers_url} (ATS: ${h.ats || 'unknown'}, open roles: ${h.open_roles ?? 'n/a'})` : 'no careers page found'), null, box);
  el('p', 'Activity: ' + (a.source ? `latest post ${a.latest_post_date}, ${a.posts_last_90d} in last 90 days (${a.source})` : 'no blog activity found'), null, box);
  const ai = s.ai;
  el('p', 'AI summary: ' + (ai ? `${ai.industry} · ${ai.b2b_or_b2c} · ${ai.size_estimate} · ${ai.icp_summary}` : 'not available'), null, box);
  box.hidden = false;
}
$('f').onsubmit = async (e) => {
  e.preventDefault();
  const s = $('status');
  s.textContent = 'Scraping...';
  $('result').hidden = true;
  try {
    const d = await call('/scrape', {method: 'POST', headers: headers(), body: JSON.stringify({url: $('url').value})});
    s.textContent = `Saved "${d.title}" with ${d.links.length} links`;
    renderResult(d);
  } catch (err) { s.textContent = 'Error: ' + err.message; }
};
$('csv').onchange = async () => { const f = $('csv').files[0]; if (f) $('batch').value = await f.text(); };
function renderRows(rows) {
  const body = document.querySelector('#batch-table tbody');
  body.replaceChildren();
  rows.forEach((r) => {
    const s = r.signals || {}, tr = el('tr', null, null, body);
    el('td', r.domain || r.url, null, tr);
    el('td', r.status === 'error' ? 'error: ' + r.error : r.status, null, tr);
    el('td', (s.profile || {}).name || '', null, tr);
    el('td', (s.tech || []).join(', '), null, tr);
    const h = s.hiring || {};
    el('td', h.open_roles != null ? h.open_roles + ' roles' : (h.careers_url ? 'careers page' : ''), null, tr);
    el('td', (s.activity || {}).latest_post_date || '', null, tr);
    el('td', (s.ai || {}).industry || '', null, tr);
  });
  $('batch-table').hidden = false;
}
async function poll(id) {
  try {
    const d = await call('/batch/' + id, {headers: headers()});
    const c = d.counts;
    $('batch-status').textContent = `queued ${c.queued} · ok ${c.ok} · error ${c.error}`;
    renderRows(d.rows);
    if (c.queued > 0) setTimeout(() => poll(id), 1000);
  } catch (err) { $('batch-status').textContent = 'Error: ' + err.message; }
}
$('bf').onsubmit = async (e) => {
  e.preventDefault();
  $('batch-status').textContent = 'Submitting...';
  try {
    const d = await call('/batch', {method: 'POST', headers: headers(), body: JSON.stringify({text: $('batch').value})});
    $('batch-status').textContent = `Queued ${d.total} items`;
    poll(d.batch_id);
  } catch (err) { $('batch-status').textContent = 'Error: ' + err.message; }
};
</script>"""


class ScrapeRequest(BaseModel):
    url: str


class BatchRequest(BaseModel):
    urls: list[str] | None = None
    text: str | None = None  # one domain/URL per line; CSV allowed (first column is used)


def parse_items(req: BatchRequest) -> list:
    raw = list(req.urls or [])
    if req.text:
        raw += [row[0] for row in csv.reader(io.StringIO(req.text)) if row and row[0].strip()]
    items = []
    for r in raw:
        if r.strip():
            url = normalize_input(r)
            if url not in items:
                items.append(url)
    return items


def create_app(scrape, save, *, token=None, spawn=None, queue_batch=None, get_batch=None) -> FastAPI:
    """scrape(url) -> record; save(record); token: required bearer token (None rejects everything);
    spawn(batch_id, [(row_id, url)]), queue_batch(batch_id, urls) -> [{id, url}] and
    get_batch(batch_id) -> rows implement batch processing."""
    app = FastAPI()

    def auth(authorization: str | None = Header(None)):
        expected = f"Bearer {token}" if token else None
        if not expected or not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(401, "Missing or invalid API token", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE

    @app.post("/scrape", dependencies=[Depends(auth)])
    def do_scrape(req: ScrapeRequest):
        try:
            record = scrape(normalize_input(req.url))
        except (FetchError, ValueError) as e:
            raise HTTPException(400, str(e))
        except Exception:
            log.exception("Scrape failed for %r", req.url)
            raise HTTPException(500, "Something went wrong on our side")
        try:
            save(record)
        except Exception:
            log.exception("Saving scrape result failed for %r", req.url)
            raise HTTPException(500, "Something went wrong on our side")
        return record

    @app.post("/batch", dependencies=[Depends(auth)])
    def do_batch(req: BatchRequest):
        if not (spawn and queue_batch):
            raise HTTPException(503, "Batch processing isn't configured")
        try:
            urls = parse_items(req)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not urls:
            raise HTTPException(400, "No domains or URLs given")
        if len(urls) > MAX_BATCH:
            raise HTTPException(400, f"Too many items ({len(urls)}); the maximum is {MAX_BATCH}")
        batch_id = str(uuid.uuid4())
        try:
            rows = queue_batch(batch_id, urls)
            spawn(batch_id, [(r["id"], r["url"]) for r in rows])
        except Exception:
            log.exception("Starting batch failed")
            raise HTTPException(500, "Something went wrong on our side")
        return {"batch_id": batch_id, "total": len(urls)}

    @app.get("/batch/{batch_id}", dependencies=[Depends(auth)])
    def batch_status(batch_id: str):
        try:
            uuid.UUID(batch_id)
        except ValueError:
            raise HTTPException(400, "Invalid batch id")
        if not get_batch:
            raise HTTPException(503, "Batch processing isn't configured")
        try:
            rows = get_batch(batch_id)
        except Exception:
            log.exception("Reading batch %s failed", batch_id)
            raise HTTPException(500, "Something went wrong on our side")
        if not rows:
            raise HTTPException(404, "Unknown batch")
        counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("queued", "ok", "error")}
        return {"batch_id": batch_id, "counts": counts, "rows": rows}

    return app
