# Variant B: Observation log + views

Designer B · 2026-10-08 · Responds to `docs/prd-signals-db.md`

## 1. Summary and philosophy

- Each scrape adds typed **observation rows**: one narrow table per fact family (`obs_company`, `obs_tech`, `obs_news`, `obs_event`, `obs_event_company`, `obs_event_person`). Every row points at a `scrapes` row, which records the URL and the time.
- **Identity tables** (`companies`, `events`, `people`) hold only the dedup key. They are tiny and never change.
- **Current state is a set of plain SQL views** (`company_current`, `company_tech`, `company_news`, `company_changes`, `event_current`, `event_companies`, `event_people`, `person_current`, `batch_status`). There is no JSON anywhere, and every view column is a normal typed column.
- Observation rows are never updated or deleted. History, provenance and change detection come free from that, so there is no upsert-merge logic in Python.
- `scrapes.status = 'ok'` is the commit marker. Supabase REST has no transactions, so a half-written scrape is ignored by every view. Re-extracting a scrape marks the old one `superseded`.

**Philosophy.** Code that merges new facts into current rows ("upsert current state, also keep history") holds most of the bugs in a signals pipeline: what counts as a change, what to do with null, whether to overwrite. This design gives that job to Postgres. Python writes down what it saw. SQL decides what that means, and SQL is cheap to change. When the owner later changes their mind about what "adopted" or "current" means, the fix is a view edit. Old data never needs a migration and nothing has to be re-scraped. The cost is a handful of window-function views. They are written once, kept short and tested below.

Validated: the DDL, the migration and all Q1–Q8 queries ran against `supabase/postgres:17.6.1` (the local Docker image), with each file applied twice. The results are in section 6. The anon role reads 0 rows through the views (`security_invoker`).

## 2. Postgres DDL

This replaces `schema.sql`. It is idempotent: `create table if not exists`, `create index if not exists` and `create or replace view`.

Scale check: 1,000 companies × 3 scrapes a month × ~10 tools ≈ 360k `obs_tech` rows a year. Plain views over indexed tables handle that easily. Switch to materialized views only if a query becomes measurably slow.

