create extension if not exists pgcrypto;

create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  password_hash text not null,
  name text not null,
  created_at timestamptz not null default now()
);

create table if not exists uploads (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  domain text not null check (domain in ('retail', 'supply-chain')),
  filename text not null,
  storage_path text not null,
  size_bytes bigint not null,
  row_count integer,
  status text not null default 'pending' check (status in ('pending', 'analyzing', 'ready', 'failed')),
  stage text check (stage in ('detect', 'decompose', 'explain', 'act')),
  created_at timestamptz not null default now()
);

create index if not exists uploads_user_id_idx on uploads(user_id);

create table if not exists analyses (
  id uuid primary key default gen_random_uuid(),
  upload_id uuid not null unique references uploads(id) on delete cascade,
  result jsonb not null,
  created_at timestamptz not null default now()
);
