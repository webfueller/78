"""The workbench, as tools an agent can call.

Model Context Protocol over stdio: JSON-RPC 2.0, one message per line. No
dependencies, because a package whose whole argument is "you can audit what your
agent did" should not ask you to trust a transport you did not read.

The shape of the contract matters more than the wiring, so it is stated once
here and enforced everywhere below:

  `propose` writes nothing. It forks, records what it would do and what it thinks
  will happen, and hands back something a person can read.

  `commit` is the only call that touches the filesystem, it takes a branch that
  `propose` produced, and it is the call a host should be asking a human about.

  `undo` puts the bytes back, from the log, inside the window.

Two things are deliberately not agent-controllable. The root directory is fixed
when the server starts, so no argument can point the workbench at somewhere else.
The check command is fixed when the server starts, so `run_checks` cannot be
turned into a shell. An agent that can choose both of those is not being
sandboxed by this server, it is being handed a machine.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional, TextIO

from rehearsal.store import TRUNK, EventStore, StoreError

from . import checks, commits, disk, gitlog, observe, propose
from .state import Tree

# Versions this server knows how to speak. It echoes the client's choice when it
# recognises it, which is what the specification asks for, and otherwise names
# its own and lets the client decide whether to continue.
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
PREFERRED = SUPPORTED[0]

SERVER_INFO = {"name": "workbench", "version": "0.1.0"}


@dataclasses.dataclass
class Context:
    """Everything the tools may act on. Fixed at start-up, not per call."""
    db: str
    root: str
    check_command: Optional[str] = None
    undo_window_note: str = "24 hours from the commit"

    def store(self) -> EventStore:
        return EventStore(self.db)


# ------------------------------------------------------------------- the tools


def _tree(store: EventStore) -> Tree:
    return Tree.fold(store.read(TRUNK))


def t_observe(ctx: Context, store: EventStore, args: dict) -> dict:
    """Record the tree as it is now."""
    return observe.observe(store, ctx.root)


def t_status(ctx: Context, store: EventStore, args: dict) -> dict:
    tree = _tree(store)
    out = tree.summary()
    out["root"] = ctx.root
    out["drift"] = disk.drift(ctx.root, tree)
    out["checks_configured"] = bool(ctx.check_command)
    return out


def t_propose(ctx: Context, store: EventStore, args: dict) -> dict:
    """Rehearse a change set. Writes nothing."""
    edits: List[propose.Edit] = []
    for row in args.get("edits", []):
        path = row.get("path")
        if not path:
            raise ValueError("every edit needs a path")
        disk.resolve(ctx.root, path)  # refuse escapes before anything else happens
        if "content" not in row:
            raise ValueError(f"{path}: an edit needs content, or use `deletes`")
        edits.append(propose.Edit(path, row["content"]))

    tree = _tree(store)
    for path in args.get("deletes", []):
        disk.resolve(ctx.root, path)
        if path not in tree.files:
            raise ValueError(f"{path} is not a file this workbench is managing")
        edits.append(propose.Edit(path, None))

    if not edits:
        raise ValueError("nothing to propose")

    out = propose.rehearse(store, edits, horizon_days=int(args.get("horizon_days", 1)))
    out["note"] = (
        "Nothing has been written. Show the plans to the human, then call "
        "workbench_commit with the branch of the one they chose."
    )
    return out


def t_commit(ctx: Context, store: EventStore, args: dict) -> dict:
    branch = args.get("branch")
    if not branch:
        raise ValueError("commit needs the branch of a proposed plan")
    out = commits.commit(store, branch, ctx.root)
    out["note"] = (
        f"Written to disk. This can be undone for {ctx.undo_window_note} with "
        f"workbench_undo and commit_id {out['commit_id']}."
    )
    return out


def t_undo(ctx: Context, store: EventStore, args: dict) -> dict:
    commit_id = args.get("commit_id")
    if not commit_id:
        raise ValueError("undo needs a commit_id")
    return commits.undo(store, commit_id, ctx.root)


def t_run_checks(ctx: Context, store: EventStore, args: dict) -> dict:
    if not ctx.check_command:
        raise ValueError(
            "this workbench was started without a check command, so there is "
            "nothing to run; start it with --check to enable this"
        )
    return checks.run(store, ctx.root, ctx.check_command)


def t_score(ctx: Context, store: EventStore, args: dict) -> dict:
    return checks.score(store)


def t_learn_from_git(ctx: Context, store: EventStore, args: dict) -> dict:
    return gitlog.ingest(store, ctx.root, limit=args.get("limit"))


TOOLS: Dict[str, dict] = {
    "workbench_status": {
        "fn": t_status,
        "description": (
            "What the workbench is managing, and whether the disk still matches the log. "
            "Call this first: if `drift` is non-empty, files changed outside the workbench "
            "and a commit will refuse until workbench_observe accepts them."
        ),
        "schema": {"type": "object", "properties": {}},
    },
    "workbench_observe": {
        "fn": t_observe,
        "description": (
            "Record the current contents of every managed file. This is how the workbench "
            "learns what is there, and how a human's own edits are deliberately accepted. "
            "Writes to the log, never to the filesystem."
        ),
        "schema": {"type": "object", "properties": {}},
    },
    "workbench_propose": {
        "fn": t_propose,
        "description": (
            "Rehearse a set of file edits. NOTHING IS WRITTEN. Returns the plans it "
            "considered (all of them, the low-risk subset, and doing nothing), what each "
            "would touch, and the probability the checks go red or each file needs revising "
            "again — measured from this repository's own history. Show the result to the "
            "human and let them choose; then call workbench_commit with that plan's branch."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "description": "Files to write, with their full new contents.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string",
                                     "description": "Relative to the workbench root."},
                            "content": {"type": "string",
                                        "description": "The complete new contents."},
                        },
                        "required": ["path", "content"],
                    },
                },
                "deletes": {
                    "type": "array",
                    "description": "Managed files to delete.",
                    "items": {"type": "string"},
                },
                "horizon_days": {
                    "type": "integer",
                    "description": "How far ahead the claims reach. Default 1.",
                },
            },
        },
    },
    "workbench_commit": {
        "fn": t_commit,
        "description": (
            "Write one proposed plan to disk. This is the only call that touches the "
            "filesystem, and it is the one to ask a human about. All of the plan or none "
            "of it: the disk and the log move in a single transaction. Refuses if any file "
            "changed since the plan was rehearsed. Returns a receipt with a state hash and "
            "an undo deadline."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "branch": {"type": "string",
                           "description": "The `branch` of the plan from workbench_propose."},
            },
            "required": ["branch"],
        },
    },
    "workbench_undo": {
        "fn": t_undo,
        "description": (
            "Take a commit back. Restores the exact previous bytes of every file it "
            "touched, including deleting files it created, and reports whether the disk "
            "now matches. Only works inside the undo window."
        ),
        "schema": {
            "type": "object",
            "properties": {"commit_id": {"type": "string"}},
            "required": ["commit_id"],
        },
    },
    "workbench_run_checks": {
        "fn": t_run_checks,
        "description": (
            "Run this project's checks and record the verdict, settling any claim whose "
            "window has closed. The command was fixed when the server started and cannot "
            "be chosen here."
        ),
        "schema": {"type": "object", "properties": {}},
    },
    "workbench_score": {
        "fn": t_score,
        "description": (
            "How well the workbench's own predictions have held up: Brier score against a "
            "leave-one-out base rate, with calibration bands. Read this before trusting "
            "the risk numbers in a proposal."
        ),
        "schema": {"type": "object", "properties": {}},
    },
    "workbench_learn_from_git": {
        "fn": t_learn_from_git,
        "description": (
            "Import this repository's commit history — paths and timestamps only, no file "
            "contents — so the risk numbers have something to be measured from. Worth "
            "doing once, at the start."
        ),
        "schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer",
                                     "description": "Most recent N commits only."}},
        },
    },
}


def tool_list() -> List[dict]:
    return [
        {"name": name, "description": spec["description"], "inputSchema": spec["schema"]}
        for name, spec in sorted(TOOLS.items())
    ]


def call(ctx: Context, name: str, args: dict) -> dict:
    """Run one tool. Returns an MCP tool result, errors included.

    A tool that fails is not a protocol failure: the agent asked for something
    reasonable and the workbench refused, and the refusal is the useful part.
    It comes back as content with isError so the model can read it and adjust.
    """
    spec = TOOLS.get(name)
    if spec is None:
        return _result({"error": f"no such tool: {name}"}, is_error=True)

    store = ctx.store()
    try:
        return _result(spec["fn"](ctx, store, args or {}))
    except (StoreError, ValueError, disk.DiskError, gitlog.GitError) as exc:
        return _result({"error": str(exc), "tool": name}, is_error=True)
    except Exception as exc:  # pragma: no cover - a bug, reported rather than swallowed
        return _result(
            {"error": f"{type(exc).__name__}: {exc}", "tool": name,
             "traceback": traceback.format_exc()[-1500:]},
            is_error=True,
        )
    finally:
        store.close()


def _result(payload: Any, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=False)}],
        "isError": is_error,
    }


# ---------------------------------------------------------------- the protocol


def handle(ctx: Context, msg: dict) -> Optional[dict]:
    """One JSON-RPC message in, at most one response out."""
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return _ok(mid, {
            "protocolVersion": asked if asked in SUPPORTED else PREFERRED,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "Propose edits, show the preview to the human, commit what they choose. "
                "workbench_propose never writes; workbench_commit is the only call that "
                "touches the filesystem, and it can be undone."
            ),
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return _ok(mid, {})

    if method == "tools/list":
        return _ok(mid, {"tools": tool_list()})

    if method == "tools/call":
        name = params.get("name", "")
        return _ok(mid, call(ctx, name, params.get("arguments") or {}))

    if mid is None:
        return None  # an unknown notification is not an error worth answering
    return _err(mid, -32601, f"method not found: {method}")


def _ok(mid, result) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve(ctx: Context, stdin: TextIO = None, stdout: TextIO = None) -> int:
    """Read messages until the input closes."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _write(stdout, _err(None, -32700, "parse error"))
            continue
        if not isinstance(msg, dict):
            _write(stdout, _err(None, -32600, "invalid request"))
            continue

        response = handle(ctx, msg)
        if response is not None:
            _write(stdout, response)
    return 0


def _write(stdout: TextIO, payload: dict) -> None:
    stdout.write(json.dumps(payload) + "\n")
    stdout.flush()
