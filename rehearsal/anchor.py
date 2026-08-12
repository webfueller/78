"""An anchor outside the log.

The chain catches a rewritten payload and a deleted event. It cannot catch
somebody who rewrites a payload and then recomputes every hash after it, because
the result is a perfectly valid chain — and the per-branch checkpoint does not
help, since whoever can write the events table can write the checkpoints table
in the same breath. Everything the log can prove, it proves to itself.

So the head goes somewhere the log cannot reach: an append-only file of
`(branch, count, head hash, time)` lines, each authenticated with a key that
lives outside the database. Forging history now needs the key as well as write
access, which is the whole point — it moves the bar from "edit a file" to "steal
a secret".

Two things this is not, stated here so nobody has to infer them:

  It is not a public-key signature. HMAC is symmetric: the anchor proves to
  *whoever holds the key* that the log is intact, and does not let a third party
  verify your log without it. The stdlib has no Ed25519 and this package has no
  dependencies, so that is the honest trade. `_mac` is the only place that would
  change if it were worth taking a dependency for.

  It is not proof against a matched rollback. An attacker who truncates the log
  *and* removes the anchor lines that came after leaves a state that verifies.
  Detecting that needs memory outside both files -- a copy of the anchor, or a
  witness who remembers the last count. Keep the anchor backed up somewhere the
  agent cannot write, and the attack becomes visible.
"""

from __future__ import annotations

import hmac
import io
import json
import os
import stat
import time
from hashlib import sha256
from typing import Dict, List, Optional

from .events import GENESIS, canonical
from .store import TRUNK, EventStore, StoreError

ENV_KEY = "REHEARSAL_ANCHOR_KEY"   # path to the key file
VERSION = 1
KEY_BYTES = 32


class AnchorError(RuntimeError):
    pass


# --------------------------------------------------------------------- the key


def create_key(path: str) -> bytes:
    """Write a new key, once.

    Refuses to overwrite. Losing a key does not merely mean re-keying: every
    anchor line written under the old one becomes unverifiable, so the history
    you were keeping honest goes quiet. That deserves a hard stop rather than a
    warning nobody reads.
    """
    if os.path.exists(path):
        raise AnchorError(
            f"{path} already exists. Overwriting it would make every anchor line "
            "written under the old key unverifiable — move it aside yourself if "
            "that is really what you want."
        )
    key = os.urandom(KEY_BYTES)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key.hex().encode("ascii") + b"\n")
    return key


def load_key(path: Optional[str] = None) -> bytes:
    path = path or os.environ.get(ENV_KEY)
    if not path:
        raise AnchorError(
            f"no anchor key: pass one, or set {ENV_KEY} to the path of a key file "
            "(`rehearsal anchor --init` makes one)"
        )
    if not os.path.exists(path):
        raise AnchorError(f"no anchor key at {path}")
    with io.open(path, encoding="ascii") as fh:
        raw = fh.read().strip()
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        raise AnchorError(f"{path} does not hold a hex key")
    if len(key) < 16:
        raise AnchorError(f"{path} holds a {len(key)}-byte key; 16 is the minimum")
    return key


def key_warnings(key_path: str, db_path: str, anchor_path: str) -> List[str]:
    """Setup problems worth saying out loud rather than failing over."""
    out = []
    try:
        mode = stat.S_IMODE(os.stat(key_path).st_mode)
        if mode & 0o077:
            out.append(f"the key at {key_path} is readable by others (mode {mode:o})")
    except OSError:
        pass
    key_dir = os.path.dirname(os.path.abspath(key_path))
    for name, other in (("log", db_path), ("anchor", anchor_path)):
        if os.path.dirname(os.path.abspath(other)) == key_dir:
            out.append(
                f"the key sits in the same directory as the {name}; anyone who can "
                f"write one can probably read the other"
            )
    return out


# ------------------------------------------------------------------ the anchor


def default_path(db_path: str) -> str:
    return db_path + ".anchor"


def _mac(key: bytes, payload: dict) -> str:
    return hmac.new(key, canonical(payload).encode("utf-8"), sha256).hexdigest()


