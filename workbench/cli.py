"""workbench, from a terminal."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from rehearsal.store import TRUNK, EventStore, StoreError

from . import backtest, checks, commits, disk, gitlog, mcp, observe, propose, synthetic
from .state import Tree


def out(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=False))


def _edits(store: EventStore, root: str, from_dir: Optional[str],
           delete: List[str]) -> List[propose.Edit]:
    """What the agent wants, as a diff against what the log says is there."""
    tree = Tree.fold(store.read(TRUNK))
    edits: List[propose.Edit] = []
    if from_dir:
        for path, content in sorted(disk.scan(from_dir).items()):
            known = tree.files.get(path)
            if known is None or known["sha256"] != disk.sha(content):
                edits.append(propose.Edit(path, content))
    for path in delete:
        if path in tree.files:
            edits.append(propose.Edit(path, None))
    return edits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="workbench")
    ap.add_argument("--db", default="workbench.db")
    ap.add_argument("--root", default=".", help="the directory the workbench manages")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("observe", help="record the tree as it is now")
    sub.add_parser("status")
    sub.add_parser("drift", help="where the disk and the log disagree")
    sub.add_parser("score", help="how well the risk numbers have held up")

    p = sub.add_parser("propose", help="rehearse a change set without writing anything")
    p.add_argument("--from-dir", help="a directory holding the agent's version of the tree")
    p.add_argument("--delete", action="append", default=[])
    p.add_argument("--horizon-days", type=int, default=1)

    p = sub.add_parser("commit", help="write a rehearsed plan to disk, atomically")
    p.add_argument("branch")

    p = sub.add_parser("undo")
    p.add_argument("commit_id")

    p = sub.add_parser("check", help="run the checks and settle what they settle")
    p.add_argument("--command", required=True)
    p.add_argument("--timeout", type=int, default=checks.TIMEOUT)

    p = sub.add_parser("git-import", help="learn from this repository's own history")
    p.add_argument("--limit", type=int)
    p.add_argument("--ref", default="HEAD")

    p = sub.add_parser("seed-repo", help="a generated history with a known answer key")
    p.add_argument("--days", type=int, default=400)
    p.add_argument("--paths", type=int, default=24)
    p.add_argument("--seed", type=int, default=3)

    p = sub.add_parser("backtest", help="does the preview know anything?")
    p.add_argument("--predictor", default=None, help="omit to compare all of them")
    p.add_argument("--horizon-days", type=float, default=7.0)

    p = sub.add_parser("mcp", help="serve the workbench as tools an agent can call")
    p.add_argument("--check", help="the command `workbench_run_checks` runs. "
                                   "Fixed here on purpose: the agent cannot choose it.")

    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    store = EventStore(args.db)

    try:
        if args.cmd == "observe":
            out(observe.observe(store, root))

        elif args.cmd == "status":
            out(Tree.fold(store.read(TRUNK)).summary())

        elif args.cmd == "drift":
            found = disk.drift(root, Tree.fold(store.read(TRUNK)))
            out({"drift": found, "clean": not found})

        elif args.cmd == "score":
            out(checks.score(store))

        elif args.cmd == "propose":
            edits = _edits(store, root, args.from_dir, args.delete)
            if not edits:
                out({"edits": 0, "note": "nothing proposed differs from what the log holds"})
                return 0
            out(propose.rehearse(store, edits, horizon_days=args.horizon_days))

        elif args.cmd == "commit":
            out(commits.commit(store, args.branch, root))

        elif args.cmd == "undo":
            out(commits.undo(store, args.commit_id, root))

        elif args.cmd == "check":
            out(checks.run(store, root, args.command, timeout=args.timeout))

        elif args.cmd == "git-import":
            out(gitlog.ingest(store, root, limit=args.limit, ref=args.ref))

        elif args.cmd == "seed-repo":
            key = synthetic.seed_repo(store, days=args.days, paths=args.paths, seed=args.seed)
            out({"paths": len(key), "answer_key": key})

        elif args.cmd == "mcp":
            # The server opens the store per call; this one is only here because
            # every other subcommand needs it.
            store.close()
            return mcp.serve(mcp.Context(db=args.db, root=root, check_command=args.check))

        elif args.cmd == "backtest":
            horizon = int(args.horizon_days * 86400)
            if args.predictor:
                out(backtest.run(store, predictor=args.predictor, horizon=horizon))
            else:
                out(backtest.compare(store, horizon=horizon))

    except (StoreError, ValueError, disk.DiskError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
