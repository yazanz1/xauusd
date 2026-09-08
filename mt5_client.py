"""Local MetaTrader 5 adapter.

Connects using `.env` credentials when present, otherwise attaches to an
already-open logged-in terminal. Exposes market open, SL/TP, trailing, close.
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - only missing before pip install
    mt5 = None

Side = Literal["buy", "sell"]

WM_COMMAND = 0x0111
MT_WMCMD_EXPERTS = 32851
VK_CONTROL = 0x11
VK_E = 0x45
KEYEVENTF_KEYUP = 0x0002


class MT5Error(RuntimeError):
    """Raised when a MetaTrader 5 call fails."""


@dataclass(frozen=True)
class TradeResult:
    ok: bool
    ticket: int | None = None
    deal: int | None = None
    price: float | None = None
    volume: float | None = None
    sl: float | None = None
    tp: float | None = None
    retcode: int | None = None
    comment: str = ""
    error: str = ""

    def raise_if_failed(self) -> TradeResult:
        if not self.ok:
            raise MT5Error(self.error or self.comment or "MT5 trade failed")
        return self


def _require_mt5():
    if mt5 is None:
        raise MT5Error("MetaTrader5 is not installed. Run: pip install -r requirements.txt")
    return mt5


def _env_optional(name: str) -> str | None:
    value = os.getenv(name, "")
    value = str(value).strip().strip('"').strip("'")
    return value or None


def default_terminal_path() -> str | None:
    candidates = [
        os.getenv("MT5_PATH") or "",
        r"C:\Program Files\MetaTrader 5\terminal64.exe",
        r"C:\Program Files\GBE MetaTrader 5 Terminal\terminal64.exe",
        r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
    ]
    for raw in candidates:
        path = raw.strip().strip('"').strip("'")
        if path and Path(path).is_file():
            return str(Path(path))
    return None


class MT5Client:
    def __init__(
        self,
        *,
        path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        magic: int = 0,
        deviation: int = 30,
    ) -> None:
        self.path = path
        self.login = login
        self.password = password
        self.server = server
        self.magic = magic
        self.deviation = deviation

    @classmethod
    def from_env(cls, *, magic: int = 0, deviation: int = 30) -> MT5Client:
        login_raw = _env_optional("MT5_LOGIN")
        login = None
        if login_raw:
            try:
                login = int(login_raw)
            except ValueError as exc:
                raise MT5Error("MT5_LOGIN must be a number") from exc
        return cls(
            path=_env_optional("MT5_PATH") or default_terminal_path(),
            login=login,
            password=_env_optional("MT5_PASSWORD"),
            server=_env_optional("MT5_SERVER"),
            magic=magic,
            deviation=deviation,
        )

    def connect(self) -> None:
        api = _require_mt5()
        timeout = int(os.getenv("MT5_TIMEOUT_MS", "90000"))
        path = self.path or default_terminal_path()

        # Prefer attaching to the already-open terminal session.
        if self._attach(api, path, timeout):
            if self._account_matches(api) or (self.login is None and api.account_info() is not None):
                self.ensure_algo_trading()
                return
            if self._can_login() and api.login(
                self.login,
                password=self.password,
                server=self.server,
                timeout=timeout,
            ):
                if self._account_matches(api):
                    self.ensure_algo_trading()
                    return
            if api.account_info() is not None and self.login is None:
                self.ensure_algo_trading()
                return
            api.shutdown()

        if self._can_login():
            kwargs: dict[str, Any] = {
                "login": self.login,
                "password": self.password,
                "server": self.server,
                "timeout": timeout,
            }
            if path:
                ok = api.initialize(Path(path).as_posix(), **kwargs)
            else:
                ok = api.initialize(**kwargs)
            if ok and self._account_matches(api):
                self.ensure_algo_trading()
                return
            err = api.last_error()
            api.shutdown()
            raise MT5Error(self._connect_error(err, path))

        err = api.last_error()
        raise MT5Error(self._connect_error(err, path))

    def trade_allowed(self) -> bool:
        api = _require_mt5()
        info = api.terminal_info()
        return bool(info and info.trade_allowed)

    def ensure_algo_trading(self, *, retries: int = 3) -> bool:
        """Turn on the MT5 Algo Trading toolbar button if it is off."""
        api = _require_mt5()
        if self.trade_allowed():
            return True

        hwnd = self._find_terminal_hwnd()
        if hwnd is None:
            return self.trade_allowed()

        user32 = ctypes.windll.user32
        for _ in range(max(1, retries)):
            if self.trade_allowed():
                return True
            # Ctrl+E is the reliable toggle on current MT5 builds.
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.2)
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            user32.keybd_event(VK_E, 0, 0, 0)
            user32.keybd_event(VK_E, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.8)
            if self.trade_allowed():
                return True
            # Fallback: toolbar command id used by many MT5 builds.
            user32.PostMessageW(hwnd, WM_COMMAND, MT_WMCMD_EXPERTS, 0)
            time.sleep(0.8)

        return self.trade_allowed()

    def _find_terminal_hwnd(self) -> int | None:
        user32 = ctypes.windll.user32
        found: list[int] = []
        login_text = str(self.login) if self.login is not None else ""
        try:
            account = self.account()
            login_text = str(account.login)
        except Exception:
            pass

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def each(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""
            low = title.lower()
            if "metatrader" in low or "vantage" in low or (login_text and login_text in title):
                found.append(int(hwnd))
            return True

        user32.EnumWindows(EnumWindowsProc(each), 0)
        return found[0] if found else None

    def _can_login(self) -> bool:
        return self.login is not None and bool(self.password) and bool(self.server)

    def _account_matches(self, api) -> bool:
        account = api.account_info()
        if account is None:
            return False
        if self.login is None:
            return True
        if int(account.login) != int(self.login):
            return False
        if self.server and account.server != self.server:
            return False
        return True

    @staticmethod
    def _attach(api, path: str | None, timeout: int) -> bool:
        if path:
            posix = Path(path).as_posix()
            if api.initialize(posix, timeout=timeout):
                return True
        return bool(api.initialize(timeout=timeout))

    @staticmethod
    def _connect_error(err: Any, path: str | None) -> str:
        code = err[0] if err else None
        if code == -10005:
            return (
                f"MT5 initialize failed: {err}. "
                "Open MetaTrader 5, log in to the account once in the GUI, "
                "enable Python (Tools → Options → Community), then retry."
            )
        if code == -6:
            return (
                f"MT5 authorization failed: {err}. "
                "Check MT5_LOGIN / MT5_PASSWORD / MT5_SERVER, and keep only one MT5 terminal open."
            )
        where = f" path={path}" if path else ""
        return f"MT5 connect failed: {err}.{where}"

    def disconnect(self) -> None:
        if mt5 is not None:
            mt5.shutdown()

    def is_connected(self) -> bool:
        if mt5 is None:
            return False
        return mt5.account_info() is not None

    def ensure_connected(self) -> bool:
        if self.is_connected():
            self.ensure_algo_trading()
            return True
        try:
            self.connect()
            return True
        except MT5Error:
            return False

    def account(self):
        api = _require_mt5()
        info = api.account_info()
        if info is None:
            raise MT5Error(f"No account info: {api.last_error()}")
        return info

    def symbol_info(self, symbol: str):
        api = _require_mt5()
        info = api.symbol_info(symbol)
        if info is None or not info.visible:
            if not api.symbol_select(symbol, True):
                raise MT5Error(f"Cannot select symbol {symbol}: {api.last_error()}")
            info = api.symbol_info(symbol)
        if info is None:
            raise MT5Error(f"No symbol info for {symbol}: {api.last_error()}")
        return info

    def tick(self, symbol: str):
        api = _require_mt5()
        self.symbol_info(symbol)
        tick = api.symbol_info_tick(symbol)
        if tick is None:
            raise MT5Error(f"No tick for {symbol}: {api.last_error()}")
        return tick

    def normalize_price(self, price: float, symbol: str | Any) -> float:
        info = symbol if hasattr(symbol, "digits") else self.symbol_info(symbol)
        return round(float(price), info.digits)

    def normalize_volume(self, lots: float, symbol: str | Any) -> float:
        info = symbol if hasattr(symbol, "volume_step") else self.symbol_info(symbol)
        step = info.volume_step or 0.01
        lots = max(info.volume_min, min(info.volume_max, lots))
        steps = round(lots / step)
        return round(steps * step, 8)

    def min_stop_distance(self, symbol: str | Any) -> float:
        info = symbol if hasattr(symbol, "trade_stops_level") else self.symbol_info(symbol)
        return (info.trade_stops_level or 0) * info.point

    def filling_modes(self, symbol: str | Any) -> list[int]:
        api = _require_mt5()
        info = symbol if hasattr(symbol, "filling_mode") else self.symbol_info(symbol)
        modes: list[int] = []
        filling = info.filling_mode
        if filling & 1:
            modes.append(api.ORDER_FILLING_FOK)
        if filling & 2:
            modes.append(api.ORDER_FILLING_IOC)
        modes.append(api.ORDER_FILLING_RETURN)
        seen: set[int] = set()
        unique: list[int] = []
        for mode in modes:
            if mode not in seen:
                seen.add(mode)
                unique.append(mode)
        return unique

    def open_market(
        self,
        symbol: str,
        side: Side,
        volume: float,
        *,
        sl: float | None = None,
        tp: float | None = None,
        comment: str = "",
        magic: int | None = None,
        deviation: int | None = None,
    ) -> TradeResult:
        api = _require_mt5()
        if side not in ("buy", "sell"):
            return TradeResult(ok=False, error=f"invalid side: {side}")

        info = self.symbol_info(symbol)
        tick = self.tick(symbol)
        order_type = api.ORDER_TYPE_BUY if side == "buy" else api.ORDER_TYPE_SELL
        price = tick.ask if side == "buy" else tick.bid
        lots = self.normalize_volume(volume, info)
        sl_price = self.normalize_price(sl, info) if sl is not None else 0.0
        tp_price = self.normalize_price(tp, info) if tp is not None else 0.0

        request = {
            "action": api.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lots,
            "type": order_type,
            "price": self.normalize_price(price, info),
            "sl": sl_price,
            "tp": tp_price,
            "deviation": deviation if deviation is not None else self.deviation,
            "magic": self.magic if magic is None else magic,
            "comment": comment[:31],
            "type_time": api.ORDER_TIME_GTC,
        }
        return self._send_deal(request, info)

    def set_sl_tp(
        self,
        ticket: int,
        *,
        sl: float | None = None,
        tp: float | None = None,
    ) -> TradeResult:
        """Set stop and/or take profit. Omitted values keep the current level."""
        api = _require_mt5()
        pos = self.get_position(ticket)
        if pos is None:
            return TradeResult(ok=False, ticket=ticket, error=f"position {ticket} not found")

        info = self.symbol_info(pos.symbol)
        new_sl = pos.sl if sl is None else self.normalize_price(sl, info)
        new_tp = pos.tp if tp is None else self.normalize_price(tp, info)
        if new_sl == pos.sl and new_tp == pos.tp:
            return TradeResult(
                ok=True,
                ticket=ticket,
                sl=new_sl,
                tp=new_tp,
                comment="unchanged",
            )

        request = {
            "action": api.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": pos.symbol,
            "sl": new_sl,
            "tp": new_tp,
            "magic": pos.magic,
        }
        result = api.order_send(request)
        return self._from_result(result, ticket=ticket, sl=new_sl, tp=new_tp)

    def set_sl(self, ticket: int, sl: float) -> TradeResult:
        return self.set_sl_tp(ticket, sl=sl)

    def set_tp(self, ticket: int, tp: float) -> TradeResult:
        return self.set_sl_tp(ticket, tp=tp)

    def trail_stop(
        self,
        ticket: int,
        *,
        distance: float,
        activate: float = 0.0,
        step: float = 0.0,
    ) -> TradeResult:
        """Move SL only in the profit direction, keeping `distance` from price.

        `distance` / `activate` / `step` are in price units (gold: 2.0 = $2).
        Trailing starts after floating profit >= `activate`. SL moves only if
        the improvement is at least `step` (0 = every better tick).
        """
        pos = self.get_position(ticket)
        if pos is None:
            return TradeResult(ok=False, ticket=ticket, error=f"position {ticket} not found")

        info = self.symbol_info(pos.symbol)
        tick = self.tick(pos.symbol)
        is_buy = pos.type == 0
        price = tick.bid if is_buy else tick.ask
        entry = float(pos.price_open)
        profit_distance = (price - entry) if is_buy else (entry - price)

        if profit_distance < activate:
            return TradeResult(
                ok=True,
                ticket=ticket,
                sl=pos.sl,
                tp=pos.tp,
                comment="trail not activated",
            )

        candidate = price - distance if is_buy else price + distance
        min_dist = self.min_stop_distance(info)
        if min_dist:
            if is_buy:
                candidate = min(candidate, price - min_dist)
            else:
                candidate = max(candidate, price + min_dist)
        candidate = self.normalize_price(candidate, info)

        current_sl = float(pos.sl or 0.0)
        if current_sl:
            improved = (candidate - current_sl) if is_buy else (current_sl - candidate)
            if improved < (step or 0.0):
                return TradeResult(
                    ok=True,
                    ticket=ticket,
                    sl=current_sl,
                    tp=pos.tp,
                    comment="trail step not reached",
                )

        return self.set_sl(ticket, candidate)

    def close_position(self, ticket: int, volume: float | None = None) -> TradeResult:
        api = _require_mt5()
        pos = self.get_position(ticket)
        if pos is None:
            return TradeResult(ok=False, ticket=ticket, error=f"position {ticket} not found")

        info = self.symbol_info(pos.symbol)
        tick = self.tick(pos.symbol)
        lots = self.normalize_volume(volume if volume is not None else pos.volume, info)
        is_buy = pos.type == 0
        request = {
            "action": api.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": pos.symbol,
            "volume": lots,
            "type": api.ORDER_TYPE_SELL if is_buy else api.ORDER_TYPE_BUY,
            "price": tick.bid if is_buy else tick.ask,
            "deviation": self.deviation,
            "magic": pos.magic,
            "comment": "close",
            "type_time": api.ORDER_TIME_GTC,
        }
        return self._send_deal(request, info, ticket=ticket)

    def get_position(self, ticket: int):
        api = _require_mt5()
        positions = api.positions_get(ticket=ticket)
        if positions:
            return positions[0]
        return None

    def positions(self, symbol: str | None = None, magic: int | None = None) -> list:
        api = _require_mt5()
        raw = api.positions_get(symbol=symbol) if symbol else api.positions_get()
        if raw is None:
            return []
        rows = list(raw)
        if magic is not None:
            rows = [p for p in rows if p.magic == magic]
        return rows

    def copy_rates(
        self,
        symbol: str,
        timeframe: int,
        date_from: datetime,
        date_to: datetime,
    ) -> list:
        api = _require_mt5()
        self.symbol_info(symbol)
        start = date_from.astimezone(timezone.utc).replace(tzinfo=None) if date_from.tzinfo else date_from
        end = date_to.astimezone(timezone.utc).replace(tzinfo=None) if date_to.tzinfo else date_to
        rates = api.copy_rates_range(symbol, timeframe, start, end)
        return list(rates) if rates is not None else []

    def m1_open_at(self, symbol: str, when: datetime) -> tuple[float, datetime] | None:
        """Open of the M1 bar at/after `when` (timezone-aware)."""
        api = _require_mt5()
        start = when
        end = when + timedelta(minutes=3)
        bars = self.copy_rates(symbol, api.TIMEFRAME_M1, start, end)
        if not bars:
            return None
        bar = bars[0]
        open_price = float(bar["open"])
        bar_time = datetime.fromtimestamp(int(bar["time"]), tz=timezone.utc)
        return open_price, bar_time

    def daily_prev_close(self, symbol: str) -> tuple[float, datetime] | None:
        """Close of the last completed daily bar."""
        api = _require_mt5()
        self.symbol_info(symbol)
        bars = api.copy_rates_from_pos(symbol, api.TIMEFRAME_D1, 0, 2)
        if bars is None or len(bars) < 2:
            return None
        prev = bars[0]
        return float(prev["close"]), datetime.fromtimestamp(int(prev["time"]), tz=timezone.utc)

    def history_deals(
        self,
        date_from: datetime,
        date_to: datetime,
        *,
        ticket: int | None = None,
    ) -> list:
        api = _require_mt5()
        deals = api.history_deals_get(date_from, date_to)
        if not deals:
            return []
        rows = list(deals)
        if ticket is None:
            return rows
        return [d for d in rows if d.position_id == ticket or d.order == ticket]

    def _send_deal(self, request: dict[str, Any], info, ticket: int | None = None) -> TradeResult:
        api = _require_mt5()
        result = None
        for mode in self.filling_modes(info):
            request["type_filling"] = mode
            result = api.order_send(request)
            if result is None:
                continue
            if result.retcode == api.TRADE_RETCODE_DONE:
                break
            if result.retcode != api.TRADE_RETCODE_INVALID_FILL:
                break
        return self._from_result(result, ticket=ticket, sl=request.get("sl"), tp=request.get("tp"))

    def _from_result(
        self,
        result,
        *,
        ticket: int | None = None,
        sl: float | None = None,
        tp: float | None = None,
    ) -> TradeResult:
        api = _require_mt5()
        if result is None:
            return TradeResult(
                ok=False,
                ticket=ticket,
                sl=sl,
                tp=tp,
                error=f"order_send returned None: {api.last_error()}",
            )
        ok = result.retcode == api.TRADE_RETCODE_DONE
        filled_ticket = int(result.order or result.deal or 0) or ticket
        return TradeResult(
            ok=ok,
            ticket=filled_ticket,
            deal=int(result.deal) if getattr(result, "deal", 0) else None,
            price=float(result.price) if getattr(result, "price", 0) else None,
            volume=float(result.volume) if getattr(result, "volume", 0) else None,
            sl=sl,
            tp=tp,
            retcode=int(result.retcode),
            comment=str(result.comment or ""),
            error="" if ok else f"retcode={result.retcode} comment={result.comment}",
        )


if __name__ == "__main__":
    import getpass

    from dotenv import load_dotenv

    load_dotenv()
    api = _require_mt5()
    path = _env_optional("MT5_PATH") or default_terminal_path()
    timeout = int(os.getenv("MT5_TIMEOUT_MS", "90000"))
    default_login = _env_optional("MT5_LOGIN") or ""
    default_server = _env_optional("MT5_SERVER") or "GBEbrokers-Demo"

    print(f"Terminal path: {path}")
    print("Open GBE MetaTrader 5 first, then enter account details below.")
    print()

    login_raw = input(f"Login [{default_login}]: ").strip() or default_login
    password = getpass.getpass("Password: ").strip()
    server = input(f"Server [{default_server}]: ").strip() or default_server

    if not login_raw.isdigit():
        raise SystemExit("Login must be a number")
    if not password:
        raise SystemExit("Password is required")
    if not server:
        raise SystemExit("Server is required")

    login = int(login_raw)
    print()
    print(f"Connecting login={login} server={server} ...")

    api.shutdown()
    attached = False
    if path:
        attached = bool(api.initialize(Path(path).as_posix(), timeout=timeout))
    if not attached:
        attached = bool(api.initialize(timeout=timeout))

    if attached:
        print("Attached to terminal OK")
        ok = api.login(login, password=password, server=server, timeout=timeout)
        if not ok:
            print(f"login() failed: {api.last_error()}")
            api.shutdown()
            # Fall through to initialize-with-credentials
            attached = False
        else:
            acc = api.account_info()
            if acc is None:
                print(f"login returned OK but no account info: {api.last_error()}")
                api.shutdown()
                raise SystemExit(1)
            print(f"SUCCESS connected login={acc.login} server={acc.server} balance={acc.balance}")
            positions = api.positions_get() or []
            print(f"open positions: {len(positions)}")
            api.shutdown()
            raise SystemExit(0)

    kwargs = {
        "login": login,
        "password": password,
        "server": server,
        "timeout": timeout,
    }
    if path:
        ok = api.initialize(Path(path).as_posix(), **kwargs)
    else:
        ok = api.initialize(**kwargs)

    if not ok:
        print(f"initialize(login=...) failed: {api.last_error()}")
        print("Check: only GBE terminal open, Python enabled, correct password/server.")
        raise SystemExit(1)

    acc = api.account_info()
    if acc is None:
        print(f"Connected but no account info: {api.last_error()}")
        api.shutdown()
        raise SystemExit(1)

    print(f"SUCCESS connected login={acc.login} server={acc.server} balance={acc.balance}")
    positions = api.positions_get() or []
    print(f"open positions: {len(positions)}")
    api.shutdown()
