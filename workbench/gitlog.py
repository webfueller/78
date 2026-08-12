"""A repository's own history, as evidence.

The risk numbers are only worth reading if they were learned from something, and
every repository is already carrying thousands of labelled examples: this file
was edited then, and it was edited again four days later, or it was not.

What comes in is timestamps and paths -- not contents. `file.touched` says the
edit happened and makes no claim to be able to put anything back, which keeps
the import from quietly pretending to be a restore point for a version of the
file nobody recorded.
"""

from __future__ import annotations

import subprocess
from typing import Dict, Iterable, List, Optional, Sequence

from rehearsal.store import TRUNK, EventStore

from . import events as E

SEP = "\x1f"
RECORD = "\x1e"


class GitError(RuntimeError):
    pass


def history(root: str, limit: Optional[int] = None, ref: str = "HEAD") -> List[dict]:
    """Commits oldest first: {sha, ts, author, paths}.

    Renames are recorded as an edit to the *new* path only. Treating a rename as
    an edit to both would invent history for a path that had none and double-count
    churn on a file that was merely moved.
    """
    # The record separator leads. Trailing it would put each commit's header and
    # its own name-status lines into different chunks, which parses to nothing at
    # all rather than to something wrong -- the good kind of bug, once found.
    fmt = RECORD + SEP.join(["%H", "%at", "%an"])
    cmd = ["git", "log", "--reverse", "--no-merges", "--name-status",
           f"--pretty=format:{fmt}"]
    if limit:
        cmd.append(f"-{limit}")
    cmd.append(ref)

    try:
        done = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise GitError("git is not on the path")
    if done.returncode != 0:
        raise GitError(f"git log failed in {root}: {done.stderr.strip()[:200]}")

    commits: List[dict] = []
    for chunk in done.stdout.split(RECORD):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        head, _, body = chunk.partition("\n")
        parts = head.split(SEP)
        if len(parts) < 3:
            continue
        sha, ts, author = parts[0], parts[1], parts[2]
        paths = []
        for line in body.splitlines():
            if not line.strip():
                continue
            fields = line.split("\t")
            status = fields[0]
            if status.startswith("R") and len(fields) >= 3:
                paths.append(fields[2])
            elif len(fields) >= 2 and status[0] in "AMD":
                paths.append(fields[1])
        if paths:
            commits.append({
                "sha": sha, "ts": int(ts), "author": author, "paths": sorted(set(paths)),
            })
    commits.sort(key=lambda c: (c["ts"], c["sha"]))
    return commits


def ingest(
    store: EventStore,
    root: str,
    limit: Optional[int] = None,
    ref: str = "HEAD",
    branch: str = TRUNK,
    include: Optional[Sequence[str]] = None,
) -> Dict[str, int]:
    """Write a repository's edit history onto a branch as `file.touched`."""
    commits = history(root, limit=limit, ref=ref)
    # Keyed on the commit, not on the timestamp. Timestamps are not stable across
    # imports: the log refuses to run backwards, so an out-of-order commit gets
    # pinned to its predecessor -- and on a second import the whole history is
    # pinned to the previous head, which made `(ts, path)` miss every time and
    # quietly duplicated the history it was supposed to skip.
    seen = {
        (e.payload.get("commit", ""), e.entity)
        for e in store.read(branch)
        if e.kind == E.FILE_TOUCHED
    }

    written = skipped = nudged = 0
    with store.transaction():
        clock = store.now(branch)
        for c in commits:
            # Author timestamps run backwards more often than people expect --
            # rebases, cherry-picks, a laptop with a wrong clock. The log refuses
            # to go back in time, so such a commit is pinned to the previous one
            # and counted, because silently reordering somebody's history is the
            # kind of thing that shows up later as an inexplicable churn rate.
            ts = max(c["ts"], clock)
            nudged += 1 if ts != c["ts"] else 0
            clock = ts
            sha = c["sha"][:12]
            for path in c["paths"]:
                if include is not None and not any(path.endswith(s) for s in include):
                    continue
                if (sha, path) in seen:
                    skipped += 1
                    continue
                store.append(
                    branch=branch, kind=E.FILE_TOUCHED, entity=path, actor=E.ACTOR_WORLD,
                    ts=ts, payload={"commit": sha, "author": c["author"]},
                )
                seen.add((sha, path))
                written += 1
    return {
        "commits": len(commits),
        "touches": written,
        "skipped": skipped,
        "out_of_order": nudged,
    }
