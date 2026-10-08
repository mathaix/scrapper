# Final design: Marketing Signals DB

Head judge · 2026-10-08 · Builds on `docs/prd-signals-db.md`. Base: Variant C (Minimal pragmatic), with the amendments from the debate.

## 0. Decision record

**Winner: Variant C, amended.** It has 5 tables and 3 views, no triggers, no JSON and no Storage bucket. It ranked first with two of the three judges and first on the combined total. The one judge who preferred B (codex) did so because of C's FR4/FR6 gaps. Those gaps are closed here by adding columns, with no new tables. That keeps C's size and fixes the reason it lost one vote.

**Score table** (judge totals, 6 criteria × 5 points = 30 max):

| Variant | claude-architect | codex | pragmatist | Sum | Ranks |
|---|---|---|---|---|---|
| A: Normalized current-state | 15 | 8 | 12 | 35 | 3, 3, 3 |
| B: Observation log + views | 22 | **20** | 20 | 62 | 2, 1, 2 |
| C: Minimal pragmatic | **25** | 19 | **26** | **70** | 1, 2, 1 |

**Key disagreements and how I resolved them.** Each one was checked against the DDL on a throwaway `supabase/postgres:17.6.1.167` container.

1. **A's DDL.** All judges said it was disqualifying, and I confirmed it. `diff` of variant-A.md lines 26–320 against variant-B.md lines 25–319 is empty, so A's schema is B's. None of A's Q1–Q8 can run against it. A's designer conceded this and resubmitted the schema as prose (A1–A5), but there was no executed DDL, so A stays last. Its ideas are scored on merit and grafted below.
2. **B vs C: is C's history loss disqualifying?** Codex said yes, the other two said no. Codex is right that FR4 ("never lose history") is a requirement. C as submitted overwrote company profile, person employer/title and event date, and C conceded this. The fix does not need B's observation layer. Profile and event title/date become snapshot columns on `scrapes`, written in the single final `status='ok'` update. Per-event employer and title go on `appearances`. With that, every fact a PRD question touches has history, and the object count stays at 5+3, against amended B's 11+10.
3. **Partial writes.** Codex said C leaks a new profile next to old signals when a worker dies midway. That was true of C as submitted. It is fixed by the same move: profile and signals live only on the scrape row and become visible only when that row turns `ok`. That is B's commit-marker idea, applied to one row. Appearances written before a crash stay visible. Each one is a true observed fact written by an idempotent upsert, so I accept this.
4. **Transient NULLs (2 → null → 9).** Every judge found this bug, and it exists in B and C (and in A's prose). I reproduced it in both. The fix is "last known value" reads, built from previous-non-null subqueries, not `lag()`. The convention: NULL means unknown, and 0 or `'{}'` means checked and found none. Verified: Q7 shows `2 → 9`, and Q2 keeps Acme while the middle scrape is null.
5. **Q3 undated funding.** The pragmatist wanted `coalesce(date, first_seen)`. The architect, A and codex showed that this gives false positives (Globex's years-old seed round, first seen today). Resolution (from B's amendment): an undated item counts only if it first appeared **after** an earlier ok scrape of that company, so it is genuinely new to us. It is also flagged `undated`, and dates are bounded above by `current_date`. Verified: Globex's old undated seed round is excluded, and Acme's newly appeared undated bridge round is included and flagged.
6. **Q5 `adopted`: default filter or not?** The architect wanted it on by default. Codex wanted it optional, matching the PRD. I followed the PRD: Q5 is "first seen after D" and returns `adopted` as a column. Q5b is the strict variant, built on `company_history.tech_added`. It also handles re-adoption (present → absent → present), which A raised against both rivals.
7. **Owner corrections: triggers (A), rejections table (B), or flags (C).** Triggers were rejected. PostgREST updates only the columns in the payload, so omitting a column already does what A's keep-first triggers did, and A conceded this. B's `rejections` table was also rejected. It only exists because B's observations are immutable, and its CHECK was broken. The result is three plain columns: `appearances.confirmed` (C), `companies.name_override` and `companies.tech_ignore` (from the C amendment). The last one closes the gap B raised: C had no way to reject a misdetected tool.
8. **Raw HTML / `superseded` (B).** All three judges called it unneeded for v1, and B conceded. Deferred. `scrapes.raw_path` is the reserved column name for later.
9. **Funding: events or a news table?** A and C put funding in `events`, B kept a separate news table. Kept in `events` with role `subject`, which matches PRD goal 3. B's main argument against this was C's monthly key, which merged rounds and split them across month ends. That is fixed by keying on the round (§3).

**Grafted.**
- From A: never let a failed sub-fetch erase known values (done as last-known-value reads, not payload stripping); owner corrections that survive re-scrape; `kind_reason` and `evidence` columns; registrable-domain test cases; re-adoption via set differences.
- From B: `status='ok'` as the commit marker for all snapshot data; NULL means unknown, with change rows only between known values; the Q3 undated rule plus an upper bound; `still_listed` (current versus ever-listed appearances); `children_*` counts in Q8; `merged_into` instead of cascade-delete; tracking-param-only URL stripping; the person fallback key never left empty.
- From C's own amendment: drop `appearances.first_scrape_id`; `on delete restrict`; id tie-breakers; the Q1 lookup by alias via `scrapes.url/final_url`; the round-based funding key; `parent_id is null` in Q8.
- From the pragmatist: sweep stuck `queued` rows at batch start (one `update`, no cron).

**Not grafted:** B's 11 tables/10 views, `rejections`, raw-HTML Storage, `superseded`, manual scrapes, `company_overrides` (one `name_override` column covers it); A's triggers, `locked`, and `company_tech` table.

**Verification.** The DDL below ran 3 times (empty, re-run, re-run with data) on `supabase/postgres:17.6.1.167`, with no errors. A seed covering the debate's edge cases was loaded:
- open_roles 2 → null → 9
- ats null mid-series
- a misdetected `jquery` in `tech_ignore`
- a sponsor dropped from the second event-page scrape
- a rejected exhibitor
- an old undated funding item and a new undated funding item
- a merged duplicate company
- a batch with 4 children

All Q1–Q8 below returned the expected rows. Re-upserting appearances left the count unchanged. `delete from companies` on a company with history is refused.

## 1. Summary and philosophy

1. **Five tables:** `companies`, `events`, `people`, `appearances` (one link table: a company or a person at or about an event, with a role) and `scrapes`.
2. **`scrapes` does three jobs:** batch queue and status (Q8), provenance log (FR6), and the per-scrape snapshot of every company signal and profile field (history: Q5, Q7). A scrape's snapshot is written in **one** final `update … set status='ok'`, so it is atomic without transactions.
3. **"Current" is a view.** `companies_current` is computed as the identity row, plus the profile from the latest ok scrape, plus tech and hiring from the latest scrape where that reading was *known*, minus owner corrections. Nothing is stored twice and nothing is synced.
4. **One `events` table** holds both meanings: gatherings the owner submits, and funding, earnings, press and hosted-webinar items found on company sites, separated by `kind`. Companies link to both through `appearances`.
5. **No JSON, no triggers, no functions.** Tech stack is `text[]`, queried with `= any(tech)`. Owner corrections are plain columns that upsert payloads never include.

**Philosophy:** one owner, the Supabase table editor, plain SQL. Each table and view exists because a PRD question needs it. Everything else in §8 can be added later with one `add column` or `create table`.

## 2. DDL

Run as `schema.sql`. Tables and indexes use `if not exists`. Views are dropped and recreated, so a column change never fails. FKs added after creation use a `do $$ … duplicate_object` guard. `unique nulls not distinct` needs PG15 or later.

```sql
-- Marketing signals DB (final: variant C + debate amendments). Idempotent: safe to run repeatedly.
-- 5 tables: companies, events, people, scrapes, appearances.  3 views: companies_current, company_history, tech_seen.
-- RLS on, no policies: only the service_role key (server-side) can read/write.
-- Convention: NULL = unknown (fetch/extractor failed). 0 or '{}' = checked and found none.

create table if not exists companies (
  id              bigint generated always as identity primary key,
  domain          text not null unique,          -- registrable domain: "acme.com"
  name            text,                          -- link-text fallback, written only in discover mode
  name_override   text,                          -- owner correction; wins over everything
  tech_ignore     text[] not null default '{}',  -- owner correction: tools wrongly detected for this company
  merged_into     bigint references companies(id),  -- owner merge: duplicate points at the kept row
  first_seen_at   timestamptz not null default now(),
  last_scraped_at timestamptz                    -- null = discovered, never scraped. Used for re-enqueue decisions.
);

create table if not exists events (
  id             bigint generated always as identity primary key,
  event_key      text not null unique,           -- see identity rules (§3)
  kind           text not null default 'event'
                 constraint events_kind_check check (kind in ('event', 'webinar', 'funding', 'earnings', 'press')),
  title          text,
  title_key      text generated always as (lower(regexp_replace(coalesce(title, ''), '[^a-zA-Z0-9]+', '', 'g'))) stored,
  url            text,
  event_date     date,                           -- gatherings: starts on. funding/earnings/press: announced on. null = undated
  location       text,
  amount_usd     bigint,                         -- funding only
  round          text,                           -- funding only: 'seed', 'series a', ...
  first_seen_at  timestamptz not null default now(),
  last_seen_at   timestamptz not null default now(),
  last_scrape_id bigint                          -- provenance (FK added below; scrapes is created later)
);

create table if not exists people (
  id             bigint generated always as identity primary key,
  person_key     text not null unique,           -- "li:<slug>" | "nm:<name_key>@<domain>" | "nm:<name_key>@event:<id>"
  name           text not null,
  job_title      text,                           -- latest seen; per-event history lives on appearances
  linkedin_url   text,
  company_id     bigint references companies(id) on delete restrict,
  company_name   text,
  merged_into    bigint references people(id),
  first_seen_at  timestamptz not null default now(),
  last_seen_at   timestamptz not null default now(),
  last_scrape_id bigint
);

create table if not exists scrapes (
  id               bigint generated always as identity primary key,
  batch_id         uuid,
  parent_id        bigint references scrapes(id) on delete set null,  -- company scrapes fanned out from an event page
  url              text not null,                -- as submitted
  final_url        text,                         -- after redirects
  kind             text constraint scrapes_kind_check check (kind in ('company', 'event')),  -- null while queued; owner may preset
  kind_reason      text,                         -- which classify rule fired, e.g. 'host:lu.ma', 'jsonld:Event'
  status           text not null default 'queued'
                   constraint scrapes_status_check check (status in ('queued', 'ok', 'error')),
  error            text,
  company_id       bigint references companies(id) on delete restrict,
  event_id         bigint references events(id) on delete restrict,
  created_at       timestamptz not null default now(),
  scraped_at       timestamptz,                  -- when the job finished (ok or error)
  -- snapshot written in ONE final update with status='ok'. History = these rows over time.
  title            text,                         -- page title (event scrapes: event title snapshot)
  event_date       date,                         -- event scrapes: event date snapshot
  name             text,                         -- company profile snapshot ...
  description      text,
  logo_url         text,
  linkedin_url     text,
  x_url            text,
  github_url       text,
  facebook_url     text,
  instagram_url    text,
  youtube_url      text,
  tech             text[],                       -- null = tech scan failed; '{}' = none found
  ats              text,
  careers_url      text,
  open_roles       int,                          -- null = ATS lookup failed / no ATS; 0 = no openings
  latest_post_date date,
  posts_last_90d   int,
  constraint scrapes_one_subject check (company_id is null or event_id is null)
);

do $$ begin
  alter table events add constraint events_last_scrape_fk foreign key (last_scrape_id) references scrapes(id) on delete set null;
exception when duplicate_object then null; end $$;
do $$ begin
  alter table people add constraint people_last_scrape_fk foreign key (last_scrape_id) references scrapes(id) on delete set null;
exception when duplicate_object then null; end $$;

-- Who appeared at / is the subject of an event. Exactly one of company_id / person_id.
create table if not exists appearances (
  id                bigint generated always as identity primary key,
  event_id          bigint not null references events(id) on delete cascade,
  company_id        bigint references companies(id) on delete restrict,
  person_id         bigint references people(id) on delete restrict,
  role              text not null
                    constraint appearances_role_check check (role in
                      ('sponsor', 'exhibitor', 'partner', 'organizer', 'speaker', 'listed', 'host', 'subject')),
  evidence          text,                        -- heading text or rule that produced the row, e.g. 'Gold Sponsors'
  job_title         text,                        -- person rows: title as printed on this page
  company_name      text,                        -- person rows: employer as printed on this page
  person_company_id bigint references companies(id) on delete set null,
  confirmed         boolean,                     -- owner: null = as extracted, true = checked, false = wrong (hidden)
  last_scrape_id    bigint references scrapes(id) on delete set null,
  first_seen_at     timestamptz not null default now(),  -- never in an upsert payload
  last_seen_at      timestamptz not null default now(),
  constraint appearances_one_party check ((company_id is null) <> (person_id is null)),
  constraint appearances_dedup unique nulls not distinct (event_id, company_id, person_id, role)
);

create index if not exists scrapes_batch_idx      on scrapes (batch_id);
create index if not exists scrapes_parent_idx     on scrapes (parent_id);
create index if not exists scrapes_company_ok_idx on scrapes (company_id, scraped_at desc, id desc) where status = 'ok';
create index if not exists scrapes_event_idx      on scrapes (event_id);
create index if not exists scrapes_queued_idx     on scrapes (created_at) where status = 'queued';
create index if not exists appearances_company_idx on appearances (company_id);
create index if not exists appearances_person_idx  on appearances (person_id);
create index if not exists events_kind_date_idx    on events (kind, event_date);
create index if not exists events_date_title_idx   on events (event_date, title_key);
create index if not exists people_company_idx      on people (company_id);

alter table companies   enable row level security;
alter table events      enable row level security;
alter table people      enable row level security;
alter table scrapes     enable row level security;
alter table appearances enable row level security;

drop view if exists companies_current;
drop view if exists company_history;
drop view if exists tech_seen;

-- One row per (unmerged) company. Profile from the latest ok scrape; tech and hiring from the latest ok scrape
-- where that reading was known, so one failed ATS call or tech scan never blanks the current row.
create view companies_current with (security_invoker = true) as
select c.id, c.domain,
       coalesce(c.name_override, p.name, c.name) as name,
       p.description, p.logo_url, p.linkedin_url, p.x_url, p.github_url,
       p.facebook_url, p.instagram_url, p.youtube_url,
       c.first_seen_at, c.last_scraped_at, p.id as scrape_id,
       case when t.tech is not null then array(select unnest(t.tech) except select unnest(c.tech_ignore)) end as tech,
       h.ats, h.careers_url, h.open_roles, h.scraped_at as hiring_as_of,
       p.latest_post_date, p.posts_last_90d
from companies c
left join lateral (select * from scrapes s where s.company_id = c.id and s.status = 'ok'
                   order by s.scraped_at desc, s.id desc limit 1) p on true
left join lateral (select s.tech from scrapes s where s.company_id = c.id and s.status = 'ok' and s.tech is not null
                   order by s.scraped_at desc, s.id desc limit 1) t on true
left join lateral (select s.ats, s.careers_url, s.open_roles, s.scraped_at from scrapes s
                   where s.company_id = c.id and s.status = 'ok' and s.open_roles is not null
                   order by s.scraped_at desc, s.id desc limit 1) h on true
where c.merged_into is null;

-- One row per ok company scrape, with the previous KNOWN value of each signal alongside.
-- tech_added / tech_removed are null (not "everything") when either side's tech reading is unknown.
create view company_history with (security_invoker = true) as
select h.*,
       case when h.tech is not null and h.prev_tech is not null
            then array(select unnest(h.tech) except select unnest(h.prev_tech)) end as tech_added,
       case when h.tech is not null and h.prev_tech is not null
            then array(select unnest(h.prev_tech) except select unnest(h.tech)) end as tech_removed
from (
  select s.company_id, s.id as scrape_id, s.scraped_at, s.url,
         (select p.name from scrapes p where p.company_id = s.company_id and p.status = 'ok' and p.name is not null
             and (p.scraped_at, p.id) < (s.scraped_at, s.id) order by p.scraped_at desc, p.id desc limit 1) as prev_name,
         s.name, s.description,
         (select array(select unnest(p.tech) except select unnest(c.tech_ignore))
            from scrapes p where p.company_id = s.company_id and p.status = 'ok' and p.tech is not null
             and (p.scraped_at, p.id) < (s.scraped_at, s.id) order by p.scraped_at desc, p.id desc limit 1) as prev_tech,
         case when s.tech is not null then array(select unnest(s.tech) except select unnest(c.tech_ignore)) end as tech,
         (select p.ats from scrapes p where p.company_id = s.company_id and p.status = 'ok' and p.ats is not null
             and (p.scraped_at, p.id) < (s.scraped_at, s.id) order by p.scraped_at desc, p.id desc limit 1) as prev_ats,
         s.ats,
         (select p.open_roles from scrapes p where p.company_id = s.company_id and p.status = 'ok' and p.open_roles is not null
             and (p.scraped_at, p.id) < (s.scraped_at, s.id) order by p.scraped_at desc, p.id desc limit 1) as prev_open_roles,
         s.open_roles, s.latest_post_date, s.posts_last_90d
  from scrapes s join companies c on c.id = s.company_id
  where s.status = 'ok'
) h;

-- One row per (company, tool) from known tech readings. adopted = an earlier ok scrape with a KNOWN tech
-- reading lacked the tool, i.e. we witnessed the change rather than seeing it on our first look.
create view tech_seen with (security_invoker = true) as
with seen as (
  select s.company_id, t.tool, min(s.scraped_at) as first_seen_at, max(s.scraped_at) as last_seen_at
  from scrapes s
  join companies c on c.id = s.company_id
  cross join lateral unnest(s.tech) as t(tool)
  where s.status = 'ok' and s.tech is not null and not (t.tool = any(c.tech_ignore))
  group by s.company_id, t.tool
)
select seen.company_id, seen.tool, seen.first_seen_at, seen.last_seen_at,
       exists (select 1 from scrapes p
               where p.company_id = seen.company_id and p.status = 'ok' and p.tech is not null
                 and not (seen.tool = any(p.tech)) and p.scraped_at < seen.first_seen_at) as adopted
from seen;

drop table if exists scraped_pages;  -- held only test rows (PRD §7)
```

**Why these shapes:**
- **`companies` is identity plus owner corrections only.** The profile lives on the scrape snapshot, so it has history and appears atomically. `companies.name` is only the link-text fallback from discover mode (an event-page anchor). `companies_current` prefers `name_override`, then the latest scraped name, then that fallback.
- **One `appearances` table** with `appearances_one_party` and `unique nulls not distinct (event_id, company_id, person_id, role)`, so PostgREST `upsert(on_conflict="event_id,company_id,person_id,role")` works for both company and person links.
- **Person rows on `appearances` carry `job_title`, `company_name` and `person_company_id`** as printed on that page. That is employment history per event without a new table. `people.job_title` and `people.company_id` hold the latest values.
- **`on delete restrict`** on every FK into `companies`/`people` from history tables. To merge, set `merged_into`, never delete (§8). The only cascade is event to appearances, which removes a bogus event's links.
- **Socials are 6 columns**, because the set in `signals.SOCIAL_HOSTS` is fixed. Check constraints on `kind`, `role` and `status` catch extractor typos.

## 3. Identity and dedup rules

Every entity has exactly one natural-key column with a unique constraint. Keys are computed in `storage.py` by one small function each, and every write is `upsert(..., on_conflict=<key>)`.

| Entity | Key | Rule |
|---|---|---|
| Company | `companies.domain` | `registrable_domain(host)`: lowercase, strip `www.`, keep the last 2 labels, or 3 when the last two are in a hand-kept second-level-suffix set (`co.uk`, `com.au`, `co.jp`, `com.br`, … about 15). Test: `jobs.acme.com`, `www.acme.com` and `acme.com` all map to `acme.com`. Today's `scraper.domain_of()` does **not** do this. It must be replaced (FR5). |
| Person | `people.person_key` | `li:<slug>` from `linkedin.com/in/<slug>` (lowercase, no trailing slash or query). Otherwise `nm:<name_key>@<company domain>` when the employer resolved, else `nm:<name_key>@event:<event_id>`. `name_key` is `[a-z0-9]` only. The last segment is never empty, so namesakes don't merge. |
| Event (gathering) | `events.event_key` | Canonical URL (`rel=canonical`, else final URL): lowercase host, no `www.`, no fragment, no trailing slash. Strip **only** tracking params (`utm_*`, `ref`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`), so `?event_id=123` survives. **Cross-host dedup:** before inserting a new key, look up `events where event_date = $d and title_key = $k`, and reuse the row on a hit. |
| Event (funding) | `events.event_key` | `funding:<domain>:<round>` when a round is parsed (`seed`, `series a`, …). A company raises each named round once, so /press and /blog copies merge and two rounds in one month stay separate. With no round: the item's canonical URL. Undated items are fine: the key needs no date. |
| Event (earnings) | `events.event_key` | `earnings:<domain>:<yyyy-mm-dd>`, else the item's canonical URL when undated. |
| Event (press, hosted webinar) | `events.event_key` | Canonical URL of the item. |
| Appearance | `(event_id, company_id, person_id, role)` | One row per party per role per event. |

**Upsert modes (supabase-py):**
- **Discover** (we only saw a link, e.g. a sponsor logo): `upsert(rows, on_conflict="domain", ignore_duplicates=True)`, then `select id, domain … in (…)`. Link text never overwrites anything.
- **Observe** (`people`, `events`): the payload strips `None` (`{k: v for k, v in row.items() if v is not None}`) and never includes `first_seen_at`. Set `last_seen_at` and `last_scrape_id`.
- **Companies get no profile upsert at all.** The profile goes into the scrape row. The only company write in a company scrape is `last_scraped_at`.
- **Appearances:** the payload carries `last_seen_at`, `last_scrape_id`, `evidence` and, for people, `job_title`, `company_name` and `person_company_id`. It never includes `first_seen_at` or `confirmed`. **Dedup each payload in Python before sending:** Postgres rejects an `ON CONFLICT DO UPDATE` that hits the same key twice in one statement (verified).

Re-scraping the same URL gives the same companies, people, events and appearances, plus one new `scrapes` row.

## 4. History and change detection

History is **the ok `scrapes` rows of a company (or event), ordered by `(scraped_at, id)`**. Rule: NULL means the reading is unknown, and it is never compared.

- **Current values** (`companies_current`): the profile comes from the latest ok scrape. `tech` comes from the latest ok scrape with `tech is not null`. `ats`, `careers_url` and `open_roles` come from the latest ok scrape with `open_roles is not null`, exposed with `hiring_as_of`. One failed ATS call or tech scan never blanks the row or drops it from Q2.
- **"Open roles 2 → 9", "ATS changed", "renamed"** (`company_history`): each ok scrape next to the *previous known* `open_roles`, `ats`, `tech` and `name`. `tech_added` and `tech_removed` are NULL unless both sides are known, so a failed scan never reports the whole stack as removed. Hiring surge: `open_roles > prev_open_roles + 3` (both non-null by construction).
- **"Adopted T since D":** `tech_seen` gives first and last seen per (company, tool) from known readings. `adopted` is true when an earlier ok scrape with a known tech reading lacked the tool. For every adoption *event*, including re-adoption, use `company_history.tech_added` (Q5b).
- **"Raised funding recently":** a dated fact: `events.kind='funding'` linked by `role='subject'`. Undated items count only if they first appeared after an earlier scrape of that company (Q3).
- **"Sponsoring next month":** `role='sponsor'` joined to `event_date`.
- **Still listed:** `appearances.last_scrape_id >= (latest ok scrape of that event)`. Removed sponsors and withdrawn speakers remain as history, but can be filtered out.
- **Person job changes:** `appearances.job_title` and `company_name` per event, ordered by `last_seen_at`. **Event reschedules:** `scrapes.event_date` per event-page scrape.
- **Owner corrections apply everywhere:** `tech_ignore` is subtracted in all three views, `confirmed = false` is filtered in every query, and merged companies are hidden from `companies_current`.

## 5. Ingestion flow

```
POST /batch (≤500, optional kind column) or POST /scrape {url, kind?}
  └─ UPDATE scrapes SET status='error', error='worker lost', scraped_at=now()
       WHERE status='queued' AND created_at < now() - interval '1 hour'      ← stuck-row sweep
  └─ INSERT scrapes(batch_id, url, kind?, status='queued')                    ← Q8 visible immediately
  └─ spawn worker(scrape_id, url)
worker (jobs.run_job), everything inside one try:
  1. fetch + parse (existing fetch.py / htmlparse.py: SSRF guard, robots, caps, politeness unchanged)
  2. kind, kind_reason = scrape.kind or classify(page, url)
  3a. company → signals.collect() + signals.company_events()
  3b. event   → eventpage.extract()
  4. write entity/children/link rows (table below)
  5. ONE UPDATE scrapes SET status='ok', kind, kind_reason, final_url, company_id|event_id,
       scraped_at=now(), <all snapshot columns>                               ← commit marker
  except: UPDATE scrapes SET status='error', error=<msg>, scraped_at=now()    ← FR7
```

**Classify** (first match wins; the rule name goes into `kind_reason`):
1. Host in `EVENT_HOSTS` (`lu.ma`, `eventbrite.*`, `meetup.com`, `hopin.com`, `sessionize.com`, …).
2. A top-level JSON-LD `Event` (or subtype) with `startDate`, and no Organization main entity.
3. At least 8 distinct outbound registrable domains (excluding social, ticketing and CDN) **and** a heading matching `sponsor|exhibitor|partner|speaker`.
4. Otherwise company. The owner overrides with `kind`.

| Step | Company path | Event path |
|---|---|---|
| Entity | `companies` discover-upsert on domain, then `update companies set last_scraped_at=now()` | `events` observe-upsert on `event_key` (after the cross-host title/date check) |
| Children | `events` observe-upsert per found item (funding/earnings/press/hosted webinar or meetup) | `companies` discover-upsert per outbound company link; `people` observe-upsert per JSON-LD Person/performer or speaker card |
| Links | `appearances`: company → item, role `subject` or `host`, with `evidence` = the rule | `appearances`: company → event, role from the nearest preceding heading (else `listed`), `evidence` = the heading text; person → event, `speaker`, with job title and employer as printed |
| Commit | `scrapes` ← `ok`, profile + tech + hiring + blog snapshot | `scrapes` ← `ok`, `title`, `event_date` snapshot |
| Fan-out | none | Discovered companies with `last_scraped_at is null or < now() - 30 days`: `insert scrapes(batch_id=parent's, parent_id=this, status='queued')` and spawn. Capped at 200 per page; overflow stays queued. |

For future AI extraction: the same tables and keys, with an `extractor` column added (§8).

## 6. Acceptance queries

All of these ran against the DDL above with the seed described in §0. None uses a JSON path.

**Q1: Sponsors and exhibitors of event X, with tech stack and open roles**
```sql
select c.domain, c.name, a.role, a.evidence, c.tech, c.ats, c.open_roles,
       a.last_scrape_id >= (select max(id) from scrapes where event_id = e.id and status = 'ok') as still_listed
from events e
join appearances a on a.event_id = e.id and a.confirmed is not false
join companies_current c on c.id = a.company_id
where e.id in (select event_id from scrapes where status = 'ok' and :url in (url, final_url)
               union select id from events where event_key = :normalized_url)
  and a.role in ('sponsor', 'exhibitor')
order by still_listed desc, a.role, c.name;
```
This finds the event by any URL that resolved to it, or by its key. Add `and a.last_scrape_id >= …` to see only current sponsors. Discovered but unscraped companies show NULL tech and roles. Seed: Acme (`still_listed=t`, `{hubspot}`, 9), Initech (`f`, dropped from the page). Globex is hidden because `confirmed=false`.

**Q2: Companies using HubSpot (tool T) that are hiring**
```sql
select domain, name, open_roles, careers_url, hiring_as_of
from companies_current
where 'hubspot' = any(tech) and open_roles > 0
order by open_roles desc;
```
Seed: Acme stays at 9 even though its middle scrape's ATS lookup failed.

**Q3: Companies with a funding event in the last 90 days, with amount when known**
```sql
select c.domain, c.name, e.event_date, e.event_date is null as undated, e.round, e.amount_usd, e.url
from events e
join appearances a on a.event_id = e.id and a.role = 'subject' and a.confirmed is not false
join companies_current c on c.id = a.company_id
where e.kind = 'funding'
  and (e.event_date between current_date - 90 and current_date
       or (e.event_date is null and e.first_seen_at >= current_date - 90
           and exists (select 1 from scrapes p where p.company_id = c.id and p.status = 'ok'
                         and p.scraped_at < e.first_seen_at)))
order by coalesce(e.event_date, e.first_seen_at::date) desc;
```
Seed: Acme's Series B (dated) and Acme's undated bridge round (it appeared after an earlier scrape, and is flagged). Globex's undated seed, found on its first scrape, is excluded.

**Q4: Speakers at events in the next 60 days and the companies they work for**
```sql
select p.name, coalesce(a.job_title, p.job_title) as job_title,
       coalesce(c.name, a.company_name, p.company_name) as company, c.domain,
       e.title as event, e.event_date, p.linkedin_url
from events e
join appearances a on a.event_id = e.id and a.role = 'speaker' and a.confirmed is not false
join people p on p.id = a.person_id
left join companies_current c on c.id = coalesce(a.person_company_id, p.company_id)
where e.kind in ('event', 'webinar') and e.event_date between current_date and current_date + 60
order by e.event_date, p.name;
```

**Q5: Companies that adopted tool T since date D (first seen after D)**
```sql
select c.domain, c.name, t.first_seen_at, t.adopted
from tech_seen t
join companies_current c on c.id = t.company_id
where t.tool = 'hubspot' and t.first_seen_at >= :d
order by t.first_seen_at;
```
`adopted = true` means we saw the tool absent before, which is a real change. Seed: Acme `t`, Globex `f` (it had HubSpot on our first look).

**Q5b (strict, includes re-adoption): adoption events since D**
```sql
select c.domain, h.scraped_at
from company_history h join companies c on c.id = h.company_id
where 'hubspot' = any(h.tech_added) and h.scraped_at >= :d
order by h.scraped_at;
```

**Q6: Every event a given company appeared at, with role**
```sql
select e.event_date, e.kind, e.title, a.role, a.evidence, e.url
from companies c
join appearances a on a.company_id = c.id and a.confirmed is not false
join events e on e.id = a.event_id
where c.domain = 'acme.com'
order by e.event_date desc nulls last;
```
This includes funding and press (`role='subject'`), per PRD goal 3b. Add `and e.kind in ('event','webinar')` for gatherings only.

**Q7: Current profile for a company, plus how its signals changed across scrapes** (two selects from the provided views)
```sql
select * from companies_current where domain = 'acme.com';

select h.scraped_at, h.prev_name, h.name, h.prev_open_roles, h.open_roles, h.prev_ats, h.ats,
       h.tech_added, h.tech_removed, h.latest_post_date, h.posts_last_90d
from company_history h join companies c on c.id = h.company_id
where c.domain = 'acme.com'
order by h.scraped_at, h.scrape_id;
```
Seed (2 → null → 9): the last row shows `Acme → Acme Inc`, `open_roles 2 → 9`, `greenhouse → greenhouse`, `tech_added {hubspot}`, `tech_removed {wordpress}`. The middle (unknown) row has NULL diffs instead of fake removals. `jquery` is hidden by `tech_ignore`.

**Q8: Batch status per submitted URL**
```sql
select s.id, s.url, s.kind, s.status, s.error, coalesce(c.domain, e.title) as result,
       (select count(*) from scrapes k where k.parent_id = s.id) as children,
       (select count(*) from scrapes k where k.parent_id = s.id and k.status = 'queued') as children_queued,
       (select count(*) from scrapes k where k.parent_id = s.id and k.status = 'error') as children_error
from scrapes s
left join companies c on c.id = s.company_id
left join events e on e.id = s.event_id
where s.batch_id = :batch_id and s.parent_id is null
order by s.id;

select status, count(*) from scrapes where batch_id = :batch_id group by status;  -- includes children
```

## 7. Migration and impact on existing code

**Data:** `scraped_pages` holds only test rows (PRD §7), so `schema.sql` ends with `drop table if exists scraped_pages`. There is no importer. Re-submit any real domains as a batch.

| File | Change | Size |
|---|---|---|
| `schema.sql` | Replaced by §2 | ~200 lines |
| `storage.py` | `queue(batch_id, urls, parent_id=None, kind=None)`, `sweep_stuck()`, `save_company(scrape_id, result)`, `save_event(scrape_id, result) -> [domains to enqueue]`, `mark_ok(scrape_id, snapshot)`, `mark_error(scrape_id, msg)`, `get_batch(batch_id)`. Key helpers: `registrable_domain`, `person_key`, `event_key`, `canonical_url`. Each save is 3–5 PostgREST calls. | ~40 → ~160 lines |
| `jobs.py` | `run_job`: route on `kind`, then save, then `mark_ok`. The **whole body** goes inside the `try`. Today `save(record)` sits outside the handler, so a storage failure leaves the row `queued`. | ~30 → ~50 |
| `scraper.py` | Returns `{"kind", "kind_reason", ...flat signal keys}`. Adds `classify()`, `EVENT_HOSTS`, and `registrable_domain` replacing `domain_of` for identity. | +~45 |
| `signals.py` | The 4 existing extractors are unchanged. New `company_events()`: JSON-LD Event, event-platform links, `/events`, newsroom RSS headline rules (`raises\|series [a-z]\|seed\|funding` → funding; `\$([\d.]+)\s*(million\|m\|billion\|b)` → amount; round parse), and IR items. | +~90 |
| `eventpage.py` (new) | `extract(page)`: event fields, outbound companies with heading role and evidence, and people (JSON-LD Person/performer, speaker cards with LinkedIn). | ~130 |
| `htmlparse.py` | Record the last `h1–h4` text before each anchor | +~10 |
| `web.py` | `/scrape` creates a scrape row and also persists failures. `/batch` accepts an optional `kind` column and calls `sweep_stuck()`. The table JS reads flat columns and child counts. | ~±40 |
| `modal_app.py` | Worker takes `scrape_id`; `enqueue` = `queue()` + `spawn`, capped at 200 per page. Add `eventpage` to `add_local_python_source`. | ~±10 |
| tests | Fakes for the new storage functions. Fixtures: one event page, one press RSS feed. Cases for `registrable_domain`, `classify`, `event_key` and `person_key`, plus one SQL smoke test (§0 seed) that runs Q1–Q8. | +~170 |

No new dependencies.

## 8. Extensibility

- **AI extraction:** `add column if not exists extractor text not null default 'rules'` on `scrapes`, `appearances` and `events`. An AI pass is a new `scrapes` row (its own snapshot), and it writes appearances through the same keys, so they dedup. For re-extraction of old pages, add `scrapes.raw_path text` (gzipped HTML in Storage, keyed by scrape id). That column name is reserved, and the feature is deferred until it is wanted.
- **CRM sync:** match on `companies.domain` and `people.person_key`. Add `crm_id` and `crm_synced_at` columns. The cursor is `scrapes.id` (monotonic, insert order) rather than a timestamp, so nothing is missed. The views are the export contract.
- **Owner corrections:**
  - Wrong link or role: `appearances.confirmed=false` (and insert the right role with `true`).
  - Wrong tool: append it to `companies.tech_ignore`.
  - Wrong name: `companies.name_override`.
  - Wrong classification: re-submit with `kind`.
  - When date or description corrections are needed: add `events.date_override` / `companies.description_override` and use `coalesce` in the view or query.
- **Merging duplicates** (no deletes; `restrict` blocks them):
  ```sql
  delete from appearances a using appearances k   -- drop links the kept row already has
   where a.company_id = :dup and k.company_id = :keep and k.event_id = a.event_id and k.role = a.role;
  update appearances set company_id = :keep where company_id = :dup;
  update scrapes     set company_id = :keep where company_id = :dup;
  update companies   set merged_into = :keep where id = :dup;
  ```
  The same pattern works for `people`.
- **New signal**, e.g. `pricing_page boolean`: one `add column` on `scrapes`, plus a line in `companies_current` and `company_history`. History comes free.
- **Scale:** past about 100k companies, materialize `companies_current` or add a `company_tech(company_id, tool)` table with an index.

## 9. Known weaknesses

1. **`adopted` needs at least 2 scrapes.** A company first scraped after D that already uses the tool shows in Q5 with `adopted=false`. That is the honest answer, and Q5b is the strict form.
2. **Tool removal is not debounced.** One known-but-incomplete tech scan (e.g. a CDN script missing once) reports `tech_removed`. Upgrade path: require absence in 2 consecutive known readings in `company_history`.
3. **The profile comes from the latest ok scrape as-is.** If the page drops its `og:description`, the current description becomes NULL. That is truthful to the page, but it differs from the last-known-value rule used for tech and hiring.
4. **The correlated subqueries in `company_history` and `companies_current` run per row.** That takes milliseconds at the PRD's scale (thousands of companies, a few scrapes per month). Materialize when it doesn't.
5. **Partial appearance writes are visible** if a worker dies before `ok`. Each is a true fact. Removal detection uses `>=` against ok event scrapes only, so it is not fooled.
6. **Cross-host event dedup is an app-side check** (date + title_key). Two workers racing on the same new event on two hosts can create two rows. Fix by merging (§8). It is rare.
7. **Person identity is weak without LinkedIn.** The same speaker at two events with no resolved employer becomes two rows. A person who later gains an `li:` key gets a second row: merge by hand with `people.merged_into`.
8. **FR6 granularity.** Each fact traces to a scrape (URL + time). Sub-fetches inside a company scrape (ATS API, RSS feed) are not recorded separately. Add `scrapes.hiring_source_url` if it matters.
9. **`events` mixes gatherings and announcements.** `event_date` means "starts on" or "announced on" depending on the row, so filter by `kind` (Q4 does).
10. **Registrable domain comes from a hand-kept suffix list**, not the PSL. Use `tldextract` if it bites.
11. **No raw HTML**, so old pages can't be re-extracted, and vanished event pages are lost. This is a known trade-off for cost.
12. **Fan-out cost.** A 500-URL batch of event pages could spawn tens of thousands of fetches. There is a cap of 200 per page plus the 30-day skip, but no per-batch cap yet.
