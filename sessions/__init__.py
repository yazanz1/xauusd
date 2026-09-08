"""Persistent MT5 session helpers.

Connect once, save session metadata under sessions/, reuse on later runs.
Password stays in `.env` only — never written to the session file.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mt5_client import MT5Client, MT5Error

log = logging.getLogger("xaubot.sessions")

ROOT = Path(__file__).resolve().parent
SESSION_FILE = ROOT / "mt5.json"


@dataclass
class Mt5Session:
    login: int
    server: str
    path: str | None
    connected_at: str
    last_seen_at: str
    balance: float | None = None
    company: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Mt5Session:
        return cls(
            login=int(data["login"]),
            server=str(data["server"]),
            path=data.get("path"),
            connected_at=str(data.get("connected_at") or ""),
            last_seen_at=str(data.get("last_seen_at") or ""),
            balance=data.get("balance"),
            company=data.get("company"),
        )


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_session() -> Mt5Session | None:
    if not SESSION_FILE.is_file():
        return None
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read session file: %s", exc)
        return None
    if not data:
        return None
    try:
        return Mt5Session.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("Invalid session file: %s", exc)
        return None


def save_session(session: Mt5Session) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(
        json.dumps(asdict(session), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def clear_session() -> None:
    if SESSION_FILE.is_file():
        SESSION_FILE.unlink()


class SessionManager:
    """Holds one live MT5 connection and persists session metadata."""

    def __init__(self, *, magic: int = 0) -> None:
        self.magic = magic
        self.client: MT5Client | None = None
        self.session: Mt5Session | None = load_session()

    def open(self) -> MT5Client:
        """Connect using .env (+ saved session path/login/server hints). Saves on success."""
        client = MT5Client.from_env(magic=self.magic)
        saved = self.session
        if saved:
            if not client.path and saved.path:
                client.path = saved.path
            if client.login is None:
                client.login = saved.login
            if not client.server:
                client.server = saved.server

        client.connect()
        account = client.account()
        now = _utcnow()
        self.session = Mt5Session(
            login=int(account.login),
            server=str(account.server),
            path=client.path,
            connected_at=saved.connected_at if saved and saved.login == int(account.login) else now,
            last_seen_at=now,
            balance=float(account.balance),
            company=getattr(account, "company", None),
        )
        save_session(self.session)
        self.client = client
        log.info(
            "MT5 session open login=%s server=%s balance=%s",
            self.session.login,
            self.session.server,
            self.session.balance,
        )
        return client

    def ensure(self) -> MT5Client:
        """Return live client; reconnect and refresh session file if dropped."""
        if self.client is not None and self.client.is_connected():
            self._touch()
            return self.client
        return self.open()

    def _touch(self) -> None:
        if self.client is None:
            return
        try:
            account = self.client.account()
        except MT5Error:
            return
        now = _utcnow()
        if self.session is None:
            self.session = Mt5Session(
                login=int(account.login),
                server=str(account.server),
                path=self.client.path,
                connected_at=now,
                last_seen_at=now,
                balance=float(account.balance),
                company=getattr(account, "company", None),
            )
        else:
            self.session.last_seen_at = now
            self.session.balance = float(account.balance)
            self.session.server = str(account.server)
        save_session(self.session)

    def close(self) -> None:
        if self.client is not None:
            self.client.disconnect()
            self.client = None
        if self.session is not None:
            self.session.last_seen_at = _utcnow()
            save_session(self.session)

    def keepalive(self, *, interval_sec: float = 30.0) -> None:
        """Keep IPC alive: connect once, ping forever, auto-reconnect on drop."""
        self.open()
        log.info("Session keepalive every %.0fs. Ctrl+C to stop.", interval_sec)
        try:
            while True:
                time.sleep(interval_sec)
                try:
                    self.ensure()
                    account = self.client.account() if self.client else None
                    if account is not None:
                        log.info(
                            "alive login=%s server=%s balance=%s",
                            account.login,
                            account.server,
                            account.balance,
                        )
                except MT5Error as exc:
                    log.error("Session lost: %s — retrying...", exc)
                    time.sleep(3)
                    try:
                        self.open()
                    except MT5Error as retry_exc:
                        log.error("Reconnect failed: %s", retry_exc)
        except KeyboardInterrupt:
            log.info("Session keepalive stopped")
        finally:
            self.close()


def status() -> dict[str, Any]:
    saved = load_session()
    return {
        "session_file": str(SESSION_FILE),
        "exists": saved is not None,
        "session": asdict(saved) if saved else None,
    }
