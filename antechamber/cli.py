"""antechamber -- drive the twin from a terminal."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from . import backtest, commits, events as E, ingest, predictions as P, rehearse, server, synthetic
from .predictors import REGISTRY
from .store import TRUNK, EventStore, StoreError
from .world import project

DAY = 24 * 3600
DEFAULT_DB = os.environ.get("ANTECHAMBER_DB", "antechamber.db")


def _out(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=False, default=str))


def _ts(value: str, store: EventStore) -> int:
    """Accept an epoch second, or a relative offset like '-30d' / '-12h'."""
    value = value.strip()
    if value.startswith("-") and value[-1] in "dh":
        n = int(value[1:-1])
        return store.now(TRUNK) - n * (DAY if value[-1] == "d" else 3600)
    return int(value)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="antechamber", description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"event store path (default: {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create an empty store")

    s = sub.add_parser("seed", help="write a synthetic life onto the trunk")
    s.add_argument("--days", type=int, default=120)
    s.add_argument("--seed", type=int, default=7)

    s = sub.add_parser("status", help="project a branch and summarise it")
    s.add_argument("branch", nargs="?", default=TRUNK)

    sub.add_parser("branches", help="list branches")

    s = sub.add_parser("fork", help="fork a branch, optionally rewound in world time")
    s.add_argument("name")
    s.add_argument("--from", dest="parent", default=TRUNK)
    s.add_argument("--at", default=None, help="epoch seconds, or -30d / -12h")
    s.add_argument("--note", default="")

    s = sub.add_parser("log", help="print a branch's events")
    s.add_argument("branch", nargs="?", default=TRUNK)
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--kind", default=None)

    s = sub.add_parser("replay", help="re-project a branch and verify its hash chain")
    s.add_argument("branch", nargs="?", default=TRUNK)

    s = sub.add_parser("predict", help="record claims for a branch's open threads")
    s.add_argument("branch", nargs="?", default=TRUNK)
    s.add_argument("--predictor", default="per-contact", choices=sorted(REGISTRY))
    s.add_argument("--horizon-hours", type=int, default=48)

    s = sub.add_parser("resolve", help="score every claim whose due date has passed")
    s.add_argument("--now", default=None)

    s = sub.add_parser("score", help="the scoreboard, and the kill criterion")
    s.add_argument("--predictor", default=None)

    s = sub.add_parser("backtest", help="rewind, predict, and score against what happened")
    s.add_argument("--predictor", default="per-contact", choices=sorted(REGISTRY))
    s.add_argument("--holdout-days", type=int, default=30)
    s.add_argument("--horizon-hours", type=int, default=48)

    s = sub.add_parser("import", help="build a twin from an mbox and/or ICS export")
    s.add_argument("--mbox", action="append", default=[], help="path to an .mbox (repeatable)")
    s.add_argument("--ics", action="append", default=[], help="path to an .ics (repeatable)")
    s.add_argument("--me", action="append", default=[], help="your email address (repeatable)")

    s = sub.add_parser("rehearse", help="run the week and print the branch map")
    s.add_argument("--mandate", default="chase,prune,defend")
    s.add_argument("--horizon-days", type=int, default=7)

    s = sub.add_parser("serve", help="run the app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)

    s = sub.add_parser("propose", help="write an agent action into a fork (nothing real happens)")
    s.add_argument("branch")
    s.add_argument("--cancel-subscription", dest="cancel_sub", default=None)
    s.add_argument("--send", dest="send_thread", default=None, help="thread id to reply on")
    s.add_argument("--body", default="Following up.")

    s = sub.add_parser("commit", help="promote a branch's agent actions onto the trunk")
    s.add_argument("branch")

    s = sub.add_parser("undo", help="withdraw a commit inside its window")
    s.add_argument("commit_id")

    args = ap.parse_args(argv)
    store = EventStore(args.db)

    try:
        return _dispatch(args, store)
    except (StoreError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


def _dispatch(args, store: EventStore) -> int:
    if args.cmd == "init":
        _out({"db": args.db, "branches": [dict(b) for b in store.branches()]})

    elif args.cmd == "seed":
        end = synthetic.seed_world(store, days=args.days, seed=args.seed)
        _out({"days": args.days, "seed": args.seed, "world_now": end,
              "summary": project(store.read(TRUNK)).summary()})

    elif args.cmd == "status":
        _out(project(store.read(args.branch)).summary())

    elif args.cmd == "branches":
        _out([dict(b) for b in store.branches()])

    elif args.cmd == "fork":
        at = _ts(args.at, store) if args.at else None
        row = store.fork(args.name, args.parent, at_ts=at, note=args.note)
        _out(dict(row))

    elif args.cmd == "log":
        rows = store.read(args.branch)
        if args.kind:
            rows = [e for e in rows if e.kind == args.kind]
        _out([
            {"gid": e.gid, "ts": e.ts, "branch": e.branch, "kind": e.kind,
             "entity": e.entity, "actor": e.actor, "hash": e.hash[:12],
             "commit": e.commit_id}
            for e in rows[-args.limit:]
        ])

    elif args.cmd == "replay":
        checked = store.verify(args.branch)
        first = project(store.read(args.branch)).state_hash()
        second = project(store.read(args.branch)).state_hash()
        _out({"branch": args.branch, "events_verified": checked,
              "state_hash": first, "deterministic": first == second})

    elif args.cmd == "predict":
        w = project(store.read(args.branch))
        at = w.clock
        horizon = args.horizon_hours * 3600
        claims = REGISTRY[args.predictor].predict(w, at_ts=at, horizon=horizon)
        for c in claims:
            P.record(store, origin_branch=args.branch, resolver=c["resolver"],
                     params=c["params"], p=c["p"], claim=c["claim"], made_at=at,
                     resolve_by=c["resolve_by"], predictor=args.predictor)
        _out({"branch": args.branch, "predictor": args.predictor,
              "made_at": at, "claims": len(claims),
              "sample": [{"claim": c["claim"], "p": c["p"]} for c in claims[:5]]})

    elif args.cmd == "resolve":
        now = _ts(args.now, store) if args.now else None
        settled = P.resolve_due(store, now=now)
        _out({"settled": len(settled),
              "sample": [{"claim": s["claim"], "p": s["p"], "outcome": s["outcome"]}
                         for s in settled[:8]]})

    elif args.cmd == "score":
        _out(P.score(store, predictor=args.predictor))

    elif args.cmd == "backtest":
        _out(backtest.run(store, predictor=args.predictor,
                          holdout_days=args.holdout_days,
                          horizon_hours=args.horizon_hours))

    elif args.cmd == "import":
        if args.mbox and not args.me:
            raise StoreError("--me is required with --mbox: without it nothing knows which messages are yours")
        records = []
        for path in args.mbox:
            records += ingest.read_mbox(path, args.me)
        for path in args.ics:
            records += ingest.read_ics(path)
        if not records:
            raise StoreError("nothing to import")
        _out({**ingest.ingest(store, records),
              "summary": project(store.read(TRUNK)).summary()})

    elif args.cmd == "rehearse":
        mandate = [m.strip() for m in args.mandate.split(",") if m.strip()]
        m = rehearse.rehearse(store, mandate=mandate, horizon_days=args.horizon_days)
        _out({
            "rehearsal": m["rehearsal"], "recommended": m["recommended"],
            "plans": [
                {"id": p["id"], "name": p["name"], "actions": len(p["actions"]),
                 "utility": p["utility"], "coverage": p["coverage"],
                 "expected": p["expected"],
                 "futures": [{"branch": b["id"], "p": b["p"], "metrics": b["metrics"]}
                             for b in p["branches"]]}
                for p in m["plans"]
            ],
        })

    elif args.cmd == "serve":
        server.serve(args.db, host=args.host, port=args.port)

    elif args.cmd == "propose":
        w = project(store.read(args.branch))
        ts = w.clock + 60
        written = []
        if args.cancel_sub:
            sub_row = w.subscriptions.get(args.cancel_sub)
            if sub_row is None:
                raise StoreError(f"no such subscription: {args.cancel_sub}")
            store.append(branch=args.branch, kind=E.SUBSCRIPTION_CANCELLED,
                         entity=args.cancel_sub, actor=E.ACTOR_AGENT, ts=ts,
                         payload={"reason": "proposed by the agent"})
            written.append(f"cancel {sub_row.merchant} ({sub_row.amount_cents}c/mo)")
            ts += 60
        if args.send_thread:
            t = w.threads.get(args.send_thread)
            if t is None:
                raise StoreError(f"no such thread: {args.send_thread}")
            store.append(branch=args.branch, kind=E.MESSAGE_SENT, entity=t.id,
                         actor=E.ACTOR_AGENT, ts=ts,
                         payload={"sender": "me", "counterparty": t.counterparty,
                                  "subject": t.subject, "body": args.body})
            written.append(f"reply to {t.counterparty} on '{t.subject}'")
        if not written:
            raise StoreError("nothing proposed; pass --cancel-subscription or --send")
        _out({"branch": args.branch, "proposed": written,
              "trunk_untouched": project(store.read(TRUNK)).state_hash()})

    elif args.cmd == "commit":
        _out(commits.commit(store, args.branch))

    elif args.cmd == "undo":
        _out(commits.undo(store, args.commit_id))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