```sql
-- ============================================================
-- Envelope: one row per URL fetch. Batch queue + provenance for every observation.
-- An observation row counts only when its scrape has status = 'ok' (commit marker).
-- ============================================================
create table if not exists scrapes (
  id          bigint generated always as identity primary key,
  batch_id    uuid,
  parent_id   bigint references scrapes(id),        -- set when an event scrape enqueued this one
  url         text not null,                        -- as submitted / discovered
  final_url   text,                                 -- after redirects
  domain      text,
  kind        text check (kind in ('company', 'event', 'manual')),  -- null until classified
  kind_reason text,                                 -- 'host:lu.ma' | 'jsonld:Event' | 'outbound_links:41' | 'default'
  status      text not null default 'queued'
              check (status in ('queued', 'ok', 'error', 'superseded')),
  error       text,
  title       text,
  raw_path    text,                                 -- gzipped HTML in Storage bucket 'raw'; null if not kept
  created_at  timestamptz not null default now(),
  fetched_at  timestamptz                           -- the time every observation of this scrape is "as of"
);
create index if not exists scrapes_batch_idx  on scrapes (batch_id) where batch_id is not null;
create index if not exists scrapes_parent_idx on scrapes (parent_id) where parent_id is not null;
create index if not exists scrapes_domain_idx on scrapes (domain, created_at desc);

-- ============================================================
-- Identity: thin, stable, never deleted. Attributes live in observations.
-- ============================================================
create table if not exists companies (
  id         bigint generated always as identity primary key,
  domain     text not null unique,                  -- registrable domain, lowercase, no www
  created_at timestamptz not null default now()
);

create table if not exists events (
  id         bigint generated always as identity primary key,
  url        text not null unique,                  -- canonical URL (normalized)
  title_key  text,                                  -- dedup hint: lowercased alnum title at creation
  date_key   date,                                  -- dedup hint: start date at creation
  created_at timestamptz not null default now()
);
create index if not exists events_dedup_idx on events (title_key, date_key);

create table if not exists people (
  id           bigint generated always as identity primary key,
  person_key   text not null unique,                -- 'li:/in/jane-doe' or 'nm:jane doe|acme.com'
  linkedin_url text,
  created_at   timestamptz not null default now()
);

-- ============================================================
-- Observations: append-only, typed, one table per fact family.
-- ============================================================
create table if not exists obs_company (            -- one snapshot row per company scrape
  scrape_id        bigint primary key references scrapes(id) on delete cascade,
  company_id       bigint not null references companies(id),
  name             text,
  description      text,
  logo_url         text,
  canonical_url    text,
  linkedin_url     text,
  x_url            text,
  github_url       text,
  facebook_url     text,
  instagram_url    text,
  youtube_url      text,
  careers_url      text,
  ats              text,
  open_roles       int,                             -- null = unknown (not "zero")
  latest_post_date date,
  posts_last_90d   int,
  activity_source  text
);
create index if not exists obs_company_company_idx on obs_company (company_id);

create table if not exists obs_tech (               -- one row per tool detected per scrape
  scrape_id  bigint not null references scrapes(id) on delete cascade,
  company_id bigint not null references companies(id),
  tool       text not null,
  primary key (scrape_id, tool)
);
create index if not exists obs_tech_company_tool_idx on obs_tech (company_id, tool);
create index if not exists obs_tech_tool_idx on obs_tech (tool);

create table if not exists obs_news (               -- funding / earnings / press items seen on a company's site
  scrape_id    bigint not null references scrapes(id) on delete cascade,
  company_id   bigint not null references companies(id),
  url          text not null,                       -- the item's own URL (dedup key with company_id)
  kind         text not null check (kind in ('funding', 'earnings', 'press')),
  headline     text,
  published_on date,
  amount_usd   bigint,                              -- from "$X million" when matched
  round        text,                                -- 'seed' | 'series_a' | ... when matched
  primary key (scrape_id, url)
);
create index if not exists obs_news_company_idx on obs_news (company_id, kind);

create table if not exists obs_event (               -- what a scrape said about an event (title, date, place)
  scrape_id  bigint not null references scrapes(id) on delete cascade,
  event_id   bigint not null references events(id),
  title      text,
  starts_on  date,
  ends_on    date,
  location   text,
  is_online  boolean,
  primary key (scrape_id, event_id)
);
create index if not exists obs_event_event_idx on obs_event (event_id);

create table if not exists obs_event_company (       -- company X appeared at event Y with role R
  scrape_id  bigint not null references scrapes(id) on delete cascade,
  event_id   bigint not null references events(id),
  company_id bigint not null references companies(id),
  role       text not null check (role in ('sponsor', 'exhibitor', 'partner', 'organizer', 'host', 'listed')),
  evidence   text,                                  -- section heading text, 'jsonld:organizer', 'link-on-company-site'
  primary key (scrape_id, event_id, company_id, role)
);
create index if not exists obs_event_company_company_idx on obs_event_company (company_id);
create index if not exists obs_event_company_event_idx on obs_event_company (event_id);

create table if not exists obs_event_person (        -- person X appeared at event Y with role R
  scrape_id    bigint not null references scrapes(id) on delete cascade,
  event_id     bigint not null references events(id),
  person_id    bigint not null references people(id),
  role         text not null check (role in ('speaker', 'organizer', 'host', 'listed')),
  name         text not null,
  job_title    text,
  company_name text,                                -- as printed on the card
  company_id   bigint references companies(id),     -- resolved when the card links a company domain
  evidence     text,
  primary key (scrape_id, event_id, person_id, role)
);
create index if not exists obs_event_person_person_idx on obs_event_person (person_id);
create index if not exists obs_event_person_event_idx on obs_event_person (event_id);

-- Owner corrections: a rejected fact is hidden from every view, past and future scrapes.
-- Exactly one of tool / news_url / event_id says which fact family is rejected.
create table if not exists rejections (
  id         bigint generated always as identity primary key,
  company_id bigint references companies(id),
  person_id  bigint references people(id),
  event_id   bigint references events(id),
  tool       text,
  news_url   text,
  role       text,                                  -- event appearances only; null = any role
  note       text,
  created_at timestamptz not null default now(),
  check (num_nonnulls(tool, news_url, event_id) = 1)
);

-- No policies: only the service_role key (server-side) can read/write.
alter table scrapes           enable row level security;
alter table companies         enable row level security;
alter table events            enable row level security;
alter table people            enable row level security;
alter table obs_company       enable row level security;
alter table obs_tech          enable row level security;
alter table obs_news          enable row level security;
alter table obs_event         enable row level security;
alter table obs_event_company enable row level security;
alter table obs_event_person  enable row level security;
alter table rejections        enable row level security;

-- ============================================================
-- Current state = views. security_invoker so the anon key can't read through them.
-- A column change in a view needs `drop view ... cascade` first (create or replace can only append).
-- ============================================================

-- Every successful company snapshot, newest first per company (recency = 1 is current).
create or replace view company_snapshots with (security_invoker = true) as
select o.*,
       s.fetched_at,
       coalesce(s.final_url, s.url) as source_url,
       row_number() over (partition by o.company_id order by s.fetched_at desc, s.id desc) as recency,
       min(s.fetched_at) over (partition by o.company_id) as first_scraped_at
from obs_company o
join scrapes s on s.id = o.scrape_id
where s.status = 'ok';

-- One row per (company, tool) ever seen: when first/last seen, is it on the latest scrape, was it adopted.
create or replace view company_tech with (security_invoker = true) as
select t.company_id,
       t.tool,
       min(cs.fetched_at)                       as first_seen,
       max(cs.fetched_at)                       as last_seen,
       bool_or(cs.recency = 1)                  as is_current,
       min(cs.fetched_at) > min(cs.first_scraped_at) as adopted   -- absent on our first scrape, present later
from obs_tech t
join company_snapshots cs on cs.scrape_id = t.scrape_id
where not exists (select 1 from rejections r where r.company_id = t.company_id and r.tool = t.tool)
group by t.company_id, t.tool;

-- One current row per company.
create or replace view company_current with (security_invoker = true) as
select c.id as company_id, c.domain,
       cs.name, cs.description, cs.logo_url, cs.canonical_url,
       cs.linkedin_url, cs.x_url, cs.github_url, cs.facebook_url, cs.instagram_url, cs.youtube_url,
       cs.careers_url, cs.ats, cs.open_roles,
       cs.latest_post_date, cs.posts_last_90d, cs.activity_source,
       array(select t.tool from company_tech t
             where t.company_id = c.id and t.is_current order by t.tool) as tools,
       cs.first_scraped_at,
       cs.fetched_at as last_scraped_at,
       cs.source_url
from companies c
left join company_snapshots cs on cs.company_id = c.id and cs.recency = 1;

-- One row per (company, news URL): latest wording, first time we saw it.
create or replace view company_news with (security_invoker = true) as
select company_id, url, kind, headline, published_on, amount_usd, round, first_seen, last_seen
from (
  select n.*,
         min(s.fetched_at) over w as first_seen,
         max(s.fetched_at) over w as last_seen,
         row_number() over (partition by n.company_id, n.url order by s.fetched_at desc, s.id desc) as rn
  from obs_news n
  join scrapes s on s.id = n.scrape_id
  where s.status = 'ok'
  window w as (partition by n.company_id, n.url)
) x
where rn = 1
  and not exists (select 1 from rejections r where r.company_id = x.company_id and r.news_url = x.url);

-- Change feed: one row per detected change between consecutive snapshots of a company.
create or replace view company_changes with (security_invoker = true) as
with h as (
  select company_id, scrape_id, fetched_at, source_url, open_roles, ats,
         lag(scrape_id)  over w as prev_scrape_id,
         lag(open_roles) over w as prev_open_roles,
         lag(ats)        over w as prev_ats
  from company_snapshots
  window w as (partition by company_id order by fetched_at, scrape_id)
)
select company_id, fetched_at as changed_at, 'open_roles' as signal,
       prev_open_roles::text as old_value, open_roles::text as new_value, source_url
from h
where open_roles is not null and prev_open_roles is not null and open_roles <> prev_open_roles
union all
select company_id, fetched_at, 'ats', prev_ats, ats, source_url
from h
where prev_scrape_id is not null and ats is distinct from prev_ats
union all
select h.company_id, h.fetched_at, 'tool_added', null, t.tool, h.source_url
from h
join obs_tech t on t.scrape_id = h.scrape_id
where h.prev_scrape_id is not null
  and not exists (select 1 from obs_tech p where p.scrape_id = h.prev_scrape_id and p.tool = t.tool)
union all
select h.company_id, h.fetched_at, 'tool_removed', p.tool, null, h.source_url
from h
join obs_tech p on p.scrape_id = h.prev_scrape_id
where not exists (select 1 from obs_tech t where t.scrape_id = h.scrape_id and t.tool = p.tool)
union all
select company_id, first_seen, 'news_' || kind, null, headline, url
from company_news;

-- One current row per event.
create or replace view event_current with (security_invoker = true) as
select e.id as event_id, e.url, x.title, x.starts_on, x.ends_on, x.location, x.is_online,
       x.fetched_at as last_seen, x.source_url
from events e
left join (
  select o.*, s.fetched_at, coalesce(s.final_url, s.url) as source_url,
         row_number() over (partition by o.event_id order by s.fetched_at desc, s.id desc) as rn
  from obs_event o
  join scrapes s on s.id = o.scrape_id
  where s.status = 'ok'
) x on x.event_id = e.id and x.rn = 1;

-- One row per (event, company, role) ever observed.
create or replace view event_companies with (security_invoker = true) as
select o.event_id, o.company_id, o.role,
       min(o.evidence)   as evidence,
       min(s.fetched_at) as first_seen,
       max(s.fetched_at) as last_seen
from obs_event_company o
join scrapes s on s.id = o.scrape_id
where s.status = 'ok'
  and not exists (select 1 from rejections r
                  where r.event_id = o.event_id and r.company_id = o.company_id
                    and (r.role is null or r.role = o.role))
group by o.event_id, o.company_id, o.role;

-- One row per (event, person, role) ever observed.
create or replace view event_people with (security_invoker = true) as
select o.event_id, o.person_id, o.role,
       min(s.fetched_at) as first_seen,
       max(s.fetched_at) as last_seen
from obs_event_person o
join scrapes s on s.id = o.scrape_id
where s.status = 'ok'
  and not exists (select 1 from rejections r
                  where r.event_id = o.event_id and r.person_id = o.person_id
                    and (r.role is null or r.role = o.role))
group by o.event_id, o.person_id, o.role;

-- One current row per person: latest name / title / employer seen on any event page.
create or replace view person_current with (security_invoker = true) as
select p.id as person_id, p.linkedin_url, x.name, x.job_title, x.company_name, x.company_id,
       x.fetched_at as last_seen
from people p
left join (
  select o.*, s.fetched_at,
         row_number() over (partition by o.person_id order by s.fetched_at desc, s.id desc) as rn
  from obs_event_person o
  join scrapes s on s.id = o.scrape_id
  where s.status = 'ok'
) x on x.person_id = p.id and x.rn = 1;

-- Batch status: one row per submitted URL (children = companies an event page enqueued).
create or replace view batch_status with (security_invoker = true) as
select s.batch_id, s.id as scrape_id, s.url, s.domain, s.kind, s.status, s.error, s.title,
       s.created_at, s.fetched_at,
       o.name as company_name, o.open_roles, o.latest_post_date,
       array(select t.tool from obs_tech t where t.scrape_id = s.id order by t.tool) as tools,
       (select count(*) from obs_event_company e where e.scrape_id = s.id) as companies_found,
       (select count(*) from obs_event_person e where e.scrape_id = s.id)  as people_found,
       (select count(*) from scrapes c where c.parent_id = s.id)           as children,
       (select count(*) from scrapes c where c.parent_id = s.id and c.status = 'queued') as children_queued
from scrapes s
left join obs_company o on o.scrape_id = s.id
where s.batch_id is not null and s.parent_id is null;
```

