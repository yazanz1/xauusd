"""Async emit of key bot events to public.bot_events (non-blocking).

Main poll never waits on Supabase for event rows. Failures stay in the file log only.
"""

from __future__ import annotations

import atexit
import logging
import os
import queue
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("xaubot.events")

_TABLE = "bot_events"
_EMIT_TIMEOUT_SEC = 5.0
_QUEUE_MAX = 500

_db: Any = None  # main client (purge)
_events_db: Any = None  # short-timeout client for inserts
_q: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=_QUEUE_MAX)
_worker: threading.Thread | None = None
_started = False
_lock = threading.Lock()


def _make_events_client() -> Any | None:
    """Dedicated PostgREST client with 5s timeout for background inserts."""
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        return None
    try:
        from supabase import ClientOptions, create_client

        return create_client(
            url,
            key,
            options=ClientOptions(postgrest_client_timeout=_EMIT_TIMEOUT_SEC),
        )
    except Exception:
        log.debug("bot_events short-timeout client failed", exc_info=True)
        return None


def _worker_loop() -> None:
    while True:
        item = _q.get()
        try:
            if item is None:
                return
            db = _events_db or _db
            if db is None:
                continue
            try:
                # Runs only in this worker thread; main poll already returned from emit().
                db.table(_TABLE).insert(item).execute()
            except Exception:
                log.debug(
                    "bot_events insert failed/timed out kind=%s",
                    item.get("kind"),
                    exc_info=True,
                )
        finally:
            _q.task_done()


def start(db: Any) -> None:
    """Bind clients and start the background writer (idempotent)."""
    global _db, _events_db, _worker, _started
    with _lock:
        _db = db
        _events_db = _make_events_client() or db
        if _started:
            return
        _worker = threading.Thread(target=_worker_loop, name="bot_events", daemon=True)
        _worker.start()
        _started = True
        atexit.register(stop)


def stop() -> None:
    """Flush signal to stop the worker (best-effort)."""
    global _started
    with _lock:
        if not _started:
            return
        try:
            _q.put_nowait(None)
        except queue.Full:
            pass
        _started = False


def emit(
    level: str,
    kind: str,
    msg: str | None = None,
    *,
    trade_id: str | None = None,
    ticket: int | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Enqueue one event. Never blocks the caller (put_nowait; drop if full)."""
    row: dict[str, Any] = {
        "level": (level or "INFO").upper(),
        "kind": kind,
        "msg": msg,
        "trade_id": trade_id,
        "ticket": int(ticket) if ticket is not None else None,
        "data": data,
    }
    try:
        _q.put_nowait(row)
    except queue.Full:
        log.warning("bot_events queue full — dropped kind=%s", kind)


def purge_older_than(days: int = 30) -> None:
    """Delete rows older than `days` (best-effort, sync, once at startup)."""
    db = _db
    if db is None:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        db.table(_TABLE).delete().lt("ts", cutoff).execute()
        log.info("bot_events purged rows older than %sd", days)
    except Exception:
        log.debug("bot_events purge failed", exc_info=True)
