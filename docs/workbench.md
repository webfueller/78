# workbench

An agent edits your files. You read what it wants to do first, and you can take
it back.

This is the second domain built on [the kernel](kernel.md), and the first place
in the repository where committing does something the database cannot undo by
itself. That is the point of it: until a commit reached outside the log, "atomic,
previewable, reversible" was a property of a toy.

```
observe → propose → (read the preview) → commit → check → undo, if you must
```

## The loop, on a real directory

```bash
workbench --db wb.db --root repo observe
```

Every text file under `repo/` is now in the log, contents and all. That is what
makes the undo exact later: there is no separate backup to go stale, because the
log *is* the restore point.

An agent writes its version of the tree into a staging directory. Nothing has
touched `repo/` yet.

```bash
workbench --db wb.db --root repo propose --from-dir staged
```

```
change set w_7954be1eaad3 | background risk 0.0417
  hold         utility -0.125  red 0.042  []
  apply        utility  0.269  red 0.185  ['Rewrite src.py']
recommended: apply
```

Still nothing on disk. The proposals live on a fork; `red` is the probability the
checks go red within the window, measured from this repository's own history of
edits followed by red builds. Both numbers are claims on the ledger, written down
before anyone knows the answer.

```bash
workbench --db wb.db --root repo commit w_7954be1eaad3_apply
```

Now the file is written, and there is a receipt: `state_before`, `state_after`,
the files touched, and `undo_until`. The `state_after` is the same hash the
preview projected — what you read is what happened.

```bash
workbench --db wb.db --root repo check --command "python3 tests/test_src.py"
# ok: false | code: 1

workbench --db wb.db --root repo undo c_e56a27017ad6dd3d
# restored: true | disk_matches: true | files_restored: ["src.py"]
```

The bytes are back, the tests pass again, and the log still holds every version
of what was almost done.

## What it refuses

**A file somebody edited behind its back.** Before writing anything, a commit
checks that the files it is about to touch still match what the preview was
computed against. If they do not, it refuses and names them. Committing anyway
would overwrite a human's work with a plan made before that work existed — and
the undo would then "restore" a version that never ran.

**A path leaving the root.** Absolute paths, `..` segments, and symlinks pointing
outside are rejected in one place, before anything is read. A symlink is never
followed and never managed: it is a pointer, not a file, and restoring one is not
the same as restoring what it points at.

**A file it cannot put back.** Over 1 MiB, or not UTF-8, and the workbench will
not manage it. Keeping a lossy copy of a file and then offering to restore it
would be worse than declining.

**Doing the same thing twice.** Committing a change set that was already
committed fails, from the kernel, for the same reason two sibling futures in the
mail product cannot both send the same message.

## What it claims, and how it gets marked

Two resolvers, both answerable from the log alone:

- `check_fails` — did the checks go red in the window after this landed?
- `rewritten_within` — did this file need touching again soon after? Churn is the
  honest proxy for "the edit was not right".

`workbench check` runs your command, records the verdict, and settles every claim
whose due date has passed. `workbench score` prints the Brier score against a
leave-one-out base rate. The risk numbers are not decoration: if they never beat
the base rate, they are theatre, and the scoreboard is where that shows up.

## The thing that had to be got right

Doing nothing is not a guaranteed green build. Builds go red because of somebody
else's commit, a flaky test, an expired token. `background_risk` measures how
often the checks failed when nothing had changed, and every plan carries it —
including `hold`.

Without that, `hold` scores a perfect zero risk and every plan that touches a
file is charged for failures it had no part in. It is the same mistake as
penalising the one plan that tries to protect a meeting for the meeting moving
anyway, which this repository already made once, in the mail product, and had to
undo.

## Limits worth knowing

**One directory, one database, one writer.** SQLite with `BEGIN IMMEDIATE`. Two
workbenches on the same tree is not a supported thing.

**A commit that dies between the disk write and the seal.** The disk write is
deliberately the last step that can fail in a way the log would not notice, and
`disk.apply` puts back what it changed when it does. If a machine loses power in
the gap, the log rolls back and the disk may not — which is exactly what the
drift check on the next commit is there to find and say out loud.

**The risk model is a base rate, not attribution.** When a change set touches six
files and the build goes red, it does not know which file did it, and it does not
pretend to. What it reports is "changes to this file have gone wrong this often",
which is a different and more defensible claim.

## Tests

```bash
python3 -m unittest discover -s tests -p "test_workbench.py"
```

26 tests. The interesting ones are the failures: a write that dies half way
(neither disk nor log moves), a file edited behind its back (refused, work
untouched), and four ways a path might try to leave the room.
