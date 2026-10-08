# Design variant A: normalized current-state

Designer A · 2026-10-08 · Implements docs/prd-signals-db.md

## 1. Summary and philosophy

1. Each thing gets one current row: `companies` (keyed by domain), `people` (keyed by `person_key`), `events` (keyed by canonical URL). Every value lives in a typed column, and no column holds JSON.
2. Relationships are join tables with a role: `event_companies`, `event_people`, `company_tech`.
3. Every fact row carries `first_seen_at/first_scrape_id` and `last_seen_at/last_scrape_id`. Change detection compares those columns: a fact is new if its first scrape is later than the company's first scrape, and gone if its last scrape is older than the company's last scrape.
4. `scrapes` is the only log. It records one row per fetched URL with batch status, the classification and why it was chosen, and the scalar readings of that scrape (open_roles, ats, posts). History for numbers like "2 → 9" is a `lag()` over this table.
5. Two triggers enforce the upsert rules inside the database, so a plain PostgREST upsert (what `storage.py` already uses) is safe. First-seen values never move. A row the owner has `locked` keeps its descriptive columns.

**Philosophy.** The owner opens the Supabase table editor and sees `companies` with a `domain`, a `name` and `open_roles`. That view is the product, and the schema is the documentation. I chose the most boring form that answers Q1–Q8 with plain joins:

- Facts are sets, so set facts get a row each, with their seen-interval on the row. That covers which tools, which events and which people.
- A few numbers change over time, and their history is the scrape log itself. A separate snapshot table would be a second copy of the same information.
- There is no generic `observations` or EAV table. Each column means one thing, and Postgres types and checks it.
- Seven tables, four views and four small trigger functions. Every one is readable in the table editor.

## 2. Postgres DDL

The DDL is fully idempotent. I ran it twice against `supabase/postgres:17.6` and it raised no errors (only NOTICEs). Seed data plus Q1–Q8 ran against it and returned the expected rows, including a lock that kept an owner-corrected name while `open_roles` still updated 2 → 9.

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

### Notes on the DDL

- **Tables (7):** scrapes, companies, company_tech, events, people, event_companies, event_people.
- **Views (4):** company_tech_current, company_overview, company_changes, batch_rows. All are `security_invoker`. Without that, a Supabase view in `public` would bypass RLS and expose data through the API's anon key.
- **Uniques used for dedup:** `companies.domain`, `events.url`, `people.person_key`, plus the composite PKs `(company_id, tool)`, `(event_id, company_id, role)` and `(event_id, person_id, role)`. They are all plain, non-partial unique constraints, so supabase-py's `upsert(on_conflict="...")` works against them. PostgREST cannot target a partial unique index, which is why `person_key` is a computed text column and not two partial indexes.
- **Evidence and confidence:** `scrapes.kind_reason` stores why a URL was classified the way it was, and `evidence` on the link tables stores the heading or rule that set the role. Rule-based extraction has no real probabilities, and a made-up `confidence real` would mislead. The literal evidence is what the owner needs in order to judge a row.

## 3. Identity, dedup and upserts

| Entity | Identity | Normalization (Python, one helper each) | Upsert |
|---|---|---|---|
| Company | `domain` | `scraper.domain_of(final_url)`: lowercase, strip `www.`. It uses the *final* URL, so redirects collapse (acme.io → acme.com). | A **site scrape** does a full upsert `on_conflict="domain"`. A **discovery** from an event page does an insert with `ignore_duplicates=True` (`ON CONFLICT DO NOTHING`), so anchor text never overwrites a scraped name, then `select id ... where domain in (...)`. |
| Person | `person_key` | `li:<slug>` when there is a LinkedIn `/in/<slug>` URL (lowercased, no trailing slash). Otherwise `nm:<name_key>@<company domain or lowercased company name or event:<id>>`, where `name_key` is the name lowercased and stripped of non-alphanumerics. | Upsert `on_conflict="person_key"`. The latest page wins for job_title and company unless the row is locked. |
| Event | `url` (canonical) | `page.canonical or final_url`, normalized by `htmlparse.normalize`, with query strings stripped for the known event hosts. For a company event signal (press item, IR item, JSON-LD Event on a company site) the key is the item's own link. If the item has no link, the key is `<page url>#<title_key>`. | Before inserting, look up `(title_key, starts_on)`. If a match exists on another host, reuse that event id; this is the "same event, two hosts" rule. Otherwise upsert `on_conflict="url"`. |
| Links | composite PK | — | Upsert with all four seen columns. The trigger restores `first_*` on conflict. |