**Tables (11):** `scrapes`, `companies`, `events`, `people`, `obs_company`, `obs_tech`, `obs_news`, `obs_event`, `obs_event_company`, `obs_event_person`, `rejections`.
**Views (10):** `company_snapshots`, `company_tech`, `company_current`, `company_news`, `company_changes`, `event_current`, `event_companies`, `event_people`, `person_current`, `batch_status`.

Why `obs_company` is one wide row rather than a separate table each for profile, hiring and activity: these are single-valued facts that the scraper always produces together. One typed row per scrape is the most readable choice, with no join needed to see "Acme at time T". Multi-valued facts (tools, news, appearances) get their own narrow tables. Typed columns stay typed, and no generic `(attr, value)` EAV table is used.

## 3. Identity and dedup

Identity rows hold only the key, so an "upsert" of an identity row is `insert … on conflict (key) do nothing`, followed by reading the id back. The client does this with `table.upsert(rows, on_conflict="domain", ignore_duplicates=True)` and then a `select id, domain … in (…)`. Observations are always plain inserts.

| Entity | Key (unique column) | Normalization (Python, one helper each) |
|---|---|---|
| Company | `companies.domain` | Host, lowercased, `www.` stripped, reduced to the registrable domain: the last 2 labels, or the last 3 if the last two are a short hard-coded multi-part suffix list (`co.uk`, `com.au`, `co.jp`, …). `blog.acme.com` maps to `acme.com`. |
| Event | `events.url` (canonical) | `<link rel=canonical>` or the final URL, lowercased host, no `www.`, no fragment, `utm_*` and `ref` query params dropped, trailing `/` stripped. If the URL is unknown, a second lookup uses `(title_key, date_key)`: alphanumeric-lowercased title plus start date. A match reuses that event id, which catches the same meetup on lu.ma and on meetup.com. Null date means no cross-host dedup. |
| Person | `people.person_key` | `li:/in/<slug>` (lowercased LinkedIn path) when a LinkedIn link exists. Otherwise `nm:<lowercased, single-spaced name>|<company domain, else lowercased company name, else empty>`. |
| Observation | Composite PK per table, e.g. `(scrape_id, tool)` or `(scrape_id, event_id, company_id, role)` | Stops a scrape from writing the same fact twice. Repeating a fact *across* scrapes is the point: it is history. |
| News item | `(company_id, url)` in the `company_news` view | The same press URL seen in 5 scrapes becomes one row with `first_seen` and `last_seen`. |

