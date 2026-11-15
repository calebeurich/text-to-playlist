"""The only part that touches the iMessage database. Runs with no network access.

Prints JSON on stdout: either the list of chats, or the new messages of one chat.
It only opens chats on the allow-list you chose (`list --all`, used when you pick
that list, returns chat names but never message text).
Senders are replaced with "Person A", "Person B" here, so phone numbers and emails
never reach the part of the tool that talks to the internet.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import imessage, state


def alias_senders(msgs: list[imessage.Message]) -> None:
    alias: dict[str, str] = {"me": "me"}
    for m in msgs:
        if m.sender not in alias:
            n = len(alias) - 1
            alias[m.sender] = "Person " + (chr(65 + n) if n < 26 else str(n))
        m.sender = alias[m.sender]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ttp-reader")
    p.add_argument("command", choices=["list", "fetch"])
    p.add_argument("--db", default=str(imessage.DEFAULT_DB))
    p.add_argument("--dms", action="store_true")
    p.add_argument("--all", action="store_true", help="list every chat, not just the allowed ones")
    p.add_argument("--key", default="", help="chat to fetch")
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--since", type=float, default=0)
    args = p.parse_args(argv)
    try:
        conn = imessage.connect(args.db)
    except imessage.MessagesAccessError as e:
        print(e, file=sys.stderr)
        return 2
    allowed = set(state.load_allowed())
    if args.command == "list":
        chats = imessage.list_chats(conn, include_dms=args.dms or not args.all)
        out = [asdict(c) for c in chats if args.all or c.key in allowed]
    else:
        chat = next((c for c in imessage.list_chats(conn, include_dms=True) if c.key == args.key), None)
        if chat is None or chat.key not in allowed:
            print(f"Chat is not on the allow-list: {args.key}", file=sys.stderr)
            return 3
        msgs = imessage.fetch_messages(conn, chat, after_rowid=args.after, since_ts=args.since)
        alias_senders(msgs)
        out = [asdict(m) for m in msgs]
    json.dump(out, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
