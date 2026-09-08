"""CLI: python -m sessions

  python -m sessions          # connect once, save session, keep alive
  python -m sessions status   # show saved session
  python -m sessions clear    # delete saved session file
"""

from __future__ import annotations

import json
import logging
import sys

from dotenv import load_dotenv

from sessions import SessionManager, clear_session, status


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = list(argv if argv is not None else sys.argv[1:])
    cmd = args[0] if args else "keepalive"

    if cmd in ("status", "show"):
        print(json.dumps(status(), indent=2, ensure_ascii=False))
        return 0

    if cmd == "clear":
        clear_session()
        print("session cleared")
        return 0

    if cmd in ("keepalive", "start", "connect"):
        manager = SessionManager()
        manager.keepalive()
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
