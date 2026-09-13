-- Daily gold stats (gold_statics). Bot module: statics.py

alter table public.gold_statics
  add column if not exists date_idt date,
  add column if not exists trades_total integer not null default 0,
  add column if not exists trades_closed integer not null default 0,
  add column if not exists trades_open integer not null default 0,
  add column if not exists count_tp integer not null default 0,
  add column if not exists count_sl integer not null default 0,
  add column if not exists count_lock integer not null default 0,
  add column if not exists count_other integer not null default 0,
  add column if not exists profit_usd numeric not null default 0,
  add column if not exists pips_total numeric not null default 0,
  add column if not exists updated_at timestamptz not null default now();

create unique index if not exists gold_statics_date_idt_key
  on public.gold_statics (date_idt);
