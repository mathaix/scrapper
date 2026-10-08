# Signals

Signals is a rules-based collector of marketing signals. You send it company websites or event pages, and it
builds a queryable Supabase database of companies, people and events. That data feeds target lists, timing
triggers and event-led outreach.

**Live app: https://claramap--signals.modal.run** (public, no login: anyone with the URL can use it).

## What it captures

Available today:

- Company profile and social links
- Tech stack (marketing, analytics and CMS tools detected from page markup)
- Hiring: ATS (Greenhouse, Lever, Ashby) and open roles
- Blog activity (latest post dates from sitemap or feed)

Planned (not built yet; see the linked issues):

- Events, funding, earnings and press signals on a company ([#6](https://github.com/mathaix/signals/issues/6)–[#13](https://github.com/mathaix/signals/issues/13))
- Sponsors, exhibitors and speakers extracted from event pages ([#6](https://github.com/mathaix/signals/issues/6)–[#13](https://github.com/mathaix/signals/issues/13))

## How it works

URL → rules-based extraction (no AI) → Supabase. Each scrape stores one snapshot row; batches of up to 500
URLs run as background jobs. The app is deployed on Modal, and merging to `main` deploys it.

## Docs

- [Product requirements](docs/prd-signals-db.md)
- [Final design](docs/designs/final.md)
- [Research](docs/research-signals-practice.md)
- [AGENTS.md](AGENTS.md) (how to work on issues in this repo)

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
