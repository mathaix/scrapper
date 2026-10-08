"""Schema v2 smoke test: applies schema.sql twice, loads the design seed, runs Q1-Q8 (+Q5b).

Needs a Postgres >= 15 reachable through TEST_DATABASE_URL; skipped otherwise. Everything runs inside a
throwaway `signals_test` schema, so pointing it at a real database never touches the real tables.
"""
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")

SCHEMA = (Path(__file__).resolve().parent.parent / "schema.sql").read_text()

TEST_SCHEMA = "signals_test"
RESET = f"drop schema if exists {TEST_SCHEMA} cascade; create schema {TEST_SCHEMA};"

SEED = """
insert into companies (id, domain, name, tech_ignore) overriding system value values
  (1, 'acme.com', 'Acme', '{jquery}'),
  (2, 'globex.com', 'Globex', '{}'),
  (3, 'initech.com', 'Initech', '{}');
insert into companies (id, domain, name, merged_into) overriding system value values
  (4, 'initech-dupe.com', 'Initech', 3);

-- Acme: open_roles 2 -> null (ATS lookup failed) -> 9; jquery is misdetected.
insert into scrapes (id, url, kind, status, company_id, scraped_at, name, tech, ats, open_roles) overriding system value values
  (1, 'https://acme.com', 'company', 'ok', 1, now() - interval '60 days', 'Acme', '{wordpress,jquery}', 'greenhouse', 2);
insert into scrapes (id, url, kind, status, company_id, scraped_at, name, tech, ats, open_roles) overriding system value values
  (2, 'https://acme.com', 'company', 'ok', 1, now() - interval '30 days', 'Acme', null, null, null);
insert into scrapes (id, url, kind, status, company_id, scraped_at, name, tech, ats, open_roles) overriding system value values
  (3, 'https://acme.com', 'company', 'ok', 1, now() - interval '1 day', 'Acme Inc', '{hubspot,jquery}', 'greenhouse', 9);
-- Globex: already on HubSpot at first look, no openings.
insert into scrapes (id, url, kind, status, company_id, scraped_at, name, tech, ats, open_roles) overriding system value values
  (4, 'https://globex.com', 'company', 'ok', 2, now() - interval '20 days', 'Globex', '{hubspot}', 'lever', 0);

-- Event X, scraped twice; Initech was dropped from the second scrape.
insert into events (id, event_key, kind, title, url, event_date) overriding system value values
  (1, 'conf.example/x', 'event', 'Conf X', 'https://conf.example/x', current_date + 20);
insert into scrapes (id, url, final_url, kind, status, event_id, scraped_at, title) overriding system value values
  (5, 'https://conf.example/x?utm_source=a', 'https://conf.example/x', 'event', 'ok', 1, now() - interval '10 days', 'Conf X'),
  (6, 'https://conf.example/x', 'https://conf.example/x', 'event', 'ok', 1, now() - interval '2 days', 'Conf X');
insert into people (id, person_key, name, job_title, company_id) overriding system value values
  (1, 'nm:alicesmith@initech.com', 'Alice Smith', 'CTO', 3);
insert into appearances (event_id, company_id, role, evidence, last_scrape_id, confirmed) values
  (1, 1, 'sponsor', 'Gold Sponsors', 6, null),
  (1, 3, 'sponsor', 'Silver Sponsors', 5, null),
  (1, 2, 'exhibitor', 'Exhibitors', 6, false);
insert into appearances (event_id, person_id, role, job_title, company_name, person_company_id, last_scrape_id) values
  (1, 1, 'speaker', 'CTO', 'Initech', 3, 6);

-- Funding: Acme dated Series B, Acme undated bridge (new after earlier scrapes), Globex old undated seed
-- found on its very first scrape.
insert into events (id, event_key, kind, title, event_date, round, amount_usd, first_seen_at) overriding system value values
  (2, 'funding:acme.com:series b', 'funding', 'Acme raises Series B', current_date - 20, 'series b', 20000000, now() - interval '20 days'),
  (3, 'funding:acme.com:bridge', 'funding', 'Acme bridge round', null, 'bridge', null, now() - interval '5 days'),
  (4, 'funding:globex.com:seed', 'funding', 'Globex seed', null, 'seed', 1000000,
       (select scraped_at from scrapes where id = 4));
insert into appearances (event_id, company_id, role, last_scrape_id) values
  (2, 1, 'subject', 3), (3, 1, 'subject', 3), (4, 2, 'subject', 4);

-- Batch with 4 children (one queued, one failed).
insert into scrapes (id, batch_id, url, kind, status, event_id, scraped_at) overriding system value values
  (10, '00000000-0000-0000-0000-000000000001', 'https://conf.example/y', 'event', 'ok', null, now());
insert into scrapes (id, batch_id, parent_id, url, status, error) overriding system value values
  (11, '00000000-0000-0000-0000-000000000001', 10, 'https://c1.example', 'ok', null),
  (12, '00000000-0000-0000-0000-000000000001', 10, 'https://c2.example', 'ok', null),
  (13, '00000000-0000-0000-0000-000000000001', 10, 'https://c3.example', 'queued', null),
  (14, '00000000-0000-0000-0000-000000000001', 10, 'https://c4.example', 'error', 'boom');
"""

