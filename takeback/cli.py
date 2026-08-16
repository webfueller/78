"""takeback, from a terminal.

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
import os
import sys
from typing import Optional

from . import anchor, audit
from .store import TRUNK, EventStore, StoreError


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="takeback",
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

    p = sub.add_parser("verify", help="recompute the chain, and check the anchor")
    p.add_argument("--key", help=f"anchor key file (default: ${anchor.ENV_KEY})")
    p.add_argument("--anchor", metavar="FILE", help="default: <db>.anchor")

    p = sub.add_parser("anchor", help="the head, recorded where the log cannot reach")
    p.add_argument("--init", action="store_true", help="create a key, once")
    p.add_argument("--write", action="store_true", help="stamp the current head")
    p.add_argument("--show", action="store_true", help="list what has been stamped")
    p.add_argument("--key", help=f"key file (default: ${anchor.ENV_KEY})")
    p.add_argument("--anchor", metavar="FILE", help="default: <db>.anchor")

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
            out = anchor.check(store, args.db, branch=args.branch,
                               key_path=args.key, path=args.anchor)
            print(json.dumps(out, indent=2))
            return 0 if out.get("ok", out["chain_ok"]) else 2

        elif args.cmd == "anchor":
            return _anchor_cmd(store, args)

        elif args.cmd == "branches":
            for row in store.branches():
                print(f"{row['name']:<40} {row['status']:<10} {row['note'] or ''}")

        elif args.cmd == "log":
            wanted = set(args.kind)
            rows = [e for e in store.read(args.branch) if not wanted or e.kind in wanted]
            for ev in rows[-args.limit:]:
                print(f"{ev.gid:>6}  {ev.ts:>12}  {ev.actor:<14} {ev.kind:<26} "
                      f"{ev.entity:<28} {ev.hash[:8]}")

    except (StoreError, anchor.AnchorError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


def _anchor_cmd(store, args) -> int:
    path = args.anchor or anchor.default_path(args.db)

    if args.init:
        key_path = args.key or os.environ.get(anchor.ENV_KEY)
        if not key_path:
            print(f"error: where should the key go? pass --key, or set "
                  f"${anchor.ENV_KEY} to the path you want",
                  file=sys.stderr)
            return 1
        anchor.create_key(key_path)
        print(f"wrote a key to {key_path} (mode 600).")
        print(f"Keep it somewhere the agent cannot write. Set "
              f"{anchor.ENV_KEY}={key_path} and commits will be anchored.")
        for warning in anchor.key_warnings(key_path, args.db, path):
            print(f"note: {warning}")
        return 0

    a = anchor.Anchor.open(args.db, key_path=args.key, path=args.anchor)

    if args.write:
        rec = a.record(store, args.branch)
        print(json.dumps(rec, indent=2))
        return 0

    if args.show:
        for rec in a.records():
            print(f"{rec['seq']:>4}  {rec['ts']:>12}  {rec['branch']:<20} "
                  f"{rec['events']:>6} events  {rec['head'][:12]}")
        return 0

    print(json.dumps(a.verify(store, args.branch), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
