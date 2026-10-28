"""Claude Code PreToolUse guard for TradePilot trading-safety rules.

Blocks (exit code 2) tool calls that would:
- enable LIVE_TRADING_ENABLED or PAPER_ALLOW_UNVERIFIED_QUOTES in runtime code or config,
- add broker order placement/modification/cancellation calls to apps/ or packages/,
- read, print or edit the secrets file `.env`,
- edit this guard.
Tests (tests/) and Markdown docs may mention the flags; they are not runtime config.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import PurePath

ENABLE_FLAG = re.compile(
    r"(?i)\b(live_trading_enabled|paper_allow_unverified_quotes)\b[\"']?\s*(?::\s*[\w\[\]|. ]+?\s*)?[:=]\s*[\"']?(true|1|yes|on)\b"
)
BROKER_ORDER = re.compile(
    r"(?i)(order/(place|modify|cancel)|\bplace_?order\b|\bmodify_?order\b|\bcancel_?order\b|/v[23]/order\b)"
)
ENV_FILE_TOKEN = re.compile(r"(^|[\s\"'=/\\<>|;&(])\.env(?![\w.-])")


def norm(path: str) -> str:
    return PurePath(path).as_posix().lower()


def is_exempt_for_flags(path: str) -> bool:
    p = norm(path)
    return p.endswith(".md") or "/tests/" in f"/{p}" or p.startswith("tests/")


def is_runtime_code(path: str) -> bool:
    p = "/" + norm(path)
    return ("/apps/" in p or "/packages/" in p) and "/tests/" not in p


def is_env_file(path: str) -> bool:
    return PurePath(path).name.lower() == ".env"


def block(reason: str) -> None:
    sys.stderr.write(
        f"TradePilot trading guard blocked this action: {reason}\n"
        "These rules come from TRADING_SAFETY.md and CLAUDE.md. Do not work around this; "
        "stop and explain to the user what you were trying to do.\n"
    )
    sys.exit(2)


def check_file_change(path: str, text: str) -> None:
    if is_env_file(path):
        block("editing the secrets file .env")
    if norm(path).endswith(".claude/hooks/trading_guard.py"):
        block("modifying the trading guard itself (only the user may change it)")
    if not is_exempt_for_flags(path) and ENABLE_FLAG.search(text):
        block(f"enabling LIVE_TRADING_ENABLED or PAPER_ALLOW_UNVERIFIED_QUOTES in {path}")
    if is_runtime_code(path) and BROKER_ORDER.search(text):
        block(f"adding a broker order placement/modify/cancel call in {path}")


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    tool = event.get("tool_name", "")
    data = event.get("tool_input") or {}
    path = data.get("file_path") or data.get("notebook_path") or ""

    if tool == "Write":
        check_file_change(path, data.get("content", ""))
    elif tool == "Edit":
        check_file_change(path, data.get("new_string", ""))
    elif tool == "MultiEdit":
        check_file_change(path, "\n".join(e.get("new_string", "") for e in data.get("edits", [])))
    elif tool == "NotebookEdit":
        check_file_change(path, data.get("new_source", ""))
    elif tool == "Read":
        if is_env_file(path):
            block("reading the secrets file .env")
    elif tool in ("Bash", "PowerShell"):
        command = data.get("command", "")
        if ENV_FILE_TOKEN.search(command):
            block("a shell command that touches the secrets file .env")
        if ENABLE_FLAG.search(command):
            block("a shell command that enables live trading or unverified-quote paper fills")


if __name__ == "__main__":
    main()
