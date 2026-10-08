create table if not exists scraped_pages (
  id bigint generated always as identity primary key,
  url text not null,
  title text,
  links jsonb not null default '[]',
  created_at timestamptz not null default now()
);

-- No policies: only the service_role key (server-side) can read/write.
alter table scraped_pages enable row level security;

-- Marketing signals (idempotent; safe to run repeatedly). One row per scrape = snapshots.
-- status: queued (batch row waiting) | ok | error
alter table scraped_pages add column if not exists domain text;
alter table scraped_pages add column if not exists signals jsonb not null default '{}';
alter table scraped_pages add column if not exists status text not null default 'ok';
alter table scraped_pages add column if not exists error text;
alter table scraped_pages add column if not exists batch_id uuid;
create index if not exists scraped_pages_domain_created_idx on scraped_pages (domain, created_at desc);
create index if not exists scraped_pages_batch_idx on scraped_pages (batch_id);