"Re-scraping the same URL twice yields no duplicate companies/people/events" holds because the identity tables are unique on their keys. Observations do repeat, by design, and every view collapses them.

## 4. History and change detection

Every view derives from `obs_* join scrapes where status = 'ok'`, and `scrapes.fetched_at` is the "as of" time.

**"Adopted HubSpot since D"** uses the `company_tech` view:
- `first_seen` = earliest snapshot containing the tool.
- `adopted` = `first_seen > first_scraped_at`. The tool was **absent on our first scrape of that company** and appeared later. Without this, every company would "adopt" its whole stack on the day we first scraped it. `adopted = false` means "already had it when we started watching".
- `is_current` = the tool is present in the company's latest successful snapshot. If it is false, the tool was dropped.
- The query is `where tool = 'hubspot' and adopted and first_seen >= D`.

**"Open roles 2 → 9"** uses the `company_changes` view: `lag(open_roles)` over the company's snapshots ordered by `fetched_at`. A row is emitted only when **both** values are non-null and differ. `open_roles` is null when the ATS API failed, and null means "unknown", not zero, so a failed fetch is not reported as "9 → unknown → 9". The same feed also emits `ats` changes, `tool_added` and `tool_removed` (a set difference between consecutive snapshots in `obs_tech`), and `news_<kind>` on the first sighting of each news item. A filter on `signal`, `old_value` and `new_value` turns this into a timing-signal list, e.g. `where signal = 'open_roles' and new_value::int >= 2 * old_value::int`.