**How an upsert behaves.** The app always sends `first_seen_at = last_seen_at = <scrape time>` and `first_scrape_id = last_scrape_id = <scrape id>`. On insert, all four are stored. On conflict, PostgREST runs `DO UPDATE`, the `*_keep_first` BEFORE UPDATE trigger puts `first_*` back, and only `last_*` move. That gives FR4: re-scraping the same URL updates the same rows and creates no duplicates.

**Null handling.** `None` values for "sticky" scalar fields (open_roles, ats, careers_url) are dropped from the companies payload. A transient ATS API failure then cannot erase a known value. The scrape row still records the null reading, so history stays honest.

## 4. History and change detection

| Signal | Derived from |
|---|---|
| **"Adopted HubSpot since D"** | `company_tech_current` (tool on the latest site scrape) where `first_seen_at >= D`, **and** there exists an earlier successful site scrape of that company. That second condition separates *adopted* from *we first looked at them*: a tool seen on the very first scrape is a baseline, not an adoption. See Q5. |
| **Dropped a tool** | `company_tech.last_scrape_id <> companies.last_scrape_id`, meaning the tool was not on the latest scrape. It appears in `company_changes` as "tech removed". |
| **"Open roles 2 → 9"** | `lag(open_roles) over (partition by company_id order by scraped_at)` on `scrapes` rows with `kind='company' and status='ok'`. The `company_changes` view emits `open roles | 2 -> 9`. The same pattern covers ats and posts_last_90d. |
| **"Raised funding last 90 days"** | `events.kind='funding' and starts_on >= current_date - 90`, joined to `event_companies.role='subject'`. `starts_on` is the press item's date from RSS pubDate, the `<time>` element or the JSON-LD date, not our scrape time. Without a date the item still gets stored, but Q3 cannot see it (see weaknesses). |
| **"Sponsoring an event next month"** | `event_companies.role in ('sponsor','exhibitor')` joined to `events.starts_on between ...`. |
| **Removed from an event page** | `event_companies.last_scrape_id <> events.last_scrape_id`. |
| **New appearance** | `event_companies.first_seen_at`. This is in `company_changes`. |

Every fact keeps `first_scrape_id` and `last_scrape_id`, and `scrapes.url` gives the source URL and scrape time (FR6).

## 5. Ingestion flow

```
POST /scrape {url}           POST /batch {urls|text}
     |                              |
  insert scrapes(queued)        insert N scrapes(queued, batch_id)   -- storage.queue_batch
     |                              |  spawn scrape_and_save(scrape_id) per row
     +----------- jobs.run_job(scrape_id, url) ------------+
                         |
         fetch (existing Fetcher: SSRF guard, caps, robots, politeness)
                         |
         classify(final_url, page) -> ('event'|'company', reason)
            event if: host in EVENT_HOSTS (lu.ma, eventbrite.*, meetup.com, ...)
                   or JSON-LD @type Event is the page's main node
                   or >= 15 outbound links to distinct non-social/non-CDN domains
                      under a sponsor/exhibitor/partner heading
                         |
        +----------------+----------------+
     company                            event
```

**Company scrape** (scrape id R, time T). Writes go in this order, and all are upserts:

1. `companies` upsert on domain. It writes the profile, socials, hiring, activity and `last_seen_at=T, last_scrape_id=R`, and returns `company_id`.
2. `company_tech` upserts one row per detected tool.
3. Event signals (new extractor `signals.events`) do the following:
   - JSON-LD `Event` nodes and the `/events` page become `events` with role `host`.
   - Press/newsroom RSS items and pages whose headline matches `raises|series [a-z]|seed|funding` become kind `funding`, with the amount parsed from `$X million|billion`. Other press items become kind `press`.
   - IR "results/earnings" items become kind `earnings`.
   - Each one gets an `events` upsert plus an `event_companies` upsert with role `host` or `subject`.
