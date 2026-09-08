-- Run once on Supabase (SQL editor). Bridge/bot execution columns.
-- Keeps journal analytics (entry/exit/pips/usd_0_3) comparable to MT5 fills.

alter table public.gold_trades
  add column if not exists mt5_fill_price numeric,
  add column if not exists mt5_close_price numeric,
  add column if not exists mt5_closed_at timestamptz,
  add column if not exists mt5_profit numeric;

create index if not exists gold_trades_pending_idx
  on public.gold_trades (status, copied_at)
  where copied_at is null and mt5_ticket is null;