**"Raised funding in the last 90 days"** uses the `company_news` view: `kind = 'funding'` and `coalesce(published_on, first_seen::date) >= current_date - 90`. `published_on` comes from the press page or RSS date. When a page has no date, `first_seen` is the fallback, and it is also the honest "we learned about it on" date.

**"Sponsoring an event next month"** is `event_companies` (role) joined to `event_current.starts_on`.

## 5. Ingestion flow

```
POST /scrape {url} | POST /batch {urls|text}
  │  web.py: normalize, dedupe, cap 500 (unchanged)
  ▼
storage.queue(batch_id, urls)        → INSERT scrapes (status='queued', url, domain, batch_id)   [one row per URL]
  │  spawn one Modal job per row (unchanged)
  ▼
jobs.run_job(scrape_id, url)
  1. fetch     fetcher.fetch(url) (SSRF guard, caps, robots, politeness unchanged)
  2. classify  event if host in {lu.ma, eventbrite.*, meetup.com, …}
               or JSON-LD @type Event is the page's main node
               or ≥15 outbound links to distinct non-social/non-ticketing domains;
               else company. kind_reason records which rule fired.
  3. extract   company → profile/tech/hiring/activity (existing) + news/IR + events-on-site
               event   → event details, companies(role from nearest heading), people
  4. write     (each step is one REST call with bulk rows)
     a. upload gzipped HTML → Storage 'raw/<scrape_id>.html.gz'
     b. upsert identity rows: companies / events / people (on conflict do nothing) → ids
     c. insert obs rows:
          company page: obs_company (1), obs_tech (n), obs_news (n),
                        obs_event + obs_event_company(role='host'|'listed') for events found on the site
          event page:   obs_event (1), obs_event_company (n), obs_event_person (n)
     d. UPDATE scrapes SET status='ok', kind, kind_reason, final_url, title, raw_path, fetched_at
        ← the commit marker; the scrape becomes visible in every view only now
  5. fan-out   event page only: for each company on the page with no ok snapshot in the last 30 days,
               INSERT scrapes (status='queued', parent_id=this scrape, batch_id=same) and spawn.
  on any exception: UPDATE scrapes SET status='error', error=<msg>; partial obs rows stay invisible.
```

