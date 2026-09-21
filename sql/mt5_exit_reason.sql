-- Bot-owned close reason. Pine keeps writing exit_reason.

alter table public.gold_trades
  add column if not exists mt5_exit_reason text;
