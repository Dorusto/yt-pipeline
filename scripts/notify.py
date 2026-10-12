#!/usr/bin/env python3
"""Send a short notification to the ntfy server used by this project.

Usage:
    python scripts/notify.py "D0 phase report is ready"
    echo "blocked on vision model" | python scripts/notify.py - --title Blocked
    python scripts/notify.py "done" --json

The server URL comes from NTFY_URL, default
http://10.10.1.61:8090/opencode-doru.
"""

import argparse
import json
import sys

from common.notify import NotifyError, resolve_url, send


def build_parser():
    parser = argparse.ArgumentParser(description="Send a notification to ntfy.")
    parser.add_argument("message", help="message text, or '-' to read it from stdin")
    parser.add_argument("--title", help="notification title")
    parser.add_argument("--url", help="override NTFY_URL for this call")
    parser.add_argument("--priority", help="ntfy priority (1-5 or min/low/default/high/max)")
    parser.add_argument("--tags", help="comma-separated ntfy tags")
    parser.add_argument("--timeout", type=float, default=10.0, help="request timeout in seconds")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    parser.add_argument("--dry-run", action="store_true", help="resolve the target but do not send")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.message == "-":
        message = sys.stdin.read().strip()
    else:
        message = args.message

    target = resolve_url(args.url)

    if args.dry_run:
        summary = {"sent": False, "url": target, "message": message}
        if args.json:
            print(json.dumps(summary))
        else:
            print(f"dry-run: would post to {target}")
        return 0

    tags = args.tags.split(",") if args.tags else None
    try:
        status = send(
            message,
            title=args.title,
            url=args.url,
            priority=args.priority,
            tags=tags,
            timeout=args.timeout,
        )
    except NotifyError as exc:
        if args.json:
            print(json.dumps({"sent": False, "url": target, "error": str(exc)}))
        else:
            print(f"[error] {exc}", file=sys.stderr)
        return 1

    summary = {"sent": True, "url": target, "status": status}
    if args.json:
        print(json.dumps(summary))
    else:
        print(f"sent to {target} (HTTP {status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())