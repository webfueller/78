"""Where the log meets the filesystem.

This is the only module that writes anything outside the database, and it is
therefore the only place where a mistake is not reversible by replaying events.
Three rules it enforces rather than documents:

  A path is inside the root or it does not exist. Absolute paths, `..`, and
  symlinks pointing out are refused before anything is read, not after.

  A write is atomic per file: a temporary file next to the target, then a
  rename. A crash leaves the old bytes, never half the new ones.

  A batch that fails part way puts back what it changed. The database
  transaction rolls back on its own; the disk has no such thing, so the journal
  below is it.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

MAX_BYTES = 1 << 20          # 1 MiB per file
IGNORE_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache"}


class DiskError(RuntimeError):
    pass


def sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------- the fence


def resolve(root: str, path: str) -> str:
    """The absolute path this relative path means, or an error.

    Everything downstream trusts the string it gets back, so the checks belong
    here and nowhere else. A path is rejected if it is absolute, if it climbs out
    with `..`, or if any component is a symlink leading outside the root -- the
    last one matters because an agent that can write a file can write a symlink,
    and `link -> /etc` would otherwise turn a confined write into an unconfined
    one on the *next* commit.
    """
    if not path or path != path.strip():
        raise DiskError(f"unusable path: {path!r}")
    if os.path.isabs(path) or (os.name == "nt" and os.path.splitdrive(path)[0]):
        raise DiskError(f"path must be relative to the workbench root: {path!r}")
    if path.startswith("~"):
        raise DiskError(f"path must be relative to the workbench root: {path!r}")

    parts = path.replace("\\", "/").split("/")
    if any(p in ("..", "") or p == "." for p in parts):
        raise DiskError(f"path must not contain '.' or '..' segments: {path!r}")

    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, path))
    if full != base and not full.startswith(base + os.sep):
        raise DiskError(f"path escapes the workbench root: {path!r}")
    return full


def relative(root: str, full: str) -> str:
    return os.path.relpath(full, os.path.realpath(root)).replace(os.sep, "/")


# -------------------------------------------------------------------- reading


def read(root: str, path: str) -> Optional[str]:
    """The file's text, or None if it is not there.

    Refuses what it cannot faithfully put back: anything over the size cap, and
    anything that is not UTF-8. Storing a lossy copy of a file and then offering
    to restore it would be worse than not managing it at all.
    """
    full = resolve(root, path)
    if not os.path.isfile(full):
        return None
    size = os.path.getsize(full)
    if size > MAX_BYTES:
        raise DiskError(
            f"{path} is {size} bytes; the workbench keeps every version in the log and "
            f"caps a file at {MAX_BYTES} bytes so that stays affordable"
        )
    with open(full, "rb") as fh:
        raw = fh.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise DiskError(
            f"{path} is not UTF-8 text; the workbench will not manage a file it cannot "
            "restore byte for byte"
        )


def scan(root: str, ignore: Iterable[str] = ()) -> Dict[str, str]:
    """Every managed-able file under the root, path -> content."""
    base = os.path.realpath(root)
    skip = set(ignore) | IGNORE_DIRS
    out: Dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue  # a symlink is a pointer, not a file; it is not ours to restore
            rel = relative(base, full)
            try:
                content = read(base, rel)
            except DiskError:
                continue  # too big, or not text: not managed, and not an error to walk past
            if content is not None:
                out[rel] = content
    return out


def on_disk(root: str, paths: Sequence[str]) -> Dict[str, Optional[str]]:
    return {p: read(root, p) for p in paths}


# -------------------------------------------------------------------- writing


def _write_atomic(full: str, content: str) -> None:
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(full) or ".", prefix=".wb-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp, full)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def apply(root: str, changes: Sequence[Tuple[str, Optional[str]]]) -> List[str]:
    """Write or delete, all of them or none of them.

    `changes` is (path, content) with content None meaning delete. On any failure
    every file already touched is put back the way it was and the exception is
    re-raised, because this runs inside a database transaction that is about to
    roll back and a disk that disagrees with the log is the one state this whole
    design exists to prevent.
    """
    journal: List[Tuple[str, Optional[str]]] = []
    touched: List[str] = []
    try:
        for path, content in changes:
            full = resolve(root, path)
            journal.append((path, read(root, path)))
            if content is None:
                if os.path.exists(full):
                    os.unlink(full)
            else:
                _write_atomic(full, content)
            touched.append(path)
        return touched
    except BaseException:
        for path, previous in reversed(journal):
            try:
                full = resolve(root, path)
                if previous is None:
                    if os.path.exists(full):
                        os.unlink(full)
                else:
                    _write_atomic(full, previous)
            except Exception:
                # Nothing useful left to do: the caller is already failing, and
                # swallowing this would be the difference between a bad commit
                # and a silent one. The drift check finds what is left over.
                pass
        raise


def drift(root: str, tree, paths: Optional[Sequence[str]] = None) -> List[dict]:
    """Paths where the disk and the log disagree.

    Anything in here means somebody edited a managed file outside the workbench.
    Committing over that would overwrite their work with a preview computed
    before it existed, and the undo would then "restore" a version that never
    ran. So it is a refusal, not a warning.
    """
    wanted = list(paths) if paths is not None else tree.managed()
    out = []
    for path in wanted:
        expected = tree.files.get(path)
        try:
            actual = read(root, path)
        except DiskError as exc:
            out.append({"path": path, "why": str(exc)})
            continue
        if expected is None and actual is not None:
            out.append({"path": path, "why": "on disk but deleted in the log"})
        elif expected is not None and actual is None:
            out.append({"path": path, "why": "missing from disk"})
        elif expected is not None and actual is not None and sha(actual) != expected["sha256"]:
            out.append({"path": path, "why": "changed on disk since the workbench last looked"})
    return out
