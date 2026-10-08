# Variant C: Minimal pragmatic

Designer C · 2026-10-08 · Builds on `docs/prd-signals-db.md`

## 1. Summary and philosophy

1. **Five tables:** `companies`, `people`, `events`, `appearances` (a single link table between an entity and an event, with a role) and `scrapes`.
2. **`scrapes` does three jobs.** It is the batch queue and status (Q8). It is the provenance log (FR6). Its typed columns also hold the company-signal snapshot for each scrape (tech, ATS, open roles, blog activity), so it is the history as well (Q5, Q7).
3. **"Current" is a view and is never stored twice.** `companies_current` is the company row plus its latest successful scrape. Re-scraping inserts one row, so FR4 idempotency holds without update logic.
4. **One `events` table holds both meanings.** Conferences and meetups the owner submits sit next to funding, earnings and press items found on company sites, separated by `kind`. A company is linked to its funding round the same way it is linked to a conference it sponsors: through `appearances`, with role `subject`.
5. **No JSON columns at all.** Tech stack is a `text[]`, queried with `= any(tech)`. Every acceptance query is plain SQL with joins, verified against a real Postgres 17 (Supabase image).

**Philosophy.** One person maintains this. Every table, column and view is something they will read in the Supabase table editor at 11pm while building a list. That means:

- No table exists unless a PRD question needs it. A separate tech-history table, a fact/observation table, raw page storage, confidence scores and job history were all considered and left out. Section 8 shows each one can be added later with a single `add column` or `create table`.
- History lives only where a question asks about change: tech, ATS, open roles and blog activity, which Q5 and Q7 need. Name, description and socials are overwritten in place, because no PRD question asks how a company's tagline changed.
- Derive, don't sync. Nothing in the schema needs a trigger or a second write to stay consistent. The only denormalized value is `companies.last_scraped_at`, which is used to decide whether to enqueue a company again.
- Writes use PostgREST upserts with `on_conflict`, the same `supabase` client already in use. There are no stored procedures and no ORM.

## 2. DDL

Run as the new `schema.sql`. Running it repeatedly is safe: create-if-not-exists for tables and indexes, and drop-then-create for views so a column change never fails. It was validated by running it 3 times (empty database, re-run, re-run with data) on `supabase/postgres:17.6.1`. `unique nulls not distinct` needs PG15 or later, and Supabase meets that.

