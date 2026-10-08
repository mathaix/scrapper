# Research: how marketing-signals databases are used in practice

Date: 2026-10-08 · Feeds: `docs/prd-signals-db.md` §5 and `docs/designs/variant-{A,B,C}.md`

## Summary

Commercial tools all converge on the same shape: a **company keyed by domain**, a **person keyed by LinkedIn URL** (email where they have it), and a **dated, typed list of signals** per company. Each tech install has a first-seen and a last-seen date. Users consume signals as a **recent, ranked worklist** (alerts, table rows), not as raw history. Freshness windows are short: weeks for intent and funding, about 3 months for hiring, up to about 2 years for tech installs. For this tool, that means four changes. Add a "what's new this week, ranked" query. Expose signals through one `signals` view with a type, date, strength and source. Keep first/last-seen on tech. Add two CRM columns now so a later sync is cheap. Everything else (intent data, alert delivery, sync jobs, ML scoring) can wait.

## Findings by topic, with sources

**1. Use cases**
- *ABM target lists.* ABM is run in tiers (1:1, 1:few, 1:many). The 1:many tier is built from an ICP-matched list with signals overlaid on it ([growigami](https://growigami.com/glossary/abm-tiers), [skills.sh ABM playbook](https://www.skills.sh/pfoy/growth-skills/abm-1-to-many-playbook)). One source describes tiers as a resource-allocation decision ([DemandScience](https://demandscience.com/resources/blog/abm-account-tiers/)). Account counts per tier vary widely between sources (same links).
- *Lead scoring.* HubSpot separates **fit** (who they are) from **engagement** (what they do), and scores companies as well as contacts. Practitioners say engagement scores should decay ([glitter.io guide](https://www.glitter.io/guides/understanding-lead-scoring-in-hubspot-engagement-fit-and-combined-approaches), [HubSpot community](https://community.hubspot.com/t5/Tips-Tricks-Best-Practices/Lead-Score-Form-Submission-Decay/m-p/288572)). One Pardot guide resets the score after 45 days of no activity ([thespotforpardot](https://thespotforpardot.com/2024/01/05/how-to-deal-with-score-decay-in-account-engagement/)).
- *Timing triggers.* Clay's built-in signals are new hires (last 3 months), promotions, job changes and news & fundraising ([Clay University: Signals](https://university.clay.com/docs/signals), [New hire signal](https://www.university.clay.com/docs/new-hire-signal-overview)). Common Room has organization-level job listings and news/events, updated daily, plus contact job changes ([Common Room native signals](https://commonroom.io/docs/signals/common-room-native-signals)). ZoomInfo "Scoops" are typed, dated events, including types such as "Event" and "Facilities Relocation / Expansion" ([ZoomInfo Enrich Scoops](https://docs.gtm.ai/reference/enrichinterface_enrichscoop.md)).
- *Event-led outreach.* Trade-show playbooks start outreach about 2 months out, send VIP invites at 6 weeks and reminders at 1 week, then follow up the same day after the show ([IFDA roadmap](https://www.ifdaonline.org/wp-content/uploads/2024/02/Shepard-Roadmap.pdf)). Post-event follow-up needs to be personalised, or attendees report it as spam ([CMWorld sponsor kit](https://www.contentmarketingworld.com/wp-content/uploads/2022/05/Sponsor_Exhibitor_Kit_2022-1.pdf)). Crunchbase models event roles as `appearance_type` ∈ {contestant, exhibitor, organizer, speaker, sponsor} ([Nodepit Crunchbase v4 connector](https://nodepit.com/node/com.nodepit.nodes.crunchbase.v4.operation.lookupeventappearancessinglecard.LookupEventAppearancesSingleCardNodeFactory), [Crunchbase help](https://support.crunchbase.com/hc/en-us/articles/115011962088-Add-an-Event-to-a-Profile)).
- *Competitive intelligence.* Technographic vendors sell "who runs product X". HG returns one row per product install at a company, with vendor and category ([HG technographic skill](https://phoenix.hginsights.com/gtm/skills/hg-technographic), [HG Technographic object](https://support.hginsights.com/articles/6258387953-hg-technographic-object)). *Inference:* tracking competitors' customers and lost installs is the same query as Q2/Q5, with the tool set to the competitor.
- *CRM enrichment.* Apollo enriches an organization by `domain` (or `linkedin_url` / `website`) and a person by `email`, `linkedin_url` or name + domain ([Apollo org enrichment](https://docs.apollo.io/reference/organization-enrichment), [Apollo people enrichment](https://docs.apollo.io/reference/people-enrichment)).

**2. How the data is used**
- *Intent* is a weekly score that compares the last 3 weeks with a 12-week baseline, with a threshold around 60–70 ([Bombora 6sense FAQ](https://customers.bombora.com/hubfs/CRC_Brand_Files%20and%20Videos/Partners_6sense/6sense-FAQs.pdf), [Bombora thresholding](https://customers.bombora.com/crc-brand/thresholding)). ZoomInfo intent returns `signalScore` (60–100), `signalDate` and `audienceStrength` ([ZoomInfo Enrich Intent](https://docs.gtm.ai/reference/enrichinterface_enrichintent.md)). This product cannot produce intent data (no third-party data).
- *Funding.* Advice on when to reach out ranges from 48 hours to 90 days after the announcement. Critics point out that funding proves capacity, not intent, so it should be paired with fit ([lemlist](https://www.lemlist.com/blog/funding-rounds-as-buying-intent-signals), [salesmotion](https://salesmotion.io/blog/funding-round-sales-trigger), [fundup](https://fundup.ai/blog/funding-round-not-a-buying-signal)). These are vendor blogs, so treat them as directional.
- *Hiring* windows run about 3 months ([Clay](https://www.university.clay.com/docs/new-hire-signal-overview)). Job listings refresh daily ([Common Room](https://commonroom.io/docs/signals/common-room-native-signals)).
- *Technographics* change slowly. HG keeps a "Date First Verified" and a "Date Last Verified" per install ([HG](https://support.hginsights.com/articles/6258387953-hg-technographic-object)), and advises downgrading signals last verified more than 24 months ago ([HG skill](https://phoenix.hginsights.com/gtm/skills/hg-technographic)). BuiltWith exposes first and last index dates per technology group ([BuiltWith Free API](https://api.builtwith.com/free-api)).
- *Flow.* Signals land as table rows (Clay), Slack team alerts (Common Room supports custom alerts on contacts, organizations and activity: [Team Alerts](https://commonroom.io/docs/using-common-room/team-alerts-page)) or CRM objects (HG writes a child object linked to the Account: [HG](https://support.hginsights.com/articles/6258387953-hg-technographic-object)). A score threshold can trigger a workflow, such as reassigning the owner ([glitter.io](https://www.glitter.io/guides/understanding-lead-scoring-in-hubspot-engagement-fit-and-combined-approaches)).

## Prior-art data models

| Tool | Entities | Identity / dedup keys | History approach |
|---|---|---|---|
| Clay | Tables of companies and people; signals as rows | User picks the dedupe column. Staff recommend company domain, falling back to LinkedIn URL. Matching is exact-string ([Clay community](https://community.clay.com/x/support/lqv9irieqts1/how-to-delete-duplicate-rows-from-a-clay-table)) | A signal run adds rows; "update outdated rows" on job change ([Clay community](https://community.clay.com/x/support/o0p6apy3q4pc/creating-a-table-to-track-user-intent-signals-on-l)) |
| Apollo | Organization, person, funding_events, job postings, news | Org: `id`, `primary_domain`, `linkedin_url`, `linkedin_uid`. Person: email / `linkedin_url` ([org](https://docs.apollo.io/reference/organization-enrichment), [people](https://docs.apollo.io/reference/people-enrichment)) | Dated `funding_events[]`; `employment_history[]` with start/end dates |
| ZoomInfo | Company, contact, scoops, intent, news | `companyId`, or match on `companyWebsite` / `companyName` ([Scoops](https://docs.gtm.ai/reference/enrichinterface_enrichscoop.md)) | Scoops: `publishedDate`, `originalPublishedDate`, `types`, `topics`. Intent: `signalDate` and score |
| Common Room | Organization, contact, activity, signal | Organization = real-world company; merges several domains and CRM accounts into one. Conservative automatic person merge, with proposed merges for review ([orgs](https://commonroom.io/docs/using-common-room/organizations-page), [merge](https://new.commonroom.io/announcements/easily-merge-members-to-create-a-source-of-truth)) | Activities appended to a member timeline ([API](https://commonroom.io/docs/signals/custom-integrations/zapier-api)) |
| Crunchbase | Organization, person, funding_round, event, event_appearance | `uuid` or `permalink` ([entity lookup](https://data.crunchbase.com/docs/using-entity-lookup-apis)) | Rounds and appearances are their own dated entities linked by identifier |
| BuiltWith | Domain → technologies | Root domain; a subdomain lookup returns the root ([Free API](https://api.builtwith.com/free-api)) | First and last detected dates per tech group |
| HG Insights | Company (HG ID), install | HG ID, matched from name, URL and country ([API overview](https://data-docs.hginsights.com/v1/openapi/section/overview-of-the-hg-insights-api)) | Install row with First/Last Verified dates and intensity |
| CRMs (sink) | Account/Company | HubSpot dedupes companies by primary domain, but not for companies created through the API ([HubSpot KB](https://knowledge.hubspot.com/records/deduplication-of-records)). Salesforce upserts on an external ID field ([SF REST upsert](https://developer.salesforce.com/docs/atlas.en-us.248.0.api_rest.meta/api/dome_upsert.htm)) | n/a |

The PRD's keys (FR5: registrable domain, then LinkedIn URL, else name + company) match industry practice. *Inference:* no change is needed.

## Recommended PRD changes

- **Add Q9, "This week's worklist".** List companies with any signal first seen in the last 7 days (parameter N), ranked by score, with tier, signal types and source URLs. This is the main way signals are consumed (Clay rows, Common Room alerts, above).
- **Add Q10, "Event playbook".** For each event starting in the next 60 days or ended in the last 14, list companies by role and speakers by company, with each company's other signals from the last 90 days. This merges Q1 and Q4 and adds the post-event window (IFDA timeline).
- **Add Q11, "CRM export delta".** List companies and people changed since `crm_synced_at` (or never synced), with `crm_id`. Sync itself stays a non-goal, but the data must make it a single query.
- **Change Q2** to "using tool T **and** open roles increased in the last 90 days", not just "hiring". A rise in roles is the trigger. Openings alone are a state.
- **Change Q5** to cover adoption **and** removal of tool T since date D. Removal is the competitive-displacement case (HG and BuiltWith both keep a last-seen date).
- **Change Q3** to return the round as well as the amount, and add a 90-day default window.
- **New FR8.** Every signal has a type, an occurred-on date (when it happened; null if unknown), a first-seen time (when we saw it) and a source URL. Freshness windows are a documented table: funding and events 90 days, hiring 90 days, tech last seen within 24 months.
- **New FR9.** The owner can set a company's tier (1–3, or excluded), and every ranked query respects it (ABM tiers above).

## Recommended design changes

These apply to whichever variant wins. Column names follow variant C.

- **`signals` view** (not a table). It is a `union all` over facts the variants already store, so it adds no second write:

  | column | meaning |
  |---|---|
  | `company_id`, `person_id`, `event_id` | subject and optional context |
  | `type` | `tech_added`, `tech_removed`, `hiring_up`, `funding`, `earnings`, `press`, `event_sponsor`/`exhibitor`/`speaker`/`organizer` |
  | `occurred_on date` | announcement or event date (null if unknown) |
  | `seen_at timestamptz` | first scrape that saw it |
  | `detail text`, `amount_usd bigint` | headline, "2 → 9", tool name |
  | `source_url`, `scrape_id` | provenance (FR6) |

  *Inference:* materialize it as a table only when signals need per-row state, such as "dismissed".
- **`signal_types(type text pk, weight smallint, window_days int)`**: one small table the owner edits in the table editor, for example funding 30/90, hiring_up 20/90, tech_added 15/180, event_sponsor 10/90.
- **`company_scores` view** = `sum(weight)` over signals where `coalesce(occurred_on, seen_at::date) > now() - window_days`, joined to `companies.tier`. The score is computed, never stored. This is a step-function decay, which matches the "date condition instead of negative points" practice above.
- **`companies`**: add `tier smallint` (null = unreviewed, 0 = excluded), `crm_id text`, `crm_synced_at timestamptz` and `updated_at timestamptz` (set on any write).
- **`people`**: add `crm_id text`, `crm_synced_at timestamptz` and `updated_at timestamptz`.
- **Tech history**: the design must yield `first_seen_at` / `last_seen_at` per (company, tool). A and B have this. C derives it from `scrapes.tech[]` through `tech_seen`; check that the view returns `last_seen` so `tech_removed` works.
- **Event dates**: `starts_on` and `ends_on` must be typed dates, because Q10 depends on them (variant C has only `event_date`, so add `ends_on`).
- **Role enum**: add `contestant`, and keep `sponsor` / `exhibitor` / `organizer` / `speaker`, to align with Crunchbase `appearance_type`.
- **History retention**: keep every scrape row; at thousands of companies and a few scrapes per month it is small. *Inference:* queries never need more than 24 months, so no pruning job is needed in v1.

## Later or not recommended

- **Later:** a `signal_actions` table (dismiss/snooze); Slack or email digest of Q9; CSV or ad-audience export; a CRM sync job (Salesforce upsert on `companies.id` as external ID, HubSpot lookup by domain first, because API-created companies are not deduped); `company_domains` aliases for multi-domain companies (Common Room merge); person job-change tracking (Apollo `employment_history` pattern); signal confidence; a separate fit score.
- **Not recommended for v1:** third-party intent scores (needs paid data, which is a non-goal); exponential decay or ML scoring (the step windows are enough for one owner); storing a computed score column (it goes stale, so compute it in a view); vendor IDs (Crunchbase uuid, HG ID) until a provider is actually added.
