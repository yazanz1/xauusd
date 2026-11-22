-- Bot-only lock columns for wick_strategy (+60).
-- Webhook continues to write lock_state / lock_sl / stop as journal.
-- manage_lock reads/writes only bot_lock_*.

alter table public.wick_strategy
  add column if not exists bot_lock_state text not null default 'none',
  add column if not exists bot_lock_sl numeric,
  add column if not exists bot_lock_pend_bar_time timestamptz;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'wick_strategy_bot_lock_state_check'
  ) then
    alter table public.wick_strategy
      add constraint wick_strategy_bot_lock_state_check
      check (bot_lock_state = any (array['none'::text, 'pend'::text, 'locked'::text]));
  end if;
end $$;
