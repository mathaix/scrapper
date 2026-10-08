# Web scraper – marketing signals

Scrapes a company site (or a batch of domains) into profile, socials, tech stack, hiring, activity and
AI-classified fit signals, and stores one snapshot row per scrape in Supabase.

## Setup

Modal secrets:

- `supabase` – `SUPABASE_URL`, `SUPABASE_KEY` (service_role key)
- `anthropic` – `ANTHROPIC_API_KEY` (if missing or the call fails, `ai` is `null`; scrapes never fail because of AI)
- `scraper-auth` – `API_TOKEN` (bearer token required by `/scrape` and `/batch`)

Apply `schema.sql` in the Supabase SQL editor (it is idempotent; running it twice is safe).
Merging to the default branch deploys with `modal deploy modal_app.py`.

## Calling the API

```sh
# single company (bare domains are fine)
curl -X POST https://mmathew--webscraper-web.modal.run/scrape \
  -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' -d '{"url": "claramap.com"}'

# batch: up to 500 domains/URLs ("urls" list, or "text" with one per line / CSV first column)
curl -X POST https://mmathew--webscraper-web.modal.run/batch \
  -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"text": "claramap.com\nexample.org"}'            # -> {"batch_id": "...", "total": 2}

curl https://mmathew--webscraper-web.modal.run/batch/<batch_id> -H "Authorization: Bearer $API_TOKEN"
# -> {"counts": {"queued": 0, "ok": 2, "error": 0}, "rows": [...]}
```

The web page at `/` does the same; paste the token into the token field (kept in `localStorage`).

## Tests

`pip install -r requirements.txt && python -m playwright install chromium && python -m pytest`
