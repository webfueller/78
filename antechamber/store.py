"""The append-only log, and the fork.

A branch sees its ancestors' history *up to the moment it forked* and nothing
after. That is deliberate: you rehearse from a known state, so a rehearsal is
reproducible forever even as the trunk keeps moving.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple

from .events import GENESIS, Event, digest, is_simulated

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

CREATE TABLE IF NOT EXISTS branches (
    name       TEXT PRIMARY KEY,
    parent     TEXT,
    fork_gid   INTEGER NOT NULL,
    created_ts INTEGER NOT NULL,
    status     TEXT NOT NULL,
    note       TEXT
);
"""


class StoreError(RuntimeError):
    pass


class EventStore:
    def __init__(self, path: str):
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        if self.branch(TRUNK) is None:
            self.db.execute(
                "INSERT INTO branches (name, parent, fork_gid, created_ts, status, note)"
                " VALUES (?, NULL, 0, 0, 'open', 'the world as it actually happened')",
                (TRUNK,),
            )
            self.db.commit()

    def close(self) -> None:
        self.db.close()

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
        self.db.execute(
            "INSERT INTO branches (name, parent, fork_gid, created_ts, status, note)"
            " VALUES (?, ?, ?, ?, 'open', ?)",
            (name, parent, fork_gid, created, note),
        )
        self.db.commit()
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
        self.db.commit()
        return self.require_branch(name)

    def set_status(self, name: str, status: str) -> None:
        self.require_branch(name)
        self.db.execute("UPDATE branches SET status = ? WHERE name = ?", (status, name))
        self.db.commit()

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
        if branch == TRUNK and is_simulated(actor):
            raise StoreError(
                f"refusing to write simulated actor {actor!r} to the trunk: "
                "simulated people do not exist outside their fork"
            )

        head = self.head(branch)
        prev = head.hash if head is not None else GENESIS
        # On a fresh fork the sequence restarts at 0, but the hash chain does not:
        # the first event still points at the last event the fork inherited.
        seq = head.seq + 1 if (head is not None and head.branch == branch) else 0
        if not allow_backdate and ts < (head.ts if head is not None else 0):
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
        self.db.commit()
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

    def read(self, branch: str, until_ts: Optional[int] = None) -> List[Event]:
        segments = self.lineage(branch)
        clauses = " OR ".join("(branch = ? AND gid <= ?)" for _ in segments)
        params: List[object] = []
        for name, max_gid in segments:
            params.extend([name, max_gid])
        sql = f"SELECT * FROM events WHERE ({clauses})"
        if until_ts is not None:
            sql += " AND ts <= ?"
            params.append(until_ts)
        sql += " ORDER BY gid"
        return [self._row_to_event(r) for r in self.db.execute(sql, params)]

    def head(self, branch: str) -> Optional[Event]:
        events = self.read(branch)
        return events[-1] if events else None

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
        """Recompute the whole chain. Returns the number of events checked."""
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