4. Links to event-platform pages (lu.ma/…, eventbrite/…) are enqueued as child event scrapes, at most 5 per company and only when this scrape is depth 0.
5. `scrapes` is updated with `status='ok', kind='company', kind_reason, final_url, title, company_id, open_roles, ats, posts_last_90d, latest_post_date, scraped_at=T`.

**Event scrape**:

1. `events` upsert (or a title_key and date match) with title, dates and location from JSON-LD or meta tags. Returns `event_id`.
2. Companies are taken from outbound anchors. Social, ticketing, CDN and the event host's own domain are skipped. The role comes from the nearest preceding h1–h3, mapped by keyword: sponsor, exhibitor, partner, organizer, else `listed`. Each company is discovery-inserted into `companies` (`ON CONFLICT DO NOTHING`) and then upserted into `event_companies` with `evidence = heading`.
3. People come from JSON-LD `Person`, `performer` and `organizer`, plus "speaker cards", meaning a LinkedIn `/in/` anchor near a name. Each person gets a `people` upsert and an `event_people` upsert.
4. Every linked company with `last_scrape_id is null`, meaning it has never been scraped, gets a child `scrapes` row with the same `batch_id` and `parent_scrape_id=R`, and is spawned. The cap is 200 per event page.
5. `scrapes` is updated with `status='ok', kind='event', event_id`.

**Batch status.** `scrapes.status` moves from `queued` to `ok` or `error`. Child scrapes share the parent's `batch_id`, so the batch progress bar includes discovered companies, and `parent_scrape_id` shows where each came from. Errors are caught per URL in `run_job`, which already does this, so one failure never kills a batch (FR7). Recursion stops structurally: only depth-0 scrapes (no `parent_scrape_id`) enqueue children.

**Atomicity.** The writes are 4–8 PostgREST calls with no transaction around them. If a call fails midway, the scrape is marked `error`, and the facts already written are still true facts. A re-run fixes the rest, because every write is idempotent.

## 6. Acceptance queries

All of these were executed against the DDL above with seed data. `:x` marks a parameter.

**Q1. Sponsors and exhibitors of event X, with tech stack and open roles**
```sql
select c.domain, c.name, ec.role, o.tech, c.open_roles, c.careers_url
from events e
join event_companies ec on ec.event_id = e.id and not ec.rejected
join companies c on c.id = ec.company_id
join company_overview o on o.id = c.id
where e.url = :event_url
  and ec.role in ('sponsor', 'exhibitor')
order by ec.role, c.name;
```

**Q2. Companies using tool T that are hiring**
```sql
select c.domain, c.name, c.ats, c.open_roles, c.careers_url
from company_tech_current t
join companies c on c.id = t.company_id
where t.tool = 'hubspot' and c.open_roles > 0
order by c.open_roles desc;
```

**Q3. Funding events in the last 90 days, with the amount when known**
```sql
select c.domain, c.name, e.starts_on as announced_on, e.funding_round, e.amount_usd, e.url
from events e
join event_companies ec on ec.event_id = e.id and ec.role = 'subject' and not ec.rejected
join companies c on c.id = ec.company_id
where e.kind = 'funding' and e.starts_on >= current_date - 90
order by e.starts_on desc;
```

**Q4. Speakers at events in the next 60 days and their companies**
```sql
select p.name, p.job_title, coalesce(c.name, p.company_name) as company, c.domain,
       p.linkedin_url, e.title as event, e.starts_on
from events e
join event_people ep on ep.event_id = e.id and ep.role = 'speaker' and not ep.rejected
join people p on p.id = ep.person_id
left join companies c on c.id = p.company_id
where e.starts_on between current_date and current_date + 60
order by e.starts_on, p.name;
```

**Q5. Companies that adopted tool T since date D**
```sql
select c.domain, c.name, t.first_seen_at as adopted_seen_at
from company_tech_current t
join companies c on c.id = t.company_id
where t.tool = 'hubspot'
  and t.first_seen_at >= :d
  and exists (select 1 from scrapes s            -- we looked before and it wasn't there
              where s.company_id = c.id and s.kind = 'company' and s.status = 'ok'
                and s.scraped_at < t.first_seen_at)
order by t.first_seen_at;
```