```sql
-- Marketing signals DB (variant C, "minimal pragmatic"). Idempotent: safe to run repeatedly.
-- 5 tables: companies, people, events, appearances (who-was-at/what-happened-to), scrapes (log + batch + signal snapshots).
-- 3 views: companies_current, company_history, tech_seen.
-- RLS on, no policies: only the service_role key (server-side) can read/write.

create table if not exists companies (
  id              bigint generated always as identity primary key,
  domain          text not null unique,          -- registrable domain, lowercase, no www: "acme.com"
  name            text,
  description     text,
  logo_url        text,
  linkedin_url    text,
  x_url           text,
  github_url      text,
  facebook_url    text,
  instagram_url   text,
  youtube_url     text,
  first_seen_at   timestamptz not null default now(),
  last_scraped_at timestamptz                   -- null = discovered (e.g. on an event page) but never scraped
);

create table if not exists events (
  id             bigint generated always as identity primary key,
  event_key      text not null unique,          -- canonical URL, or "funding:acme.com:2026-09" (see dedup rules)
  kind           text not null default 'event'
                 constraint events_kind_check check (kind in ('event', 'webinar', 'funding', 'earnings', 'press')),
  title          text,
  title_key      text generated always as (lower(regexp_replace(coalesce(title, ''), '[^a-zA-Z0-9]+', '', 'g'))) stored,
  url            text,
  event_date     date,                          -- start date for gatherings; announcement date for funding/earnings/press
  location       text,
  amount_usd     bigint,                        -- funding only, when "$X million" is found
  round          text,                          -- funding only: 'seed', 'series a', ...
  first_seen_at  timestamptz not null default now(),
  last_seen_at   timestamptz not null default now()
);

create table if not exists people (
  id             bigint generated always as identity primary key,
  person_key     text not null unique,          -- "li:jane-doe-123" or "nm:janedoe@acme.com" / "nm:janedoe@event:42"
  name           text not null,
  job_title      text,
  linkedin_url   text,
  company_id     bigint references companies(id) on delete set null,
  company_name   text,                          -- as printed on the page, kept even when we cannot resolve a domain
  first_seen_at  timestamptz not null default now(),
  last_seen_at   timestamptz not null default now()
);

create table if not exists scrapes (
  id               bigint generated always as identity primary key,
  batch_id         uuid,
  parent_id        bigint references scrapes(id) on delete set null,  -- set on company scrapes spawned by an event page
  url              text not null,               -- as submitted
  final_url        text,                        -- after redirects
  kind             text constraint scrapes_kind_check check (kind in ('company', 'event')),  -- null while queued
  status           text not null default 'queued'
                   constraint scrapes_status_check check (status in ('queued', 'ok', 'error')),
  error            text,
  title            text,
  company_id       bigint references companies(id) on delete cascade,
  event_id         bigint references events(id) on delete cascade,
  created_at       timestamptz not null default now(),
  scraped_at       timestamptz,                 -- when the fetch finished (ok or error)
  -- company signal snapshot (null on event scrapes). History = these rows over time.
  tech             text[],
  ats              text,
  careers_url      text,
  open_roles       int,
  latest_post_date date,
  posts_last_90d   int,
  constraint scrapes_one_subject check (company_id is null or event_id is null)
);

-- Who appeared at / is the subject of an event. Exactly one of company_id / person_id.
create table if not exists appearances (
  id              bigint generated always as identity primary key,
  event_id        bigint not null references events(id) on delete cascade,
  company_id      bigint references companies(id) on delete cascade,
  person_id       bigint references people(id) on delete cascade,
  role            text not null
                  constraint appearances_role_check check (role in
                    ('sponsor', 'exhibitor', 'partner', 'organizer', 'speaker', 'listed', 'host', 'subject')),
  confirmed       boolean,                      -- owner correction: null = as extracted, true = checked, false = wrong (hidden)
  first_scrape_id bigint references scrapes(id) on delete set null,
  last_scrape_id  bigint references scrapes(id) on delete set null,
  first_seen_at   timestamptz not null default now(),
  last_seen_at    timestamptz not null default now(),
  constraint appearances_one_party check ((company_id is null) <> (person_id is null)),
  constraint appearances_dedup unique nulls not distinct (event_id, company_id, person_id, role)
);

create index if not exists scrapes_batch_idx on scrapes (batch_id);
create index if not exists scrapes_company_ok_idx on scrapes (company_id, scraped_at desc) where status = 'ok';
create index if not exists scrapes_event_idx on scrapes (event_id);
create index if not exists appearances_company_idx on appearances (company_id);
create index if not exists appearances_person_idx on appearances (person_id);
create index if not exists events_kind_date_idx on events (kind, event_date);
create index if not exists events_date_title_idx on events (event_date, title_key);
create index if not exists people_company_idx on people (company_id);

alter table companies   enable row level security;
alter table events      enable row level security;
alter table people      enable row level security;
alter table scrapes     enable row level security;
alter table appearances enable row level security;

-- Views are dropped and recreated so column changes never trip "cannot change view column".
drop view if exists companies_current;
drop view if exists company_history;
drop view if exists tech_seen;

-- One row per company: profile + signals from its latest successful scrape.
create view companies_current with (security_invoker = true) as
select c.id, c.domain, c.name, c.description, c.logo_url, c.linkedin_url, c.x_url, c.github_url,
       c.facebook_url, c.instagram_url, c.youtube_url, c.first_seen_at, c.last_scraped_at,
       s.id as scrape_id, s.tech, s.ats, s.careers_url, s.open_roles, s.latest_post_date, s.posts_last_90d
from companies c
left join lateral (
  select * from scrapes s
  where s.company_id = c.id and s.status = 'ok'
  order by s.scraped_at desc
  limit 1
) s on true;

-- One row per successful company scrape, with the previous scrape's values alongside.
create view company_history with (security_invoker = true) as
select h.*,
       case when h.prev_scrape_id is null then '{}'::text[]
            else array(select unnest(h.tech) except select unnest(h.prev_tech)) end as tech_added,
       case when h.prev_scrape_id is null then '{}'::text[]
            else array(select unnest(h.prev_tech) except select unnest(h.tech)) end as tech_removed
from (
  select s.company_id, s.id as scrape_id, s.scraped_at, s.url,
         s.tech,       lag(s.tech)       over w as prev_tech,
         s.ats,        lag(s.ats)        over w as prev_ats,
         s.open_roles, lag(s.open_roles) over w as prev_open_roles,
         s.latest_post_date, s.posts_last_90d,
         lag(s.id) over w as prev_scrape_id
  from scrapes s
  where s.status = 'ok' and s.company_id is not null
  window w as (partition by s.company_id order by s.scraped_at)
) h;

-- One row per (company, tool): when we first/last saw it, and whether an earlier scrape saw the company WITHOUT it
-- (adopted = true means a real change we witnessed, not just "it was there the first time we looked").
create view tech_seen with (security_invoker = true) as
with seen as (
  select s.company_id, t.tool, min(s.scraped_at) as first_seen_at, max(s.scraped_at) as last_seen_at
  from scrapes s cross join lateral unnest(s.tech) as t(tool)
  where s.status = 'ok'
  group by s.company_id, t.tool
)
select seen.company_id, seen.tool, seen.first_seen_at, seen.last_seen_at,
       exists (select 1 from scrapes p
               where p.company_id = seen.company_id and p.status = 'ok'
                 and p.scraped_at < seen.first_seen_at) as adopted
from seen;

-- Old single-table design held only test rows (PRD §7). Remove it.
drop table if exists scraped_pages;
```

