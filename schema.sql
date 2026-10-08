create table if not exists scraped_pages (
  id bigint generated always as identity primary key,
  url text not null,
  title text,
  links jsonb not null default '[]',
  created_at timestamptz not null default now()
);