**Q6. Every event a company appeared at, with role**
```sql
select e.kind, e.title, e.starts_on, ec.role, ec.evidence, e.url, ec.first_seen_at
from companies c
join event_companies ec on ec.company_id = c.id and not ec.rejected
join events e on e.id = ec.event_id
where c.domain = :domain
order by e.starts_on desc nulls last;
```

**Q7. Current profile plus how its signals changed across scrapes**
```sql
select o.domain, o.name, o.description, o.tech, o.ats, o.open_roles, o.posts_last_90d,
       o.last_scraped_at, ch.changed_at, ch.change, ch.detail
from company_overview o
left join company_changes ch on ch.company_id = o.id
where o.domain = :domain
order by ch.changed_at desc;
```
Sample output from the test run: `open roles | 2 -> 9`, `tech added | hubspot`, `tech removed | intercom`, `sponsor of conference | SaaS Summit 2026 (2026-11-07)`, `subject of funding | Acme raises $20 million Series B (2026-09-28)`.

**Q8. Batch status per submitted URL**
```sql
select url, parent_scrape_id is not null as discovered, status, error, kind, name, tech,
       open_roles, companies_found, people_found
from batch_rows
where batch_id = :batch_id
order by id;

-- counts for the progress line
select status, count(*) from scrapes where batch_id = :batch_id group by status;
```

## 7. Migration and code impact

**Data.** `scraped_pages` holds a handful of test rows and its `signals` JSON is not worth migrating, since re-scraping regenerates it. The plan:

1. Append the DDL above to `schema.sql` and keep the old `scraped_pages` block for one deploy.
2. Optionally carry over status history with one line: `insert into scrapes (batch_id, url, status, error, created_at, scraped_at) select batch_id, url, status, error, created_at, created_at from scraped_pages;`
3. In the next deploy, replace the old block with `drop table if exists scraped_pages;`.

**Code** (rough sizes against the current ~920 lines):

| File | Change | Size |
|---|---|---|
| `schema.sql` | Add the DDL above, which replaces the scraped_pages block | +270 / −20 |
| `htmlparse.py` | Anchors also record the nearest preceding h1–h3 (`self._last_heading`), giving 3-tuples `(url, text, heading)`. Small update to the existing `for u, _ in` unpackings in signals.py. | +6 |
| `signals.py` | New `events(fetcher, page, final_url)`: JSON-LD Event, event-platform links, newsroom/RSS funding headlines with amount regex, IR earnings. `collect` returns it too. | +110 |
| `eventpage.py` (new) | `classify(final_url, page)`, `companies(page)` (outbound domain filter + heading → role), `people(page)` (JSON-LD Person/performer + LinkedIn cards), `event(page)` | +150 |
| `scraper.py` | `scrape()` returns `{kind, kind_reason, final_url, title, ...}` and calls the company or event extractors | +20 |
| `storage.py` | Becomes about 6 small functions: `queue(batch_id, urls, parent=None)`, `save_company(scrape_id, result)`, `save_event(scrape_id, result) -> new_company_urls`, `finish(scrape_id, fields)`, `fail(scrape_id, error)`, `get_batch(batch_id)` (reads `batch_rows`). Each is a handful of `.upsert(..., on_conflict=...)` calls. | 38 → ~140 |
| `jobs.py` | `run_job(scrape_id, url)`: scrape, then `save_company`/`save_event`, then enqueue children when depth 0, with the try/except kept | 30 → ~55 |
| `web.py` | `/scrape` inserts a scrapes row first, then runs the same job inline. Batch JS reads flat `batch_rows` columns instead of `r.signals.*`. `parse_items` and the limits are unchanged. | ~±40 |
| `modal_app.py` | `scrape_and_save(scrape_id, url)` takes the scrape id, and spawn also gets used from jobs for child scrapes | ±10 |
| tests | New fixtures for an event page and a press page, plus classify/role/person_key unit tests. Storage tests use a fake recording client as today. | +150 |

Net is about +650 lines, mostly extractors the PRD requires under any design. The storage-model part, meaning schema plus storage.py, is about 300 lines.