**Why these shapes:**
- **Socials are 6 plain columns, not a map.** The set is fixed in `signals.SOCIAL_HOSTS`, so columns are boring and filterable.
- **`events.title_key`** is a generated column, used only to find the same event listed on two hosts (§3).
- **`appearances_dedup` uses `nulls not distinct`.** Because exactly one of `company_id` or `person_id` is set, one plain unique constraint covers both company links and person links. That means PostgREST `upsert(on_conflict="event_id,company_id,person_id,role")` works directly, with no partial-index tricks.
- **Check constraints on `kind`, `role` and `status`** catch typos in the extractor. Adding a value means changing one line: drop the constraint and add it again.
- **`security_invoker` views.** Only service_role reads them today, and if anon access is ever added, the views will not bypass RLS.

## 3. Identity and dedup rules

Every entity has **exactly one natural-key column with a unique constraint**. The Python code computes the key and every write is `upsert(..., on_conflict=<key>)`. The key rules live in one small function each, in `storage.py`.

| Entity | Key column | Rule |
|---|---|---|
| Company | `companies.domain` | Registrable domain of the URL: lowercase host, strip `www.`, keep the last 2 labels, or 3 when the last two are a known second-level suffix (`co.uk`, `com.au`, `co.jp`, …, a list of about 15). `jobs.acme.com` and `www.acme.com` both become `acme.com`. |
| Person | `people.person_key` | `li:<slug>` taken from a `linkedin.com/in/<slug>` URL, lowercased with the trailing slash and query removed. If there is no LinkedIn URL: `nm:<name_key>@<company domain>` when the company resolved, else `nm:<name_key>@event:<event_id>`. `name_key` is lowercase letters and digits only. |
| Event (gathering) | `events.event_key` | Canonical URL (`<link rel=canonical>` or final URL): lowercase host, no `www.`, no query or fragment, no trailing slash. **Cross-host dedup:** before inserting a new key, look up `events where event_date = $d and title_key = $k`. On a hit, reuse that row and don't insert. |
| Event (funding) | `events.event_key` | `funding:<domain>:<yyyy-mm>`, so one round per company per month. The same raise announced on both the newsroom and the blog collapses into one row. |
| Event (earnings) | `events.event_key` | `earnings:<domain>:<yyyy-mm-dd>` |
| Event (press, hosted webinar) | `events.event_key` | Canonical URL of the item |
| Appearance | `(event_id, company_id, person_id, role)` | One row per party per role per event. The same company as both sponsor and exhibitor gives 2 rows, which is intentional. |

**How upserts work (supabase-py), two modes:**

