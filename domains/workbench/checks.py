"""Reality answering back.

The claims a change set makes are about what happens *after* it lands, and the
thing that settles them is the project's own checks. Run them, write down what
they said, and let the ledger score every claim whose moment has passed.

This is the loop that makes the preview worth anything: without it the workbench
would be making confident statements nobody ever marks.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from typing import List, Optional

from rehearsal.store import TRUNK, EventStore

from . import events as E
from .kernel import KERNEL
from .observe import clock

TIMEOUT = 900


def run(
    store: EventStore,
    root: str,
    command: str,
    at: Optional[int] = None,
    timeout: int = TIMEOUT,
) -> dict:
    """Run the checks, record the verdict, settle what it settles."""
    started = time.time()
    try:
        done = subprocess.run(
            shlex.split(command), cwd=root, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
        code, tail = done.returncode, (done.stdout + done.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        code, tail = 124, f"timed out after {timeout}s"
    except FileNotFoundError as exc:
        raise ValueError(f"cannot run checks: {exc}")

    return report(
        store, command=command, ok=(code == 0), code=code,
        detail=tail, seconds=round(time.time() - started, 2), at=at,
    )


def report(
    store: EventStore,
    *,
    command: str,
    ok: bool,
    code: int = 0,
    detail: str = "",
    seconds: float = 0.0,
    at: Optional[int] = None,
) -> dict:
    """Write down a verdict somebody else produced (CI, a colleague, a hook)."""
    ts = clock(store, at)
    store.append(
        branch=TRUNK, kind=E.CHECK_REPORTED, entity=f"check_{ts}", actor=E.ACTOR_WORLD,
        ts=ts, payload={"command": command, "ok": bool(ok), "code": int(code),
                        "detail": detail, "seconds": seconds},
    )
    settled = KERNEL.ledger.resolve_due(store)
    return {
        "ok": bool(ok),
        "code": int(code),
        "at": ts,
        "seconds": seconds,
        "settled": len(settled),
        "detail": detail,
    }


def score(store: EventStore) -> dict:
    return KERNEL.ledger.score(store)


def pending(store: EventStore) -> List[dict]:
    return [r for r in KERNEL.ledger.all(store).values() if r["outcome"] is None]
