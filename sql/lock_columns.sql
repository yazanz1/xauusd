-- Lock state for bot-side 50/30 (same as Pine journal).
-- none → pend (50% hit on closed M5) → locked (SL moved at start of next M5).

alter table public.gold_trades
  add column if not exists lock_state text not null default 'none',
  add column if not exists lock_sl numeric,
  add column if not exists lock_pend_bar_time timestamptz,
  add column if not exists orig_stop numeric;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'gold_trades_lock_state_check'
  ) then
    alter table public.gold_trades
      add constraint gold_trades_lock_state_check
      check (lock_state = any (array['none'::text, 'pend'::text, 'locked'::text]));
  end if;
end $$;