- **Observe**: we scraped the thing itself. `upsert(row, on_conflict=key)` overwrites the columns in the payload. PostgREST only updates columns that are present, so `first_seen_at` (default `now()`) is never touched again.
- **Discover**: we only saw a link to it, such as a company logo link on an event page. `upsert(rows, on_conflict="domain", ignore_duplicates=True)` turns into `ON CONFLICT DO NOTHING`, then `select id, domain ... in domain`. Link text from a sponsor grid therefore never overwrites a name we got from the company's own site.
- **Appearances**: always upsert with `last_seen_at = now()` and `last_scrape_id = <this scrape>`, and `first_scrape_id` only on insert. In practice the payload includes `first_scrape_id` and we accept that it is overwritten on conflict. If that matters, drop it from the payload and use the oldest scrape of the event instead.
- **People**: observe mode. The latest page wins for `job_title`, `company_id` and `company_name`.

Re-scraping the same URL twice gives the same companies, people, events and appearances, plus one more `scrapes` row. This was verified: replaying an appearance insert with `ON CONFLICT` left the count unchanged.

## 4. History and change detection

History is **the `scrapes` rows for a company, ordered by `scraped_at`**. Each successful company scrape writes one snapshot row containing `tech text[]`, `ats`, `careers_url`, `open_roles`, `latest_post_date` and `posts_last_90d`. Two views turn that into change signals:

