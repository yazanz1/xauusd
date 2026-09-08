"""One-off local test: open a tiny market trade with SL/TP at live price."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from mt5_client import MT5Client, MT5Error


def main() -> int:
    load_dotenv()
    symbol = os.getenv("MT5_SYMBOL", "XAUUSD")
    lots = float(os.getenv("TEST_LOT", "0.01"))
    sl_dist = float(os.getenv("TEST_SL_USD", "3.0"))
    tp_dist = float(os.getenv("TEST_TP_USD", "3.0"))
    side = (os.getenv("TEST_SIDE", "buy") or "buy").lower()
    if side not in ("buy", "sell"):
        print("TEST_SIDE must be buy or sell")
        return 1

    client = MT5Client.from_env(magic=260831)
    try:
        client.connect()
    except MT5Error as exc:
        print(f"CONNECT FAILED: {exc}")
        return 1

    acc = client.account()
    print(f"connected login={acc.login} server={acc.server} balance={acc.balance}")
    print(f"algo_trading={client.trade_allowed()}")

    tick = client.tick(symbol)
    price = float(tick.ask if side == "buy" else tick.bid)
    if price <= 0:
        print(f"Bad live price for {symbol}: ask={tick.ask} bid={tick.bid}")
        client.disconnect()
        return 1
    if side == "buy":
        sl = price - sl_dist
        tp = price + tp_dist
    else:
        sl = price + sl_dist
        tp = price - tp_dist

    sl = client.normalize_price(sl, symbol)
    tp = client.normalize_price(tp, symbol)
    print(f"opening {side} lots={lots} price~{price} sl={sl} tp={tp}")

    result = client.open_market(
        symbol,
        side,  # type: ignore[arg-type]
        lots,
        sl=sl,
        tp=tp,
        comment="xaubot-test",
        magic=260831,
    )
    if not result.ok:
        print(f"OPEN FAILED: {result.error or result.comment}")
        client.disconnect()
        return 1

    print(
        f"SUCCESS ticket={result.ticket} fill={result.price} "
        f"volume={result.volume} sl={result.sl} tp={result.tp}"
    )
    client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