- **A single `/scrape`** inserts its own `scrapes` row with `batch_id = null` and runs the same code inline.
- **Batch status (Q8)** reads the `batch_status` view: one row per submitted URL, with flat columns for the UI (company name, open roles, tools as a `text[]`, companies/people found, child counts). The per-status counts come from `scrapes where batch_id = X`, including the fanned-out children.
- **Retry** means a new scrape row. A scrape id is written once, so there are no observation PK conflicts and no deletes.
- **Manual corrections** are `kind = 'manual'` scrapes. See section 8.

## 6. Acceptance queries Q1–Q8

These ran against the DDL above. The sample results come from seed data with 3 Acme scrapes, 1 event page, 1 error and 1 migrated legacy row.

**Q1: Sponsors and exhibitors of event X, with tech stack and open roles**
```sql
select co.domain, co.name, ec.role, co.tools, co.ats, co.open_roles
from event_current e
join event_companies ec on ec.event_id = e.event_id
join company_current co on co.company_id = ec.company_id
where e.url = 'https://lu.ma/devconf'          -- or e.title ilike '%devconf%'
  and ec.role in ('sponsor', 'exhibitor')
order by ec.role, co.open_roles desc nulls last;
```
Result: `acme.com | Acme Inc | sponsor | {hubspot} | greenhouse | 9`. The rejected Initech exhibitor row is correctly hidden.

**Q2: Companies using HubSpot that are hiring**
```sql
select co.domain, co.name, co.open_roles, co.careers_url
from company_tech t
join company_current co using (company_id)
where t.tool = 'hubspot' and t.is_current
  and co.open_roles > 0
order by co.open_roles desc;
```
A shorter form is `select * from company_current where 'hubspot' = any(tools) and open_roles > 0`.

**Q3: Companies with a funding event in the last 90 days, with amount**
```sql
select co.domain, co.name, n.published_on, n.round, n.amount_usd, n.headline, n.url
from company_news n
join company_current co using (company_id)
where n.kind = 'funding'
  and coalesce(n.published_on, n.first_seen::date) >= current_date - 90
order by n.published_on desc nulls last;
```
The same press URL was seen in 2 scrapes and came back as 1 row, with the latest headline.

**Q4: Speakers at events in the next 60 days, and their companies**
```sql
select e.title, e.starts_on, p.name, p.job_title,
       coalesce(co.name, p.company_name) as company, co.domain, p.linkedin_url
from event_current e
join event_people ep on ep.event_id = e.event_id and ep.role = 'speaker'
join person_current p on p.person_id = ep.person_id
left join company_current co on co.company_id = p.company_id
where e.starts_on between current_date and current_date + 60
order by e.starts_on, e.title, p.name;
```

**Q5: Companies that adopted tool T since date D**
```sql
select co.domain, co.name, t.first_seen
from company_tech t
join company_current co using (company_id)
where t.tool = 'hubspot' and t.adopted
  and t.first_seen >= date '2026-08-01'
order by t.first_seen;
```
Result: Acme (HubSpot was absent on its first scrape). Globex and the legacy company had HubSpot on their first scrape, so they are excluded as "already had it".

**Q6: Every event a company appeared at, with role**
```sql
select e.title, e.starts_on, e.url, ec.role, ec.evidence, ec.first_seen
from companies c
join event_companies ec on ec.company_id = c.id
join event_current e on e.event_id = ec.event_id
where c.domain = 'acme.com'
order by e.starts_on desc nulls last;
```

**Q7: Current profile plus how its signals changed**
```sql
select * from company_current where domain = 'acme.com';

select ch.changed_at, ch.signal, ch.old_value, ch.new_value, ch.source_url
from company_changes ch
join companies c on c.id = ch.company_id
where c.domain = 'acme.com'
order by ch.changed_at, ch.signal;
```
Result:
```
2026-09-08 | news_funding | (null)    | Acme raises $40 million Series B | https://acme.com/press/series-b
2026-09-08 | tool_added   | (null)    | hubspot                          | https://acme.com
2026-10-08 | open_roles   | 2         | 9                                | https://acme.com
2026-10-08 | tool_removed | wordpress | (null)                           | https://acme.com
```
For the full raw history, use `select * from company_snapshots where company_id = … order by fetched_at`.

**Q8: Batch status**
```sql
select url, kind, status, error, company_name, companies_found, children, children_queued
from batch_status
where batch_id = '11111111-1111-1111-1111-111111111111'
order by scrape_id;

select status, count(*) from scrapes
where batch_id = '11111111-1111-1111-1111-111111111111'
group by status;                         -- includes fanned-out children
```

All 8 use only typed columns. The one `text[]` (`tools`) is a convenience column, and Q2 also works without it.

