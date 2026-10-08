# Web scraper – marketing signals

Scrapes a company site (or a batch of domains) into profile, socials, tech stack, hiring and
activity signals, and stores one snapshot row per scrape in Supabase.

## Setup

Modal secrets:

- `supabase` – `SUPABASE_URL`, `SUPABASE_KEY` (service_role key)

Apply `schema.sql` in the Supabase SQL editor (it is idempotent; running it twice is safe).
Merging to the default branch deploys with `modal deploy modal_app.py`.

## Calling the API

```sh
# single company (bare domains are fine)
curl -X POST https://claramap--signals.modal.run/scrape \
  -H 'Content-Type: application/json' -d '{"url": "claramap.com"}'

# batch: up to 500 domains/URLs ("urls" list, or "text" with one per line / CSV first column)
curl -X POST https://claramap--signals.modal.run/batch \
  -H 'Content-Type: application/json' \
  -d '{"text": "claramap.com\nexample.org"}'            # -> {"batch_id": "...", "total": 2}

curl https://claramap--signals.modal.run/batch/<batch_id>
# -> {"counts": {"queued": 0, "ok": 2, "error": 0}, "rows": [...]}
```

The web page at `/` does the same. The endpoint is unauthenticated: anyone with the URL can use it.

## Tests

`pip install -r requirements-dev.txt && python -m playwright install chromium && python -m pytest`

Schema tests need a Postgres 15+ and `TEST_DATABASE_URL` (they run in a throwaway `signals_test` schema), e.g.
`docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=pw postgres:17` and
`TEST_DATABASE_URL=postgresql://postgres:pw@localhost:5432/postgres`. Without it they are skipped.
