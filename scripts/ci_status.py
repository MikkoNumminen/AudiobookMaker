#!/usr/bin/env python3
"""Show the status of recent GitHub Actions runs for this repo.

Usage:
    python scripts/ci_status.py           # show last 5 runs
    python scripts/ci_status.py --watch   # poll every 30 s
    python scripts/ci_status.py -n 10     # show last 10 runs

Uses the public GitHub API (no auth needed for public repos). Hits the
same endpoint the browser uses, so rate limits apply — don't hammer it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

REPO = "MikkoNumminen/AudiobookMaker"
API_URL = f"https://api.github.com/repos/{REPO}/actions/runs"

# Terminal colors (fall back to plain text if not a TTY).
def _color(code: str) -> str:
    return code if sys.stdout.isatty() else ""

RED = _color("\033[31m")
GREEN = _color("\033[32m")
YELLOW = _color("\033[33m")
BLUE = _color("\033[34m")
DIM = _color("\033[2m")
RESET = _color("\033[0m")


def fetch_runs(limit: int = 5) -> list[dict]:
    """Fetch the latest workflow runs from the GitHub API."""
    req = Request(f"{API_URL}?per_page={limit}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "audiobookmaker-ci-status")
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("workflow_runs", [])
    except URLError as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return []


def format_status(status: str, conclusion: Optional[str]) -> str:
    """Turn a run's status + conclusion into a one-word colored label."""
    if status == "in_progress" or status == "queued":
        return f"{YELLOW}running{RESET}"
    if conclusion == "success":
        return f"{GREEN}ok     {RESET}"
    if conclusion == "failure":
        return f"{RED}FAIL   {RESET}"
    if conclusion == "cancelled":
        return f"{DIM}cancel {RESET}"
    return f"{DIM}{conclusion or status:7}{RESET}"


def format_age(iso_ts: str) -> str:
    """Return a short '5m ago' style timestamp."""
    try:
        t = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return iso_ts
    now = datetime.now(t.tzinfo)
    secs = int((now - t).total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def print_runs(runs: list[dict]) -> None:
    """Print a compact table of runs."""
    if not runs:
        print("No runs found.")
        return
    print(f"{'status':7}  {'workflow':30}  {'branch':20}  {'age':10}  message")
    print("-" * 100)
    for run in runs:
        status = format_status(run["status"], run.get("conclusion"))
        name = (run.get("name") or "")[:30]
        branch = (run.get("head_branch") or "")[:20]
        age = format_age(run["updated_at"])
        message_line = (run.get("head_commit") or {}).get("message", "").split("\n")[0]
        print(f"{status}  {name:30}  {BLUE}{branch:20}{RESET}  {age:10}  {message_line[:50]}")


def watch(interval: int = 30, limit: int = 5) -> None:
    """Poll and reprint the table on an interval."""
    print(f"Watching CI every {interval}s (Ctrl+C to stop)...")
    try:
        while True:
            print(f"\n--- {datetime.now().strftime('%H:%M:%S')} ---")
            runs = fetch_runs(limit)
            print_runs(runs)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--limit", type=int, default=5, help="runs to show (default: 5)")
    parser.add_argument("--watch", action="store_true", help="poll every 30s")
    parser.add_argument("--interval", type=int, default=30, help="watch interval in seconds")
    args = parser.parse_args()

    if args.watch:
        watch(args.interval, args.limit)
    else:
        print_runs(fetch_runs(args.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