Q1 = """
select c.domain, a.role, c.tech, c.open_roles,
       a.last_scrape_id >= (select max(id) from scrapes where event_id = e.id and status = 'ok') as still_listed
from events e
join appearances a on a.event_id = e.id and a.confirmed is not false
join companies_current c on c.id = a.company_id
where e.id in (select event_id from scrapes where status = 'ok' and %(url)s in (url, final_url)
               union select id from events where event_key = %(url)s)
  and a.role in ('sponsor', 'exhibitor')
order by still_listed desc, a.role, c.name
"""

Q2 = """
select domain, open_roles from companies_current
where 'hubspot' = any(tech) and open_roles > 0 order by open_roles desc
"""

Q3 = """
select c.domain, e.round, e.event_date is null as undated
from events e
join appearances a on a.event_id = e.id and a.role = 'subject' and a.confirmed is not false
join companies_current c on c.id = a.company_id
where e.kind = 'funding'
  and (e.event_date between current_date - 90 and current_date
       or (e.event_date is null and e.first_seen_at >= current_date - 90
           and exists (select 1 from scrapes p where p.company_id = c.id and p.status = 'ok'
                         and p.scraped_at < e.first_seen_at)))
order by coalesce(e.event_date, e.first_seen_at::date) desc
"""

Q4 = """
select p.name, coalesce(a.job_title, p.job_title) as job_title,
       coalesce(c.name, a.company_name, p.company_name) as company, c.domain,
       a.last_scrape_id >= (select max(id) from scrapes where event_id = e.id and status = 'ok') as still_listed
from events e
join appearances a on a.event_id = e.id and a.role = 'speaker' and a.confirmed is not false
join people p on p.id = a.person_id
left join companies_current c on c.id = coalesce(a.person_company_id, p.company_id)
where e.kind in ('event', 'webinar') and e.event_date between current_date and current_date + 60
order by e.event_date, p.name
"""

Q5 = """
select c.domain, t.adopted
from tech_seen t join companies_current c on c.id = t.company_id
where t.tool = 'hubspot' and t.first_seen_at >= now() - interval '40 days'
order by t.first_seen_at
"""

Q5B = """
select c.domain
from company_history h join companies c on c.id = h.company_id
where 'hubspot' = any(h.tech_added) and h.scraped_at >= now() - interval '40 days'
order by h.scraped_at
"""

Q6 = """
select e.title, a.role
from companies c
join appearances a on a.company_id = c.id and a.confirmed is not false
join events e on e.id = a.event_id
where c.domain = 'acme.com'
order by e.event_date desc nulls last
"""

Q7_CURRENT = "select name, tech, ats, open_roles from companies_current where domain = 'acme.com'"
Q7_HISTORY = """
select h.prev_name, h.name, h.prev_open_roles, h.open_roles, h.tech_added, h.tech_removed
from company_history h join companies c on c.id = h.company_id
where c.domain = 'acme.com'
order by h.scraped_at, h.scrape_id
"""

Q8 = """
select s.id, s.status, coalesce(c.domain, e.title) as result,
       (select count(*) from scrapes k where k.parent_id = s.id) as children,
       (select count(*) from scrapes k where k.parent_id = s.id and k.status = 'queued') as children_queued,
       (select count(*) from scrapes k where k.parent_id = s.id and k.status = 'error') as children_error
from scrapes s
left join companies c on c.id = s.company_id
left join events e on e.id = s.event_id
where s.batch_id = '00000000-0000-0000-0000-000000000001' and s.parent_id is null
order by s.id
"""


@pytest.fixture
def conn():
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, options=f"-c search_path={TEST_SCHEMA}") as c:
        c.execute(RESET)
        yield c
        c.execute(f"drop schema if exists {TEST_SCHEMA} cascade")


@pytest.fixture
def seeded(conn):
    conn.execute(SCHEMA)
    conn.execute(SCHEMA)  # idempotent: second apply must be clean
    conn.execute(SEED)
    return conn


def rows(conn, sql, **params):
    return conn.execute(sql, params).fetchall()


def test_schema_applies_twice_and_drops_scraped_pages(conn):
    conn.execute("create table scraped_pages (id int)")
    conn.execute(SCHEMA)
    conn.execute(SCHEMA)
    tables = {r[0] for r in conn.execute(
        "select table_name from information_schema.tables where table_schema = current_schema()")}
    assert {"companies", "people", "events", "appearances", "scrapes",
            "companies_current", "company_history", "tech_seen"} <= tables
    assert "scraped_pages" not in tables
    cols = {r[0] for r in conn.execute(
        "select column_name from information_schema.columns where table_name = 'scrapes'")}
    assert "raw_path" in cols


def test_no_json_columns(seeded):
    assert rows(seeded, """select table_name, column_name from information_schema.columns
        where table_schema = current_schema() and data_type in ('json', 'jsonb')""") == []


