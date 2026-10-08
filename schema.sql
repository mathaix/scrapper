create table if not exists scraped_pages (
  id bigint generated always as identity primary key,
  url text not null,
  title text,
  links jsonb not null default '[]',
  created_at timestamptz not null default now()
);

-- No policies: only the service_role key (server-side) can read/write.
alter table scraped_pages enable row level security;
