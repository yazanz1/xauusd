"""File logging for xaubot: TimedRotatingFileHandler + quiet httpx."""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def git_commit_short(repo_dir: Path | None = None) -> str:
    root = repo_dir or Path(__file__).resolve().parent
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return out.decode("utf-8", errors="replace").strip() or "unknown"
    except Exception:
        return "unknown"


def setup_logging(bot_dir: Path | None = None) -> Path:
    """Configure root logger: console + logs/bot.log (append, rotate at midnight, 14 days)."""
    root_dir = bot_dir or Path(__file__).resolve().parent
    log_dir = root_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "bot.log"

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Drop prior handlers (e.g. basicConfig) so we own destinations.
    for h in list(root.handlers):
        try:
            h.close()
        except Exception:
            pass
        root.removeHandler(h)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = TimedRotatingFileHandler(
        filename=str(log_path),
        when="midnight",
        backupCount=14,
        encoding="utf-8",
        utc=False,
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)

    return log_path


def bot_start_banner(commit: str | None = None) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    hash_ = commit if commit is not None else git_commit_short()
    return f"===== Bot start {ts} commit={hash_} ====="