## 7. Migration and code impact

**Data.** `scraped_pages` holds a handful of test rows. A one-off file, `migrations/001_scraped_pages.sql`, copies them over, keeping the ids. It is safe to re-run, and it was tested twice in a row. It is the only place JSON paths appear, and they run once.

```sql
-- One-off: copy scraped_pages into the observation log, keeping ids. Safe to re-run.
insert into scrapes (id, url, final_url, domain, kind, status, error, title, batch_id, created_at, fetched_at)
overriding system value
select id, url, url, domain, case when status = 'ok' then 'company' end, status, error, title, batch_id,
       created_at, created_at
from scraped_pages
on conflict (id) do nothing;
select setval(pg_get_serial_sequence('scrapes', 'id'), (select coalesce(max(id), 0) + 1 from scrapes), false);

insert into companies (domain)
select distinct domain from scraped_pages where status = 'ok' and domain is not null
on conflict (domain) do nothing;

insert into obs_company (scrape_id, company_id, name, description, logo_url, canonical_url,
  linkedin_url, x_url, github_url, facebook_url, instagram_url, youtube_url,
  careers_url, ats, open_roles, latest_post_date, posts_last_90d, activity_source)
select p.id, c.id,
  p.signals #>> '{profile,name}', p.signals #>> '{profile,description}',
  p.signals #>> '{profile,logo}', p.signals #>> '{profile,canonical_url}',
  p.signals #>> '{profile,socials,linkedin}', p.signals #>> '{profile,socials,x}',
  p.signals #>> '{profile,socials,github}', p.signals #>> '{profile,socials,facebook}',
  p.signals #>> '{profile,socials,instagram}', p.signals #>> '{profile,socials,youtube}',
  p.signals #>> '{hiring,careers_url}', p.signals #>> '{hiring,ats}',
  (p.signals #>> '{hiring,open_roles}')::int,
  (p.signals #>> '{activity,latest_post_date}')::date,
  (p.signals #>> '{activity,posts_last_90d}')::int,
  p.signals #>> '{activity,source}'
from scraped_pages p join companies c on c.domain = p.domain
where p.status = 'ok'
on conflict (scrape_id) do nothing;

insert into obs_tech (scrape_id, company_id, tool)
select p.id, c.id, t.tool
from scraped_pages p
join companies c on c.domain = p.domain
cross join lateral jsonb_array_elements_text(coalesce(p.signals -> 'tech', '[]')) as t(tool)
where p.status = 'ok'
on conflict do nothing;

-- When happy: drop table scraped_pages;
```
The other choice is to skip this and `drop table scraped_pages`. The PRD allows that.

**Code** (rough line counts, excluding the new event/news extractors, which every design needs equally):

| File | Change | Size |
|---|---|---|
| `schema.sql` | Replaced by the DDL above. The migration goes in its own file. | ~280 lines SQL (mostly views and comments) |
| `storage.py` | `queue_batch` targets `scrapes`. `save_to_supabase(record)` becomes `save_scrape(scrape_id, result)`: identity upserts → obs inserts → mark ok. `get_batch` reads `batch_status`. A `fail(scrape_id, msg)` helper is added. Raw HTML upload is 3 lines. | 40 → ~120 |
| `jobs.py` | Works on a `scrape_id`, not a record dict. Calls `fail()` on error. Adds the fan-out step for event pages (insert child scrapes and spawn). | 30 → ~55 |
| `scraper.py` | Returns `{kind, kind_reason, final_url, title, html, company: {...flat obs_company fields}, tools, news, events, event, companies, people}`. Classification lives here. | 47 → ~80 |
| `signals.py` | `profile` flattens socials into `linkedin_url`, `x_url`, …; `collect` returns flat keys matching `obs_company` columns. Existing extractors are otherwise unchanged. | ~20 lines touched |
| new `events.py` | Classification heuristics, event-page companies/people/role-by-heading, news/IR rules. Same in any design. | ~250 |
| `web.py` | `/scrape` creates a scrape row and returns the extraction. The UI renders flat fields (`company.name`, `tools`, `company.open_roles`) instead of `signals.*`. The batch table reads `batch_status` columns. | ~40 lines touched |
| `modal_app.py` | `scrape_and_save(scrape_id, url)` signature. The worker needs `spawn` for fan-out. | ~10 |
| tests | Fake `save` becomes a fake storage with lists per table. Add one SQL fixture test, or keep the Docker check above as a CI step if wanted. | moderate |

## 8. Extensibility

