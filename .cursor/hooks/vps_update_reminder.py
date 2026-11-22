#!/usr/bin/env python3
"""Remind the agent to log VPS deploys in VPS_UPDATES.md around git push."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VPS_LOG = ROOT / "VPS_UPDATES.md"

MSG_BEFORE = (
    "VPS deploy reminder: before finishing this push, ensure VPS_UPDATES.md has a NEW "
    "dated entry at the top (below the intro) with: date, commit hash if known, detailed "
    "what changed, and VPS run notes. Mark comparison-critical changes with "
    "'⭐ חשוב להשוואה'. Also keep STRATEGY.md / MEMORY.md / the xaubot-strategy rule in sync "
    "if execution rules changed."
)

MSG_AFTER = (
    "git push finished. If this was a VPS upload and VPS_UPDATES.md was not updated yet, "
    "add the dated entry now (top of file), then commit/amend only if the user asked and "
    "amend rules allow — otherwise make a follow-up commit the user requests."
)


def _read_stdin() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _looks_like_push(payload: dict) -> bool:
    cmd = str(payload.get("command") or payload.get("command_line") or "")
    lower = cmd.lower()
    return "git" in lower and "push" in lower


def _emit(obj: dict) -> None:
    # Cursor hooks on Windows may use a non-UTF console; keep stdout ASCII-safe.
    print(json.dumps(obj, ensure_ascii=True))


def main() -> int:
    phase = (sys.argv[1] if len(sys.argv) > 1 else "before").lower()
    payload = _read_stdin()

    if not _looks_like_push(payload):
        if phase == "before":
            _emit({"permission": "allow"})
        else:
            _emit({})
        return 0

    log_exists = VPS_LOG.is_file()
    extra = "" if log_exists else " WARNING: VPS_UPDATES.md is missing — create it."

    if phase == "before":
        _emit(
            {
                "permission": "allow",
                "agent_message": MSG_BEFORE + extra,
                "user_message": "Remember to log this deploy in VPS_UPDATES.md.",
            }
        )
        return 0

    # afterShellExecution — inject context for the agent when possible
    exit_code = payload.get("exit_code", payload.get("status"))
    ok = exit_code in (None, 0, "0")
    if ok:
        _emit(
            {
                "additional_context": MSG_AFTER + extra,
                "agent_message": MSG_AFTER + extra,
            }
        )
    else:
        _emit({})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
