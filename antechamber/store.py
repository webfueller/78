"""The append-only log, and the fork.

A branch sees its ancestors' history *up to the moment it forked* and nothing
after. That is deliberate: you rehearse from a known state, so a rehearsal is
reproducible forever even as the trunk keeps moving.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple

from .events import GENESIS, REAL_ACTORS, Event, digest, is_simulated

TRUNK = "trunk"
OPEN_END = 1 << 62  # stands in for "no upper bound" on a branch's own segment

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    gid        INTEGER PRIMARY KEY AUTOINCREMENT,
    branch     TEXT    NOT NULL,
    seq        INTEGER NOT NULL,
    ts         INTEGER NOT NULL,
    kind       TEXT    NOT NULL,
    entity     TEXT    NOT NULL,
    actor      TEXT    NOT NULL,
    payload    TEXT    NOT NULL,
    prev       TEXT    NOT NULL,
    hash       TEXT    NOT NULL UNIQUE,
    commit_id  TEXT,
    UNIQUE (branch, seq)
);
CREATE INDEX IF NOT EXISTS events_branch_gid ON events (branch, gid);
CREATE INDEX IF NOT EXISTS events_kind ON events (kind);

CREATE TABLE IF NOT EXISTS checkpoints (
    branch     TEXT PRIMARY KEY,
    events     INTEGER NOT NULL,
    head_hash  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS branches (
    name       TEXT PRIMARY KEY,
    parent     TEXT,
    fork_gid   INTEGER NOT NULL,
    created_ts INTEGER NOT NULL,
    status     TEXT NOT NULL,
    note       TEXT,
    until_ts   INTEGER
);
"""


class StoreError(RuntimeError):
    pass


