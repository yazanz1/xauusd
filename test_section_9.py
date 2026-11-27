"""Section 9.4 checks (unit / structural — no live MT5 required).

1) Close near SL (10029): frozen_retry, callers do not write mt5_error.
2) Restart does not truncate logs/bot.log (TimedRotatingFileHandler append).
3) Supabase hang → WARNING + cycle continues (fetch failures swallowed in run_cycle).
"""

from __future__ import annotations

import logging
import queue
import tempfile
import time
import unittest
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from unittest.mock import MagicMock, patch

import bot_events
from logging_setup import setup_logging
from mt5_client import MT5Client, TradeResult, call_timed


class Test10029FrozenRetry(unittest.TestCase):
    def test_safe_close_maps_10029_to_frozen_retry(self) -> None:
        mt5 = MT5Client.__new__(MT5Client)
        pos = MagicMock()
        failed = TradeResult(
            ok=False,
            ticket=42,
            retcode=10029,
            comment="Trade is disabled",
            error="retcode=10029",
        )
        with (
            patch.object(MT5Client, "get_position", return_value=pos),
            patch.object(MT5Client, "close_position", return_value=failed),
        ):
            result = MT5Client.safe_close_position(mt5, 42)
        self.assertEqual(result.error, "frozen_retry")
        self.assertFalse(result.ok)
        self.assertEqual(result.retcode, 10029)

    def test_pine_exit_sync_skips_mt5_error_on_frozen(self) -> None:
        """sync_trade must not PATCH mt5_error when safe_close returns frozen_retry."""
        import bot

        trade = {
            "id": "11111111-1111-1111-1111-111111111111",
            "mt5_ticket": 99,
            "status": "closed",
            "updated_at": "2000-01-01T00:00:00+00:00",  # long ago → pine sync fires
            "copied_at": "2000-01-01T00:00:00+00:00",
            "exit": 1.0,
            "exit_reason": "sl",
            "bot_lock_state": "none",
        }
        pos = MagicMock()
        pos.profit = -1.0
        pos.swap = 0.0
        pos.sl = 1.0
        pos.tp = 2.0

        mt5 = MagicMock()
        mt5.get_position.return_value = pos
        mt5.tick.return_value = MagicMock(bid=1.0, ask=1.0)
        mt5.safe_close_position.return_value = TradeResult(
            ok=False, ticket=99, retcode=10029, error="frozen_retry"
        )

        db = MagicMock()
        updates: list[dict] = []

        def capture_update(_db, _tid, payload):
            updates.append(payload)

        with (
            patch.object(bot, "env_bool", return_value=False),
            patch.object(bot, "env_float", side_effect=lambda n, d: d),
            patch.object(bot, "update_trade", side_effect=capture_update),
            patch.object(bot, "bot_events") as ev,
        ):
            ev.emit = MagicMock()
            bot.sync_trade(db, mt5, trade)

        self.assertTrue(updates, "expected floating profit update")
        for payload in updates:
            self.assertNotIn("mt5_error", payload)


class TestLogSurvivesRestart(unittest.TestCase):
    def test_setup_logging_appends_timed_rotating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "logs").mkdir()
            log_path = setup_logging(root)
            self.assertEqual(log_path, root / "logs" / "bot.log")

            marker = "MARKER_BEFORE_RESTART"
            logging.getLogger().info(marker)

            # Simulate second start (watchdog restart): handlers reconfigured, file kept.
            setup_logging(root)
            logging.getLogger().info("MARKER_AFTER_RESTART")

            root_logger = logging.getLogger()
            file_handlers = [
                h for h in root_logger.handlers if isinstance(h, TimedRotatingFileHandler)
            ]
            self.assertEqual(len(file_handlers), 1)
            self.assertEqual(file_handlers[0].backupCount, 14)
            for h in list(root_logger.handlers):
                h.close()
                root_logger.removeHandler(h)

            text = log_path.read_text(encoding="utf-8")
            self.assertIn(marker, text)
            self.assertIn("MARKER_AFTER_RESTART", text)


class TestSupabaseTimeoutContinues(unittest.TestCase):
    def test_run_cycle_continues_when_fetch_times_out(self) -> None:
        import bot

        mt5 = MagicMock()
        mt5.ensure_connected.return_value = True
        db = MagicMock()
        last_step = ["start"]

        with (
            patch.object(bot, "sync_gann_levels"),
            patch.object(bot, "sync_daily_statics"),
            patch.object(
                bot,
                "fetch_new_signals",
                side_effect=TimeoutError("supabase timed out"),
            ),
            patch.object(bot, "fetch_open_copied", return_value=[]),
            patch.object(bot, "check_orphans"),
            patch.object(bot, "env_bool", return_value=False),
            self.assertLogs("xaubot", level="ERROR") as cm,
        ):
            bot.run_cycle(db, mt5, last_step=last_step)

        self.assertEqual(last_step[0], "done")
        self.assertTrue(any("fetch_new_signals" in line for line in cm.output))

    def test_sb_call_emits_timeout_warning(self) -> None:
        import bot

        class TimeoutException(Exception):
            pass

        with (
            patch.object(bot, "bot_events") as ev,
            self.assertLogs("xaubot", level="WARNING") as cm,
        ):
            ev.emit = MagicMock()
            with self.assertRaises(TimeoutException):
                bot._sb_call("probe", lambda: (_ for _ in ()).throw(TimeoutException("timed out")))
            ev.emit.assert_called()
            self.assertEqual(ev.emit.call_args.args[1], "timeout")
            self.assertTrue(any("Supabase timeout" in line for line in cm.output))


class TestBotEventsNonBlocking(unittest.TestCase):
    def test_emit_put_nowait_drops_when_full(self) -> None:
        bot_events._q = queue.Queue(maxsize=1)
        bot_events._q.put_nowait({"kind": "filler"})
        with self.assertLogs("xaubot.events", level="WARNING") as cm:
            bot_events.emit("INFO", "open", "should drop")
        self.assertTrue(any("queue full" in line for line in cm.output))
        # Drain filler so other tests are not affected.
        try:
            bot_events._q.get_nowait()
        except queue.Empty:
            pass


class TestMt5NoThreadWrapper(unittest.TestCase):
    def test_call_timed_runs_inline_and_warns_when_slow(self) -> None:
        with (
            patch("mt5_client._mt5_slow_warn_sec", return_value=0.01),
            self.assertLogs("xaubot.mt5", level="WARNING") as cm,
        ):

            def slow():
                time.sleep(0.05)
                return 7

            self.assertEqual(call_timed("probe", slow), 7)
        self.assertTrue(any("MT5 slow op=probe" in line for line in cm.output))

    def test_no_threadpool_in_mt5_module(self) -> None:
        import mt5_client as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("ThreadPoolExecutor", src)
        self.assertNotIn("call_with_timeout", src)
        self.assertIn("call_timed", src)


if __name__ == "__main__":
    unittest.main()