## 8. Extensibility

- **AI extraction later.** AI writes into the same tables. Add one idempotent column, `alter table event_companies add column if not exists extractor text not null default 'rule'` (same for event_people, people and events), and let the AI path write `extractor='ai'` with its reasoning in `evidence`. AI-extracted people and roles then show up in Q1–Q8 with no query changes, and `where extractor = 'rule'` filters them out. If raw text is needed for re-extraction, add a `page_text text` column on `scrapes`, or a Supabase Storage object keyed by scrape id. It is skipped in v1 because re-scraping is cheap and the pages are public.
- **CRM sync.** Add `crm_id text` and `crm_synced_at timestamptz` to `companies` and `people`. The sync job pushes rows `where last_seen_at > crm_synced_at` and reads `company_changes` for the timeline or notes feed. The view already is the change feed.
- **Owner corrections.** These are table-editor native, with no UI to build:
  - *Wrong profile value:* edit it and tick `locked`. The trigger keeps descriptive columns, while hiring and activity numbers keep updating.
  - *Wrong link* (company isn't a sponsor, person isn't a speaker): tick `rejected`. The row stays, so a re-scrape won't re-add it, because the PK matches and the upsert payload never includes `rejected`. Every query filters `not rejected`.
  - *Wrong role:* reject the bad row. The owner can insert the right role, or it will appear on the next scrape if the heading was fixed.
  - *Duplicate person or company:* merge by hand. Point link rows at the survivor with `update ... set person_id = :keep where person_id = :dup`, then delete the duplicate. The FKs cascade.
- **More tools or roles:** the `check` lists are one-line edits (`alter table ... drop constraint/add constraint` in the migration). `tool` is free text by design, because signals.py owns that vocabulary.

## 9. Known weaknesses

1. **Person identity upgrades create duplicates.** A speaker first seen without LinkedIn (`nm:jane@acme.com`) and later with LinkedIn (`li:jane-doe`) becomes two rows. Mitigation in `save_event`: before inserting an `li:` key, look for an `nm:` row with the same name_key and company and rewrite its key. That is about 10 lines but still heuristic. Name-only people with no company are only deduped per event.
2. **No transaction per scrape.** PostgREST calls are separate, so a mid-flight failure leaves a partial write. That is safe, because the facts are true and idempotent, but the result can briefly look inconsistent. The fix if it ever matters is one `ingest_*` SQL function per kind called via `rpc`, which moves logic into plpgsql.
3. **Scalar history only for what `scrapes` has columns for.** History covers open_roles, ats, posts_last_90d and latest_post_date. A change to `description` or `name` is not tracked, because current state overwrites it. Adding a tracked scalar means adding a column to `scrapes`, which is a deliberate limit. `scrapes` is also a mixed-purpose table: its reading columns are null for event scrapes.
4. **"Adopted" means "first observed after a prior scrape that didn't see it".** Accuracy depends on scrape cadence. A flaky detection (a script loaded only sometimes) shows up as remove/add churn. There is no debounce: one missed scrape means "tech removed".
5. **Event dedup across hosts is a lookup, not a constraint.** The `(title_key, starts_on)` match is done in app code, so two concurrent workers can both insert. A slightly different title or a missing date also misses the match. Recurring meetups titled "Monthly Meetup" on the same date in different cities could wrongly merge.
6. **Event dates drive Q3/Q4.** Funding or press items without a parseable date get `starts_on = null` and are invisible to date-window queries. Using `first_seen_at` as a fallback would mis-date old news on first scrape.
7. **Whole-company discovery from logos is impossible.** This follows PRD §8. Role assignment from "nearest heading" breaks on pages that use images or divs instead of h1–h3, and those links fall back to `listed`.
8. **Trigger-enforced rules are invisible to someone reading Python.** The keep-first and keep-locked behaviour lives in SQL. That is boring and correct, but surprising if you don't read schema.sql.
9. **Some row and table growth from people.** Tens of thousands of people and links is trivial for Postgres. However, `company_changes` is a view of unions with a window function and is computed on read. It is fine at thousands of companies; if it gets slow, materialize it.
