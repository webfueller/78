"""The tree, projected from the log.

Every version of every file the workbench has ever seen is in the log, so the
projection is also the restore point: replaying without a commit's events yields
the exact bytes that were there before it. That is what makes the undo real
rather than best-effort -- there is no separate backup to go stale.
"""

from __future__ import annotations

from typing import Dict, List, Sequence

from rehearsal.events import Event
from rehearsal.projection import Projection

from . import events as E


class Tree(Projection):
    def __init__(self) -> None:
        super().__init__()
        self.files: Dict[str, dict] = {}      # path -> {"sha256", "content"}
        self.writes: List[dict] = []          # every edit, in order
        self.checks: List[dict] = []          # every time the checks ran

    # ------------------------------------------------------------------ shape

    def shape(self) -> dict:
        """Content hashes, path by path.

        The content itself is not in here because the hash already stands for it,
        and hashing the same bytes twice would only make `state_hash` slower at
        saying the same thing.
        """
        return {"files": {p: f["sha256"] for p, f in sorted(self.files.items())}}

    # ------------------------------------------------------------------- fold

    def apply(self, ev: Event) -> None:
        p = ev.payload
        k = ev.kind

        if k in (E.FILE_OBSERVED, E.FILE_WRITTEN):
            self.files[ev.entity] = {"sha256": p["sha256"], "content": p["content"]}
            if k == E.FILE_WRITTEN:
                self.writes.append({"ts": ev.ts, "path": ev.entity, "kind": k})

        elif k == E.FILE_DELETED:
            self.files.pop(ev.entity, None)
            self.writes.append({"ts": ev.ts, "path": ev.entity, "kind": k})

        elif k == E.CHECK_REPORTED:
            self.checks.append({"ts": ev.ts, "ok": bool(p["ok"]), "command": p.get("command", "")})

    # ----------------------------------------------------------------- asking

    def managed(self) -> List[str]:
        """Every path the log has ever touched, present or deleted.

        Undo has to be able to delete a file it created, so "what this workbench
        is responsible for" cannot be read off the current tree alone.
        """
        seen = {w["path"] for w in self.writes} | set(self.files)
        return sorted(seen)

    def rewritten_between(self, path: str, after: int, until: int) -> bool:
        return any(after < w["ts"] <= until and w["path"] == path for w in self.writes)

    def check_failed_between(self, after: int, until: int) -> bool:
        return any(after < c["ts"] <= until and not c["ok"] for c in self.checks)

    def summary(self) -> dict:
        failed = [c for c in self.checks if not c["ok"]]
        return {
            "clock": self.clock,
            "files": len(self.files),
            "bytes": sum(len(f["content"].encode("utf-8")) for f in self.files.values()),
            "edits": len(self.writes),
            "checks": len(self.checks),
            "checks_failed": len(failed),
            "commits": len(self.commits),
            "predictions": len(self.predictions),
            "state_hash": self.state_hash(),
        }


def project(evs: Sequence[Event], include_simulated: bool = True) -> Tree:
    return Tree.fold(evs, include_simulated=include_simulated)
