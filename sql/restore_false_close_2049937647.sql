-- One-shot: restore ticket falsely closed by deal mixup (report 2026-09-18).
-- Run only if the position is still open in MT5. If already closed in MT5,
-- leave status=closed and re-sync with history_deals_get(position=...).

UPDATE public.gold_trades
SET
  status = 'open',
  exit = NULL,
  exit_time = NULL,
  exit_reason = NULL,
  pips = NULL,
  usd_0_3 = NULL,
  mt5_close_price = NULL,
  mt5_closed_at = NULL,
  mt5_profit = NULL,
  stop = COALESCE(orig_stop, stop),
  mt5_error = 'restored after false close (deal mixup); re-sync from MT5'
WHERE mt5_ticket = 2049937647
  AND status = 'closed';
