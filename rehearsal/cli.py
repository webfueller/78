"""rehearsal, from a terminal.

Deliberately small, and deliberately read-only. Everything here answers "what
happened and can I trust it" — the questions somebody asks *about* an agent
rather than the ones they ask it to do. Making changes is a domain's job; this
command has no way to write to a log and that is a feature, since the thing you
audit with should not be the thing that can alter the record.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from typing import Optional

from . import audit
from .store import TRUNK, EventStore, StoreError


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="rehearsal",
        description="Read the record: what an agent committed, what it was chosen "
                    "over, what it predicted, and whether the chain holds.",
    )
    ap.add_argument("--db", required=True, help="the log to read")
    ap.add_argument("--branch", default=TRUNK)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("audit", help="what was committed, in readable form")
    p.add_argument("--limit", type=int, help="only the most recent N commits")
    p.add_argument("--html", metavar="FILE", help="write a self-contained page instead")
    p.add_argument("--json", action="store_true")
    p.add_argument("--title", default="Audit")

    p = sub.add_parser("verify", help="recompute the chain")

    p = sub.add_parser("branches", help="every branch, and what became of it")

    p = sub.add_parser("log", help="raw events, oldest first")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--kind", action="append", default=[])

    args = ap.parse_args(argv)

    try:
        store = EventStore(args.db)
    except Exception as exc:
        print(f"error: cannot open {args.db}: {exc}", file=sys.stderr)
        return 1

    try:
        if args.cmd == "audit":
            if args.html:
                page = audit.render_html(store, branch=args.branch, limit=args.limit,
                                         title=args.title)
                with io.open(args.html, "w", encoding="utf-8") as fh:
                    fh.write(page)
                print(f"wrote {args.html} ({len(page):,} bytes, self-contained)")
            elif args.json:
                print(json.dumps(audit.summary(store, branch=args.branch, limit=args.limit),
                                 indent=2))
            else:
                print(audit.render_text(store, branch=args.branch, limit=args.limit))

        elif args.cmd == "verify":
            out = audit.integrity(store, args.branch)
            print(json.dumps(out, indent=2))
            return 0 if out["ok"] else 2

        elif args.cmd == "branches":
            for row in store.branches():
                print(f"{row['name']:<40} {row['status']:<10} {row['note'] or ''}")

        elif args.cmd == "log":
            wanted = set(args.kind)
            rows = [e for e in store.read(args.branch) if not wanted or e.kind in wanted]
            for ev in rows[-args.limit:]:
                print(f"{ev.gid:>6}  {ev.ts:>12}  {ev.actor:<14} {ev.kind:<26} "
                      f"{ev.entity:<28} {ev.hash[:8]}")

    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