def test_q1_sponsors_of_event(seeded):
    expected = [("acme.com", "sponsor", ["hubspot"], 9, True),
                ("initech.com", "sponsor", None, None, False)]
    assert rows(seeded, Q1, url="https://conf.example/x") == expected
    # also found by a URL that redirected, and by event key
    assert rows(seeded, Q1, url="https://conf.example/x?utm_source=a") == expected
    assert rows(seeded, Q1, url="conf.example/x") == expected


def test_q2_hubspot_hiring(seeded):
    assert rows(seeded, Q2) == [("acme.com", 9)]


def test_q3_recent_funding(seeded):
    assert rows(seeded, Q3) == [("acme.com", "bridge", True), ("acme.com", "series b", False)]


def test_q4_speakers(seeded):
    assert rows(seeded, Q4) == [("Alice Smith", "CTO", "Initech", "initech.com", True)]


def test_q5_adopted_since(seeded):
    assert rows(seeded, Q5) == [("globex.com", False), ("acme.com", True)]


def test_q5b_adoption_events(seeded):
    assert rows(seeded, Q5B) == [("acme.com",)]


def test_q6_company_appearances(seeded):
    assert rows(seeded, Q6) == [("Conf X", "sponsor"), ("Acme raises Series B", "subject"),
                                ("Acme bridge round", "subject")]


def test_q7_current_and_history(seeded):
    # tech_ignore hides jquery; the failed middle scrape does not blank hiring
    assert rows(seeded, Q7_CURRENT) == [("Acme Inc", ["hubspot"], "greenhouse", 9)]
    assert rows(seeded, Q7_HISTORY) == [
        (None, "Acme", None, 2, None, None),
        ("Acme", "Acme", 2, None, None, None),
        ("Acme", "Acme Inc", 2, 9, ["hubspot"], ["wordpress"]),
    ]


def test_q8_batch_status(seeded):
    assert rows(seeded, Q8) == [(10, "ok", None, 4, 1, 1)]


def test_merged_company_hidden_and_restrict(seeded):
    domains = {r[0] for r in rows(seeded, "select domain from companies_current")}
    assert "initech-dupe.com" not in domains
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        seeded.execute("delete from companies where id = 1")


def test_appearance_upsert_is_idempotent(seeded):
    before = rows(seeded, "select count(*) from appearances")
    seeded.execute("""insert into appearances (event_id, company_id, role, last_scrape_id)
        values (1, 1, 'sponsor', 6)
        on conflict (event_id, company_id, person_id, role) do update set last_seen_at = now()""")
    assert rows(seeded, "select count(*) from appearances") == before


def test_tech_arrays_keep_scan_order(seeded):
    seeded.execute("""insert into companies (id, domain, tech_ignore) overriding system value values (5, 'order.com', '{mid}');
        insert into scrapes (id, url, kind, status, company_id, scraped_at, tech) overriding system value values
          (40, 'https://order.com', 'company', 'ok', 5, now() - interval '2 days', '{zeta,mid,alpha}'),
          (41, 'https://order.com', 'company', 'ok', 5, now(), '{zeta,beta,alpha,gamma}')""")
    assert rows(seeded, "select tech from companies_current where id = 5") == [(["zeta", "beta", "alpha", "gamma"],)]
    assert rows(seeded, "select tech_added from company_history where company_id = 5 and tech_added is not null") == \
        [(["beta", "gamma"],)]


def test_adopted_breaks_timestamp_ties_by_id(seeded):
    seeded.execute("""insert into companies (id, domain) overriding system value values (5, 'tie.com');
        insert into scrapes (id, url, kind, status, company_id, scraped_at, tech) overriding system value values
          (20, 'https://tie.com', 'company', 'ok', 5, '2026-01-01', '{wordpress}'),
          (21, 'https://tie.com', 'company', 'ok', 5, '2026-01-01', '{wordpress,hubspot}')""")
    assert rows(seeded, "select tool, adopted from tech_seen where company_id = 5 order by tool") == \
        [("hubspot", True), ("wordpress", False)]
    assert rows(seeded, "select tech_added from company_history where scrape_id = 21") == [(["hubspot"],)]


def test_merge_company_moves_rows_to_kept_company(seeded):
    seeded.execute("""insert into companies (id, domain, name) overriding system value values (5, 'initech.io', 'Initech');
        insert into scrapes (id, url, kind, status, company_id, scraped_at, tech) overriding system value values
          (30, 'https://initech.io', 'company', 'ok', 5, now(), '{salesforce}');
        insert into appearances (event_id, company_id, role, evidence) values
          (1, 5, 'sponsor', 'duplicate of the initech.com row'),
          (1, 5, 'exhibitor', 'only on the duplicate');
        select merge_company(5, 3)""")
    assert rows(seeded, "select count(*) from appearances where company_id = 5") == [(0,)]
    assert rows(seeded, "select role from appearances where company_id = 3 and event_id = 1 order by role") == \
        [("exhibitor",), ("sponsor",)]
    assert rows(seeded, "select tech from companies_current where id = 3") == [(["salesforce"],)]
    assert rows(seeded, "select merged_into from companies where id = 5") == [(3,)]
    with pytest.raises(psycopg.errors.RaiseException):
        seeded.execute("select merge_company(3, 3)")
