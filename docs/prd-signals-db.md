# PRD: Marketing Signals Database

Status: draft v2 (research amendments from docs/research-signals-practice.md) · Owner: repo owner · Date: 2026-10-08

## 1. Problem
The owner collects marketing signals about companies and people by sending URLs to a scraper. Today
every scrape is one row in `scraped_pages` with all signals packed into a single `signals` JSON column.
That is hard to query, has no "current" record per company, no people, and no notion of events.
URLs sent in are often **event pages** (conferences, meetups, expos) that list **many companies and
people**, not just single company homepages.

## 2. Users
- **Primary: the owner (single marketer/founder).** Sends single URLs or batches (paste/CSV, ≤500),
  then queries the data in Supabase (table editor / SQL) to build target lists and spot timing signals.
- **Downstream consumers (later):** CRM sync, outreach tools, dashboards. Not in v1.

"Users" in this product means **people/contacts captured from pages** (speakers, organizers, team
members), not app login accounts. There is no login: the endpoint is intentionally unauthenticated
(owner's decision, risk accepted).

## 3. Goals
1. **Queryable with plain SQL** – no digging through nested JSON for routine questions.
2. **One current record per company and per person**, plus history so changes can be detected.
3. **Events as first-class data**, in two senses:
   - *Event pages the owner submits* → the event plus every company/person on it, with their role.
   - *Events signal on a company* → meetups/webinars they host or attend, funding announcements,
     earnings releases, press.
4. **Change/timing signals**: e.g. "adopted HubSpot", "dropped a competitor's tool", "open roles 2 → 9",
   "raised Series A last month", "sponsoring an event next month".
5. **Prioritization**: a weekly worklist ranked by a simple, owner-tunable score and an owner-set tier.
6. **CRM-ready**: rows carry the IDs/timestamps needed to export changes to a CRM later (sync itself is a non-goal).

## 4. Non-goals (v1)
- AI/LLM extraction (removed by owner decision; rule-based only). Design must allow adding it later.
- Authentication, multi-tenant accounts, RLS policies beyond service-role-only.
- Paid data providers (Crunchbase, Apollo, Clearbit); external news search.
- Email/phone enrichment of people; JS-rendered pages (headless browser).
- CRM sync job, UI beyond the existing single page.
- Later (not v1): dismiss/snooze state on signals, Slack digest, ad-audience export, multi-domain company
  aliases, person job-change tracking. Not recommended: third-party intent data, ML/decay scoring,
  stored score columns, vendor IDs.

## 5. Example questions the data must answer (acceptance queries)
- Q1 Sponsors/exhibitors of event X, with their tech stack and open roles.
- Q2 Companies using tool T whose open roles rose in the last 90 days.
- Q3 Companies with a funding event in the last 90 days (default window), with round and amount when known.
- Q4 Speakers at events in the next 60 days and the companies they work for.
- Q5 Companies that adopted tool T since date D (first seen after D) **or dropped it** (no longer seen
  since D) – the competitor-displacement case.
- Q6 Every event a given company appeared at, with role.
- Q7 For a company: current profile plus how its signals changed across scrapes.
- Q8 Batch status: queued/ok/error per submitted URL.
- Q9 **This week's worklist**: companies with any signal first seen in the last 7 days, ranked by score
  then tier (tier 0 excluded), with each signal's type, date and source URL.
- Q10 **Event playbook**: events starting in the next 60 days or ended in the last 14 days, with
  companies by role, speakers by company, and each company's signals from the last 90 days.
- Q11 **CRM export delta**: companies and people changed since their `crm_synced_at` (or never synced).

## 6. Functional requirements
- FR1 Accept single URL or batch (≤500). Classify each URL as **event page** or **company site**
  (known event hosts like lu.ma, eventbrite, meetup.com; JSON-LD `Event`; many outbound company links).
- FR2 Event page → create/update the event; extract companies (outbound links to company domains,
  skipping social/ticketing/CDN; role from section heading: sponsor/exhibitor/partner/organizer) and
  people (JSON-LD `Person`/`performer`, speaker cards with LinkedIn links); enqueue each new company
  for a company scrape.
- FR3 Company site → profile, socials, tech stack, hiring (ATS + open roles), blog activity (existing
  extractors) **plus events signal**: JSON-LD `Event`, links to event platforms, `/events` pages,
  press/newsroom RSS & pages (headline rules: raises / Series X / seed / funding → funding, amount
  via "$X million"), investor-relations pages (earnings/results items with dates).
- FR4 Upsert current state; never lose history. Re-scraping is idempotent for current state.
- FR5 Identity/dedup: company by registrable domain; person by LinkedIn URL, else (normalized name,
  company); event by canonical URL (and date+title for duplicates across hosts).
- FR6 Every stored fact should be traceable to its source URL and scrape time.
- FR7 Errors per URL are recorded and visible in batch status; one failure never kills a batch.
- FR8 **Uniform signals**: every signal exposes type, occurred_on (date it happened; null if unknown),
  seen_at (first seen by us), detail, amount (when relevant) and source URL, queryable in one place.
  Signal types at least: tech_added, tech_removed, hiring_up, funding, earnings, press, event_hosted,
  event_attended/sponsored. Freshness windows: funding, events, hiring 90 days; a tool counts as current
  if last seen within 24 months.
- FR9 **Tier & score**: owner sets a company tier (1–3; 0 = excluded) and every ranked query respects it.
  Score = sum of owner-editable per-type weights over signals inside each type's window; computed at query
  time, never stored.
- FR10 **CRM hooks**: companies and people carry `crm_id`, `crm_synced_at`, `updated_at`.
- FR11 **Tool history**: first_seen and last_seen per (company, tool) so adoption and drops are detectable.
  Events carry start and end dates. Keep all history (no pruning in v1). Event roles include
  sponsor, exhibitor, partner, organizer, speaker_employer, contestant, subject.

## 7. Non-functional requirements & constraints
- Stack is fixed: Python, FastAPI on **Modal**, **Supabase Postgres** (service_role key server-side),
  GitHub Actions CI/CD (tests on PR, deploy on merge to `main`).
- Rule-based extraction only; stdlib + httpx; small, readable code (owner prefers minimal, boring
  designs and dislikes "everything in a JSON column").
- Scale: thousands of companies, tens of thousands of people/event links, a few scrapes per company
  per month. Single owner. Cost-sensitive.
- Existing safety stays: SSRF guard, response caps, robots.txt, per-domain politeness.
- Migrations must be idempotent SQL in the repo; existing `scraped_pages` data may be migrated or
  dropped (it holds only a handful of test rows).

## 8. Known limitations to accept or mitigate
- Companies shown only as logos/text without links will be missed.
- People extraction is patchy without structured markup.
- Funding only from the company's own press pages; earnings only for public companies with IR pages.

## 9. Success metrics
- All Q1–Q11 answerable with a single readable SQL query (no JSON path expressions) or a provided view.
- Re-scraping the same URL twice yields no duplicate companies/people/events.
- On a sample of 10 real event pages, ≥70% of linked sponsor/exhibitor companies captured.

## 10. Design guidance from research (see docs/research-signals-practice.md)
- Prefer `signals` as a **view** (union over stored facts) rather than a duplicated table; promote to a
  table only when signals need per-row state (e.g. dismissed).
- One small owner-editable table `signal_types(type, weight, window_days)` and a `company_scores` view.
- Identity keys match industry practice: company = domain, person = LinkedIn URL (else name+company).

## 11. Open questions for design
- How to model history: snapshot rows vs. per-fact first_seen/last_seen vs. append-only observations.
- Whether "event I submitted" and "company event signal" share one table.
- How much raw data to keep (raw HTML in storage? raw JSON?) for re-extraction later.
- Where to put classification/role confidence, and how to let the owner correct bad extractions.