**AI extraction later.** An AI pass over a stored page creates a **new `scrapes` row** with the same `url`, `raw_path` and `fetched_at` as the original, plus one new column added then: `extractor text not null default 'rules'`. That is instant, since adding a column with a default is free in PG11+. Its rows go into the same `obs_*` tables, so the views pick them up unchanged. `company_tech` takes the union of tools both extractors found. For single-valued `obs_company`, the views' tie-break (`fetched_at desc, id desc`) makes the newest extraction win. If rules should win instead, that is a one-line `order by` change in `company_snapshots`. If AI needs new fact families (e.g. "pain points"), they become a new `obs_<family>` table plus a view, with no change to existing tables.

**Re-extraction with improved rules.** Load `raw_path`, run the extractors, write a new scrape row with the original `fetched_at`, and set the old one to `status = 'superseded'`. History stays correct as of the original time. No obs row is touched, and the old one stays for audit.

**CRM sync later.** The views are the export surface. Add a `crm_links(company_id, crm, external_id, synced_at)` table at that point. A "changes since last sync" feed is just `company_changes where changed_at > synced_at`, which the change feed already provides.

**Owner corrections.**
- *Remove a wrong fact* (a tool, a news item, or a company or person on an event, optionally by role): insert a row into `rejections` using the Supabase table editor. All views anti-join it, so the fact is hidden for past **and future** scrapes. Deleting the rejection row brings the fact back.
- *Add or fix a fact* (wrong role, missing sponsor, better company name): insert a `scrapes` row with `kind = 'manual'`, `status = 'ok'`, `url = 'manual:<note>'` and `fetched_at = now()`, plus the right `obs_*` rows. The owner becomes one more source with full provenance. Example: to fix a role, reject the wrong role and add a manual `obs_event_company` row with the right one.
- *Classification and role evidence* go in `scrapes.kind_reason` and `obs_event_company.evidence` (e.g. the heading text "Gold Sponsors"), so the owner can see *why* a row exists before correcting it. There is no numeric confidence score, because rule-based extraction has no honest number to put there.

## 9. Known weaknesses

1. **Manual profile fixes don't stick.** A manual `obs_company` row is the newest snapshot only until the next automatic scrape. Sticky overrides need either a source-priority tie-break in `company_snapshots` or an `overrides` table. I deferred both until it actually bites.
2. **The views compute on every read.** That is fine at the PRD's scale (thousands of companies). At hundreds of thousands of snapshots, `company_current` and `company_changes` would need materialized views refreshed after each batch, which is extra moving parts.
3. **Ad-hoc SQL needs the views.** Querying raw `obs_*` tables directly means remembering `join scrapes … where status = 'ok'`, or the owner sees uncommitted or superseded rows. The table editor shows both raw tables and views. That is 21 objects, more than a "current tables" design has.
4. **There are no transactions over Supabase REST.** The commit marker hides partial writes, but orphan obs rows from failed scrapes stay in the tables. They are harmless but untidy. A worker that crashes mid-write leaves a scrape `queued` forever, and nothing sweeps that yet.
5. **Raw HTML storage grows.** About 30 KB gzipped per page × ~3k scrapes a month ≈ 90 MB a month in Storage, which fills the 1 GB free tier in about 10 months. A retention job (delete raw older than N days and null `raw_path`) is needed eventually. Without raw HTML, re-extraction is impossible. Follow-up fetches (ATS API, sitemap) are not stored, so `open_roles` can't be re-derived.
6. **Identity is heuristic.** The registrable-domain suffix list is hand-kept (there is no public-suffix library, to avoid a dependency). An event listed on two hosts merges only if title and date both match. A person seen first without LinkedIn and later with it becomes **two** people rows, and there is no merge support yet (it would need `people.merged_into`).
7. **Appearances don't expire.** If a sponsor is removed from an event page, `event_companies` still lists it, with an older `last_seen`. That is correct as history but may surprise someone expecting "current sponsors". They have to compare `last_seen` to `event_current.last_seen`.
8. **"Adopted" depends on detection.** A tool that a scrape misses intermittently (CDN variation, consent-gated script) shows up as removed and then added in `company_changes`, and can make `adopted` true falsely if the first scrape missed it. A mitigation is to require presence in 2 consecutive snapshots, but I didn't add it.
9. **News dedup is by URL only.** The same funding round on `/press/x` and `/blog/x` shows up twice in Q3.
10. **Write amplification.** An unchanged company still writes a full `obs_company` row and every tool row on each scrape. That is cheap at this scale, but it is the price of never computing diffs in Python.