class Anchor:
    def __init__(self, path: str, key: bytes):
        self.path = path
        self.key = key

    @classmethod
    def open(cls, db_path: str, key_path: Optional[str] = None,
             path: Optional[str] = None) -> "Anchor":
        return cls(path or default_path(db_path), load_key(key_path))

    @classmethod
    def from_env(cls, db_path: str, path: Optional[str] = None) -> Optional["Anchor"]:
        """An anchor if one is configured, and silence if not.

        This is how a domain gets tamper-evidence without growing a flag for it:
        set the environment variable and commits start being anchored.
        """
        if not os.environ.get(ENV_KEY):
            return None
        try:
            return cls.open(db_path, path=path)
        except AnchorError:
            return None

    # --------------------------------------------------------------- reading

    def records(self) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        out = []
        with io.open(self.path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                body, _, mac = line.rpartition("\t")
                if not body:
                    raise AnchorError(f"{self.path}:{lineno} is not an anchor line")
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    raise AnchorError(f"{self.path}:{lineno} is not readable")
                payload["_mac"] = mac
                payload["_line"] = lineno
                out.append(payload)
        return out

    def _authentic(self, records: List[dict]) -> None:
        """Every line signed, and every line pointing at the one before it."""
        prev = GENESIS
        for rec in records:
            body = {k: v for k, v in rec.items() if not k.startswith("_")}
            if not hmac.compare_digest(_mac(self.key, body), rec["_mac"]):
                raise AnchorError(
                    f"{self.path}:{rec['_line']} was not written with this key — "
                    "either the anchor was edited or the key is the wrong one"
                )
            if rec.get("prev") != prev:
                raise AnchorError(
                    f"{self.path}:{rec['_line']} does not follow the line before it; "
                    "a line has been removed or reordered"
                )
            prev = rec["_mac"]

    # --------------------------------------------------------------- writing

    def record(self, store: EventStore, branch: str = TRUNK,
               at: Optional[int] = None) -> dict:
        """Stamp the current head of a branch."""
        existing = self.records()
        self._authentic(existing)

        head = store.head(branch)
        own = [e for e in store.read(branch) if e.branch == branch]
        payload = {
            "v": VERSION,
            "seq": len(existing),
            "branch": branch,
            "events": len(own),
            "head": head.hash if head is not None else GENESIS,
            "ts": int(time.time()) if at is None else at,
            "prev": existing[-1]["_mac"] if existing else GENESIS,
        }
        line = canonical(payload) + "\t" + _mac(self.key, payload) + "\n"
        with io.open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
        return payload

    # -------------------------------------------------------------- checking

    def verify(self, store: EventStore, branch: str = TRUNK) -> dict:
        """Does the log still say what the anchor says it said?"""
        records = self.records()
        self._authentic(records)

        mine = [r for r in records if r["branch"] == branch]
        if not mine:
            return {"anchored": False, "branch": branch, "ok": True,
                    "why": "nothing has been anchored for this branch yet"}

        last = mine[-1]
        head = store.head(branch)
        own = [e for e in store.read(branch) if e.branch == branch]
        head_hash = head.hash if head is not None else GENESIS

        if len(own) < last["events"]:
            return {
                "anchored": True, "branch": branch, "ok": False,
                "why": (f"the log holds {len(own)} events on {branch} but the anchor "
                        f"recorded {last['events']}: history has been removed"),
                "anchored_at": last["ts"], "anchored_events": last["events"],
                "events": len(own),
            }

        if len(own) == last["events"] and head_hash != last["head"]:
            return {
                "anchored": True, "branch": branch, "ok": False,
                "why": (f"{branch} has the same number of events as the anchor but a "
                        f"different head: history has been rewritten"),
                "anchored_head": last["head"], "head": head_hash,
            }

        if len(own) > last["events"]:
            # Not tampering: work happened since the last stamp. Worth saying,
            # because an anchor that silently lags is an anchor nobody notices
            # has stopped being written.
            return {
                "anchored": True, "branch": branch, "ok": True, "behind": True,
                "why": (f"{len(own) - last['events']} events on {branch} since the last "
                        f"anchor; run `rehearsal anchor --write` to stamp them"),
                "anchored_events": last["events"], "events": len(own),
            }

        return {"anchored": True, "branch": branch, "ok": True, "why": "",
                "anchored_at": last["ts"], "events": len(own), "head": head_hash}


def check(store: EventStore, db_path: str, branch: str = TRUNK,
          key_path: Optional[str] = None, path: Optional[str] = None) -> Dict:
    """Chain plus anchor, in one answer, whether or not an anchor is configured."""
    out: Dict = {"branch": branch}
    try:
        out["events"] = store.verify(branch)
        out["chain_ok"] = True
        out["chain_why"] = ""
    except StoreError as exc:
        out["events"] = 0
        out["chain_ok"] = False
        out["chain_why"] = str(exc)

    anchor_path = path or default_path(db_path)
    configured = bool(key_path or os.environ.get(ENV_KEY))

    if not configured and not os.path.exists(anchor_path):
        out["mode"] = "chain only"
        out["anchor_ok"] = None
        out["anchor_why"] = (
            "no anchor configured: this detects edits and deletions, but not a "
            "rewrite that recomputes the hashes after it"
        )
        out["ok"] = out["chain_ok"]
        return out

    # "I cannot check" and "the check failed" are different answers and must never
    # be collapsed. A missing key is a setup problem; reporting it as tampering
    # teaches people that the alarm means nothing, which is how you end up with an
    # audit trail nobody looks at.
    try:
        key = load_key(key_path)
    except AnchorError as exc:
        out["mode"] = "chain only"
        out["anchor_ok"] = None
        out["anchor_why"] = (
            f"an anchor exists at {anchor_path} but it cannot be checked: {exc}"
            if os.path.exists(anchor_path) else str(exc)
        )
        out["ok"] = out["chain_ok"]
        return out

    out["mode"] = "chain + anchor"
    try:
        result = Anchor(anchor_path, key).verify(store, branch)
        out["anchor_ok"] = result["ok"]
        out["anchor_why"] = result.get("why", "")
        out["anchor_behind"] = result.get("behind", False)
    except AnchorError as exc:
        out["anchor_ok"] = False
        out["anchor_why"] = str(exc)

    out["ok"] = bool(out["chain_ok"]) and out["anchor_ok"] is not False
    return out
