-- Bot event log (durable remote backup of key bot/watchdog events).
-- Apply once on Supabase project pcieshmxxwplaodkrqet.

create table if not exists public.bot_events (
  id bigserial primary key,
  ts timestamptz not null default now(),
  level text not null,              -- INFO / WARNING / ERROR
  kind text not null,               -- start, preopen, open, lock, close, pine_exit_sync, timing, slow_cycle, timeout, error, restart
  trade_id uuid null,
  ticket bigint null,
  msg text,
  data jsonb
);

create index if not exists bot_events_ts on public.bot_events (ts desc);
create index if not exists bot_events_kind_ts on public.bot_events (kind, ts desc);

alter table public.bot_events enable row level security;