class EventStore:
    def __init__(self, path: str):
        self.path = path
        # isolation_level=None puts sqlite3 in autocommit, which is what lets
        # `transaction()` below open a real BEGIN IMMEDIATE. Under the default
        # the driver has already started one and the explicit BEGIN is refused.
        self.db = sqlite3.connect(path, isolation_level=None, timeout=15.0)
        self.db.row_factory = sqlite3.Row
        self._depth = 0
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        try:  # stores written before rewinding was bounded properly
            self.db.execute("ALTER TABLE branches ADD COLUMN until_ts INTEGER")
        except sqlite3.OperationalError:
            pass
        if self.branch(TRUNK) is None:
            self.db.execute(
                "INSERT INTO branches (name, parent, fork_gid, created_ts, status, note)"
                " VALUES (?, NULL, 0, 0, 'open', 'the world as it actually happened')",
                (TRUNK,),
            )

    def close(self) -> None:
        self.db.close()

    @contextlib.contextmanager
    def transaction(self):
        """Run a unit of work atomically, or not at all.

        A commit is ten appends. Without this, a crash on the fifth leaves five
        messages sent, no receipt, and nothing for undo to withdraw -- and two
        concurrent commits interleave and execute the same actions twice.
        IMMEDIATE takes the write lock up front so the second writer waits rather
        than discovering the conflict half way through.
        """
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return
        self.db.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield self
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")
        finally:
            self._depth = 0

    # ---------------------------------------------------------------- branches

    def branch(self, name: str) -> Optional[sqlite3.Row]:
        cur = self.db.execute("SELECT * FROM branches WHERE name = ?", (name,))
        return cur.fetchone()

    def branches(self) -> List[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM branches ORDER BY created_ts, name"))

    def require_branch(self, name: str) -> sqlite3.Row:
        row = self.branch(name)
        if row is None:
            raise StoreError(f"no such branch: {name}")
        return row

    def fork(
        self,
        name: str,
        parent: str = TRUNK,
        at_ts: Optional[int] = None,
        note: str = "",
    ) -> sqlite3.Row:
        """Fork `parent` into `name`, optionally rewinding to a point in world time."""
        if self.branch(name) is not None:
            raise StoreError(f"branch already exists: {name}")
        self.require_branch(parent)

        if at_ts is None:
            fork_gid = self._max_visible_gid(parent)
        else:
            cur = self.db.execute(
                "SELECT COALESCE(MAX(gid), 0) AS g FROM events"
                " WHERE branch = ? AND ts <= ? AND gid <= ?",
                (parent, at_ts, self._max_visible_gid(parent)),
            )
            fork_gid = cur.fetchone()["g"]

        created = at_ts if at_ts is not None else self.now(parent)
        # Bounding the parent's own segment is not enough: a fork of a fork would
        # take the grandparent's history whole and silently ignore the rewind.
        # The bound travels with the branch and applies to everything inherited.
        self.db.execute(
            "INSERT INTO branches (name, parent, fork_gid, created_ts, status, note, until_ts)"
            " VALUES (?, ?, ?, ?, 'open', ?, ?)",
            (name, parent, fork_gid, created, note, at_ts),
        )
        return self.require_branch(name)

    def root(self, name: str, note: str = "") -> sqlite3.Row:
        """A parentless branch with its own timeline (used for the ledger)."""
        row = self.branch(name)
        if row is not None:
            return row
        self.db.execute(
            "INSERT INTO branches (name, parent, fork_gid, created_ts, status, note)"
            " VALUES (?, NULL, 0, 0, 'open', ?)",
            (name, note),
        )
        return self.require_branch(name)

    def set_status(self, name: str, status: str) -> None:
        self.require_branch(name)
        self.db.execute("UPDATE branches SET status = ? WHERE name = ?", (status, name))

    def _max_visible_gid(self, branch: str) -> int:
        cur = self.db.execute(
            "SELECT COALESCE(MAX(gid), 0) AS g FROM events WHERE branch = ?", (branch,)
        )
        return cur.fetchone()["g"]

    def lineage(self, branch: str) -> List[Tuple[str, int]]:
        """Visible segments, oldest ancestor first: (branch name, max visible gid)."""
        segments: List[Tuple[str, int]] = []
        row = self.require_branch(branch)
        segments.append((branch, OPEN_END))
        while row["parent"] is not None:
            segments.append((row["parent"], row["fork_gid"]))
            row = self.require_branch(row["parent"])
        segments.reverse()
        return segments

    # ------------------------------------------------------------------ append

    def append(
        self,
        branch: str,
        kind: str,
        entity: str,
        payload: dict,
        actor: str,
        ts: int,
        commit_id: Optional[str] = None,
        allow_backdate: bool = False,
    ) -> Event:
        row = self.require_branch(branch)
        if row["status"] != "open":
            raise StoreError(f"branch {branch} is {row['status']}; it accepts no new events")
        if branch == TRUNK and actor not in REAL_ACTORS:
            raise StoreError(
                f"refusing actor {actor!r} on the trunk: it accepts "
                f"{', '.join(sorted(REAL_ACTORS))} and nothing else, so no spelling "
                "of a simulated counterparty can reach the record"
            )

        head = self.head(branch)
        prev = head.hash if head is not None else GENESIS
        # On a fresh fork the sequence restarts at 0, but the hash chain does not:
        # the first event still points at the last event the fork inherited.
        seq = head.seq + 1 if (head is not None and head.branch == branch) else 0
        if not allow_backdate and head is not None and ts < head.ts:
            raise StoreError(
                f"world time may not run backwards on {branch}: {ts} < {head.ts}"
            )

        h = digest(prev, branch, seq, ts, kind, entity, actor, payload)
        cur = self.db.execute(
            "INSERT INTO events (branch, seq, ts, kind, entity, actor, payload, prev, hash, commit_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (branch, seq, ts, kind, entity, actor,
             json.dumps(payload, sort_keys=True), prev, h, commit_id),
        )
        self.db.execute(
            "INSERT INTO checkpoints (branch, events, head_hash) VALUES (?, 1, ?)"
            " ON CONFLICT(branch) DO UPDATE SET events = events + 1, head_hash = ?",
            (branch, h, h),
        )
        return Event(
            branch=branch,
            seq=seq,
            ts=ts,
            kind=kind,
            entity=entity,
            actor=actor,
            payload=payload,
            prev=prev,
            hash=h,
            gid=cur.lastrowid,
            commit_id=commit_id,
        )

    def append_many(self, branch: str, rows: Iterable[dict]) -> List[Event]:
        return [self.append(branch=branch, **r) for r in rows]

    # -------------------------------------------------------------------- read

    def _inherited_bound(self, branch: str) -> Optional[int]:
        """The tightest rewind anywhere up this branch's chain."""
        bounds = []
        row = self.require_branch(branch)
        while row is not None:
            if row["until_ts"] is not None:
                bounds.append(row["until_ts"])
            row = self.branch(row["parent"]) if row["parent"] else None
        return min(bounds) if bounds else None

    def _visible(self, branch: str) -> Tuple[str, List[object]]:
        segments = self.lineage(branch)
        bound = self._inherited_bound(branch)
        parts, params = [], []  # type: (List[str], List[object])
        for name, max_gid in segments:
            if bound is not None and name != branch:
                parts.append("(branch = ? AND gid <= ? AND ts <= ?)")
                params.extend([name, max_gid, bound])
            else:
                parts.append("(branch = ? AND gid <= ?)")
                params.extend([name, max_gid])
        return " OR ".join(parts), params

    def read(self, branch: str, until_ts: Optional[int] = None) -> List[Event]:
        clauses, params = self._visible(branch)
        sql = f"SELECT * FROM events WHERE ({clauses})"
        if until_ts is not None:
            sql += " AND ts <= ?"
            params.append(until_ts)
        sql += " ORDER BY gid"
        return [self._row_to_event(r) for r in self.db.execute(sql, params)]

    def head(self, branch: str) -> Optional[Event]:
        clauses, params = self._visible(branch)
        cur = self.db.execute(
            f"SELECT * FROM events WHERE ({clauses}) ORDER BY gid DESC LIMIT 1", params
        )
        row = cur.fetchone()
        return self._row_to_event(row) if row else None

    def now(self, branch: str = TRUNK) -> int:
        """World time, not wall time. The log decides what 'now' means."""
        head = self.head(branch)
        return head.ts if head is not None else 0

    def by_kind(self, branch: str, kinds: Sequence[str]) -> Iterator[Event]:
        wanted = set(kinds)
        for ev in self.read(branch):
            if ev.kind in wanted:
                yield ev

    @staticmethod
    def _row_to_event(r: sqlite3.Row) -> Event:
        return Event(
            branch=r["branch"],
            seq=r["seq"],
            ts=r["ts"],
            kind=r["kind"],
            entity=r["entity"],
            actor=r["actor"],
            payload=json.loads(r["payload"]),
            prev=r["prev"],
            hash=r["hash"],
            gid=r["gid"],
            commit_id=r["commit_id"],
        )

    # --------------------------------------------------------------- integrity

    def verify(self, branch: str) -> int:
        """Recompute the whole chain. Returns the number of events checked.

        The chain alone cannot see its own tail being cut off: truncate the last
        forty events and what remains is a perfectly valid chain -- which is the
        cheapest way to erase a commit receipt. So each branch also keeps a count
        and a head hash, and verify checks the chain ends where the branch says
        it ends.

        This is not a signature. Someone with write access to the file can update
        the checkpoint too. It catches truncation, corruption and naive editing;
        it does not withstand a determined adversary, and the README says so.
        """
        own = [e for e in self.read(branch) if e.branch == branch]
        row = self.db.execute(
            "SELECT events, head_hash FROM checkpoints WHERE branch = ?", (branch,)
        ).fetchone()
        if row is not None:
            if len(own) != row["events"]:
                raise StoreError(
                    f"{branch} holds {len(own)} events but its checkpoint says "
                    f"{row['events']}: history has been truncated or removed"
                )
            if own and own[-1].hash != row["head_hash"]:
                raise StoreError(f"{branch} does not end where its checkpoint says it does")

        prev = GENESIS
        count = 0
        for ev in self.read(branch):
            if ev.prev != prev:
                raise StoreError(
                    f"broken chain at gid {ev.gid} on {branch}: "
                    f"prev={ev.prev[:12]} expected={prev[:12]}"
                )
            if ev.recompute() != ev.hash:
                raise StoreError(f"tampered event at gid {ev.gid} on {branch}")
            prev = ev.hash
            count += 1
        return count