- **"Adopted HubSpot since D":** the `tech_seen` view gives one row per (company, tool) with `first_seen_at`, `last_seen_at` and `adopted`. `adopted` is true when an earlier ok scrape of that company did **not** include the tool, so we saw the change happen and are not just counting what was there the first time we looked. The PRD's Q5 says "first seen after D", which is `first_seen_at >= D`. Adding `and adopted` makes it stricter: a real adoption, not a first observation.
- **"Open roles 2 → 9":** the `company_history` view puts `prev_open_roles`, `prev_ats` and `prev_tech` next to the current values using `lag()`, and also computes `tech_added` and `tech_removed` arrays. "Hiring surge" is `where open_roles > coalesce(prev_open_roles, 0) + 3`, or whatever threshold the owner wants, in plain SQL.
- **"Raised funding in the last 90 days":** this is not a diff. It is a dated fact: `events.kind = 'funding' and event_date >= current_date - 90`, with the company linked through `appearances.role = 'subject'`. `event_date` is the press item's date (RSS `pubDate` or the page's `datePublished`).
- **"Sponsoring an event next month":** `appearances.role = 'sponsor'` joined to `events.event_date between …`.
- **Appearance history:** `appearances.first_seen_at` and `last_seen_at` show when a sponsor first appeared on a page and whether it was still there on the latest scrape (compare `appearances.last_scrape_id` with the event's latest scrape).

**Deliberately not historised:** company name, description, logo and socials; event title and date; person job title and employer. No PRD question asks about them. If one does later, the change is one snapshot column on `scrapes`.

## 5. Ingestion flow

```
POST /batch (≤500) or POST /scrape
  └─ INSERT scrapes(batch_id, url, status='queued')                  ← Q8 is visible immediately
  └─ spawn worker(scrape_id, url)
worker (jobs.run_job):
  1. fetch + parse (existing fetch.py / htmlparse.py, SSRF guard, robots, caps unchanged)
  2. classify(page, url) -> 'event' | 'company'
  3a. company  → signals.collect() + signals.company_events()
  3b. event    → eventpage.extract()
  4. write rows (below), then UPDATE scrapes SET status='ok', kind, company_id|event_id, signal columns, scraped_at
  on any exception: UPDATE scrapes SET status='error', error=<msg>, scraped_at=now()   ← FR7, batch keeps going
```

**Classify (rule order, first match wins):**
1. The host is in `EVENT_HOSTS` (`lu.ma`, `eventbrite.*`, `meetup.com`, `hopin.com`, `sessionize.com`, …) → event.
2. A top-level JSON-LD node has `@type` Event (or a subtype) with `startDate`, and no Organization node is the page's main entity → event.
3. The page has at least 8 distinct outbound registrable domains (excluding social, ticketing and CDN hosts) **and** a heading matching `sponsor|exhibitor|partner|speaker` → event.
4. Otherwise → company.

The owner can override this: `POST /scrape {"url": ..., "kind": "event"}`, and batch CSV accepts an optional second column `event` or `company`. That one optional field is the whole correction story for classification.

**Rows each path writes:**

| Step | Company path | Event path |
|---|---|---|
| Entity | `companies` upsert (observe): profile + socials + `last_scraped_at` | `events` upsert (observe) on `event_key`, after the cross-host title/date check |
| Children | `events` upsert for each found item: funding, earnings, press, hosted webinars or meetups (JSON-LD Event, links to event platforms, `/events`, newsroom RSS, IR page) | `companies` upsert (discover) for each outbound company link. `people` upsert (observe) for each JSON-LD Person/performer or speaker card with a LinkedIn link |
| Links | `appearances` upsert: company to each child event, role `subject` (funding, earnings, press) or `host` (webinar or meetup) | `appearances` upsert: company to event, role from the nearest preceding heading (`sponsor`, `exhibitor`, `partner`, `organizer`, else `listed`). Person to event, role `speaker` |
| Scrape row | `scrapes` ← `status='ok'`, `kind='company'`, `company_id`, tech, ats, careers_url, open_roles, latest_post_date, posts_last_90d | `scrapes` ← `status='ok'`, `kind='event'`, `event_id`, title |
| Fan-out | none | For discovered companies with `last_scraped_at is null or < now() - 30 days`: insert `scrapes(batch_id = parent's, parent_id = this scrape, status='queued')` and spawn. Capped at 200 per event page. |

The scrape row is marked `ok` **last**. If a worker crashes midway, the scrape shows `error` or `queued` and a re-run is safe because every write is an upsert. No transaction is needed.

**Batch status (Q8)** is just `scrapes where batch_id = ?`. Rows with `parent_id is null` are what the owner submitted. Rows with `parent_id` are the companies found on their event pages, so the UI can show "event page ok, 37 companies queued, 30 done".

## 6. Acceptance queries

All of these ran against the DDL above with seed data on Postgres 17. None uses a JSON path expression.

**Q1: Sponsors and exhibitors of event X, with tech stack and open roles**
```sql
select c.domain, c.name, a.role, c.tech, c.ats, c.open_roles
from events e
join appearances a on a.event_id = e.id and a.confirmed is not false
join companies_current c on c.id = a.company_id
where e.url = 'https://lu.ma/saas-summit' and a.role in ('sponsor', 'exhibitor')
order by a.role, c.name;
```
Companies that were discovered but not scraped yet show `null` tech and roles until their child scrape finishes.

**Q2: Companies using HubSpot (tool T) that are hiring**
```sql
select domain, name, open_roles, careers_url
from companies_current
where 'hubspot' = any(tech) and open_roles > 0
order by open_roles desc;
```

**Q3: Companies with a funding event in the last 90 days, with amount when known**
```sql
select c.domain, c.name, e.event_date, e.round, e.amount_usd, e.url
from events e
join appearances a on a.event_id = e.id and a.confirmed is not false
join companies c on c.id = a.company_id
where e.kind = 'funding' and e.event_date >= current_date - 90
order by e.event_date desc;
```

**Q4: Speakers at events in the next 60 days and the companies they work for**
```sql
select p.name, p.job_title, coalesce(c.name, p.company_name) as company, c.domain,
       e.title as event, e.event_date, p.linkedin_url
from events e
join appearances a on a.event_id = e.id and a.role = 'speaker' and a.confirmed is not false
join people p on p.id = a.person_id
left join companies c on c.id = p.company_id
where e.event_date between current_date and current_date + 60
order by e.event_date, p.name;
```

**Q5: Companies that adopted tool T since date D**
```sql
select c.domain, c.name, t.first_seen_at, t.adopted
from tech_seen t
join companies c on c.id = t.company_id
where t.tool = 'hubspot' and t.first_seen_at >= date '2026-09-01'
  -- and t.adopted   -- stricter: we saw it absent before, so it is a real change
order by t.first_seen_at;
```

**Q6: Every event a given company appeared at, with role**
```sql
select e.event_date, e.kind, e.title, a.role, e.url
from companies c
join appearances a on a.company_id = c.id and a.confirmed is not false
join events e on e.id = a.event_id
where c.domain = 'acme.com'
order by e.event_date desc nulls last;
```
This includes funding and press rows (role `subject`). Add `and e.kind in ('event','webinar')` for gatherings only.

**Q7: Current profile for a company, plus how its signals changed across scrapes**
```sql
select * from companies_current where domain = 'acme.com';

select h.scraped_at, h.prev_open_roles, h.open_roles, h.prev_ats, h.ats,
       h.tech_added, h.tech_removed, h.latest_post_date, h.posts_last_90d
from company_history h
join companies c on c.id = h.company_id
where c.domain = 'acme.com'
order by h.scraped_at;
```
Seed result: `open_roles 2 → 9`, `tech_added {hubspot}`.

**Q8: Batch status per submitted URL**
```sql
select s.url, s.kind, s.status, s.error, s.parent_id, coalesce(c.domain, e.title) as result
from scrapes s
left join companies c on c.id = s.company_id
left join events e on e.id = s.event_id
where s.batch_id = :batch_id
order by s.id;

select status, count(*) from scrapes where batch_id = :batch_id group by status;
```

## 7. Migration and impact on existing code

**Data:** `scraped_pages` holds only test rows (PRD §7), so the schema ends with `drop table if exists scraped_pages`. There is no data migration. Re-submit any real domains as a batch.

**Code** (rough size, against the current tree with AI and auth removed):

| File | Change | Size |
|---|---|---|
| `schema.sql` | Replaced by the DDL above | ~160 lines (was 20) |
| `storage.py` | Rewritten around 6 functions: `queue(batch_id, urls, parent_id=None) -> [{id,url}]`, `save_company(scrape_id, result)`, `save_event(scrape_id, result) -> [company domains to enqueue]`, `mark_error(scrape_id, msg)`, `get_batch(batch_id)`, plus key helpers `registrable_domain`, `person_key`, `event_key`. Each save is 3–5 PostgREST calls. | ~40 → ~140 lines |
| `jobs.py` | `run_job(scrape, store, url, scrape_id, enqueue)`: route on `result["kind"]`, call `save_company` or `save_event`, then `enqueue(children)`. The error path calls `mark_error`. The `id`-in-record trick goes away. | ~30 → ~45 lines |
| `scraper.py` | `scrape()` fetches once, calls `classify()` and returns `{"kind", ...}` with flat signal keys. New `classify()` and `EVENT_HOSTS`. | +~35 lines |
| `signals.py` | Existing 4 extractors unchanged. New `company_events(fetcher, page, final_url)`: JSON-LD Event nodes, event-platform links, newsroom RSS headline rules (`raises\|series [a-z]\|seed\|funding` → funding, `\$([\d.]+)\s*(million\|m\|billion\|b)` → amount), IR page items. | +~90 lines |
| `eventpage.py` (new) | `extract(page)`: event fields from JSON-LD and meta, companies from outbound links with a role taken from the nearest heading (needs `htmlparse` to keep the heading before each anchor, about 10 lines there), and people from JSON-LD Person/performer and speaker cards with LinkedIn links. | ~130 lines |
| `htmlparse.py` | Record the last `h1–h4` text seen before each anchor | +~10 lines |
| `web.py` | `/scrape` creates a scrape row then runs the job inline. `/batch` accepts an optional `kind` column. Batch table JS reads flat columns (`tech`, `open_roles`, …) instead of `signals.*`, and shows child counts. | ~±40 lines |
| `modal_app.py` | Worker gets `scrape_id`; `enqueue` = `queue()` + `scrape_and_save.spawn`. Add `eventpage` to `add_local_python_source`. | ~±10 lines |
| tests | Update fakes for the new storage functions. Add fixture HTML for one event page and one press RSS feed, plus classify cases. | +~150 lines |

**Total: about 450 new lines and about 100 changed.** No new dependencies.

## 8. Extensibility

- **AI extraction later.** Add `extractor text not null default 'rules'` to `appearances`, `events` and `scrapes` (one `add column if not exists` each). The AI step writes the same tables through the same upserts, so dedup keys stop duplicates. Queries filter on `extractor` if the owner wants rules-only. If re-extracting old pages becomes a goal, add `raw_path text` to `scrapes` and gzip HTML into a Supabase Storage bucket keyed by scrape id. Skipped in v1 because it costs storage and code for a use that hasn't happened yet.
- **CRM sync.** The stable keys are `companies.domain` and `people.person_key`, which is what CRMs match on anyway. Add `crm_id text` and `crm_synced_at timestamptz` columns to `companies` and `people`. A sync job reads `companies_current` where `last_scraped_at > crm_synced_at`. The views are the export contract, so tables can change behind them.
- **Owner corrections.**
  - *Wrong link* (a CDN logo counted as a sponsor, or a wrong role): set `appearances.confirmed = false` in the table editor. Every acceptance query filters `confirmed is not false`. Upserts never send `confirmed`, so re-scrapes don't bring the link back.
  - *Wrong role*: insert the right role with `confirmed = true` and set the wrong one to `false`.
  - *Wrong classification*: re-submit with `kind`.
  - *Wrong company name or event date*: v1 overwrites it on the next scrape. When this becomes annoying, add `name_override` or `event_date_override` and use `coalesce(override, value)` in the views: one column and one view edit, with no extractor changes.
  - *Duplicate company* (two domains for one brand): v1 has no merge. Delete one; appearances cascade. If it becomes common, add `companies.merged_into bigint`.
- **New signal**, for example `pricing_page boolean`: one `add column if not exists` on `scrapes`, and a line in each of the three views. History comes for free.

## 9. Known weaknesses

1. **"Adopted since D" is only as good as scrape cadence.** A company first scraped after D that already uses the tool shows `first_seen_at >= D` but `adopted = false`. The PRD's literal Q5 ("first seen after D") is satisfied, but owners may read it as real adoption. The `adopted` flag is the honest answer, and it needs at least two scrapes.
2. **`tech` is a Postgres array.** It is not JSON, but it is still a collection in a column. Q2 cannot use an index through the `companies_current` view; it is a lateral scan over a few thousand companies, which takes milliseconds at the PRD's scale. Past about 100k companies, materialize the view or add a `company_tech` table.
3. **The `scrapes` table is wide and half-empty.** Event scrapes leave the six signal columns null. It is one boring table instead of two, at the price of a little sparseness.
4. **`events` mixes gatherings and announcements.** `event_date` means "starts on" for one and "announced on" for the other, and `amount_usd` and `round` are null for most rows. Queries must always filter `kind`.
5. **No history for profile, people or events.** Job changes are lost: the LinkedIn key is stable but `company_id` is overwritten. Event date changes are lost too.
6. **Weak person dedup without LinkedIn.** The same speaker without a LinkedIn link at two events with no resolved company becomes two rows (`@event:1` and `@event:2`). This is accepted, because the PRD itself says people extraction is patchy.
7. **Funding key by month.** Two rounds in one month merge, which is rare. A press release dated 31 Aug and a blog post dated 1 Sep split into two rows, which is also rare and visible.
8. **Stale appearances never expire.** A sponsor removed from the page keeps its row, and only `last_seen_at` or `last_scrape_id` shows it's gone. The queries don't filter on that by default.
9. **No raw HTML.** When the rules improve, old pages can't be re-extracted, only re-scraped as they look now. Past event pages may already be gone.
10. **Registrable domain without the Public Suffix List.** A hand-kept suffix list will mis-key rare ccTLDs (`acme.com.br` is fine if listed, wrong if not). The upgrade path is the `tldextract` package if it ever bites.
11. **Fan-out cost.** An event page with 200 sponsors spawns 200 Modal jobs. It is capped at 200 and skips companies scraped in the last 30 days, but a 500-URL batch of event pages could still trigger tens of thousands of fetches. A per-batch cap may be needed.
12. **No multi-statement transactions** (PostgREST calls). Partial writes are possible on a crash. They are harmless because every write is an idempotent upsert and the scrape row turns `ok` last. Stuck `queued` rows from a dead worker remain an existing, unfixed issue.
13. **`appearances.first_scrape_id` is overwritten on upsert** unless the payload code omits it on conflict. This is minor: the oldest scrape of the event gives the same answer.
