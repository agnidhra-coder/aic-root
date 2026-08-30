create extension if not exists pgcrypto;

create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  password_hash text not null,
  name text not null,
  -- The user's tenant in the Python KPI engine, created lazily on first upload.
  -- One company per user; never reused across users.
  company_slug text unique,
  created_at timestamptz not null default now()
);

alter table users add column if not exists company_slug text unique;

create table if not exists uploads (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  domain text not null check (domain in ('retail', 'supply-chain')),
  filename text not null,
  storage_path text not null,
  size_bytes bigint not null,
  context_filename text,
  context_storage_path text,
  row_count integer,
  -- pending -> planning -> awaiting_plan -> confirming -> awaiting_question
  -- -> analyzing -> ready | failed. The middle states are the KPI-plan
  -- handshake with the Python engine.
  status text not null default 'pending' check (status in (
    'pending', 'planning', 'awaiting_plan', 'confirming',
    'awaiting_question', 'analyzing', 'ready', 'failed'
  )),
  stage text check (stage in ('detect', 'decompose', 'explain', 'act')),
  -- The Python draft plan's id, needed to confirm it, and the full KpiPlan the
  -- engine proposed, which is what the selection UI renders.
  plan_id text,
  kpi_plan jsonb,
  error_message text,
  created_at timestamptz not null default now()
);

create index if not exists uploads_user_id_idx on uploads(user_id);

alter table uploads add column if not exists context_filename text;
alter table uploads add column if not exists context_storage_path text;
alter table uploads add column if not exists plan_id text;
alter table uploads add column if not exists kpi_plan jsonb;
alter table uploads add column if not exists error_message text;

-- Widen the status check for the new handshake states on an existing table.
alter table uploads drop constraint if exists uploads_status_check;
alter table uploads add constraint uploads_status_check check (status in (
  'pending', 'planning', 'awaiting_plan', 'confirming',
  'awaiting_question', 'analyzing', 'ready', 'failed'
));

create table if not exists analyses (
  id uuid primary key default gen_random_uuid(),
  upload_id uuid not null unique references uploads(id) on delete cascade,
  result jsonb not null,
  created_at timestamptz not null default now()
);
