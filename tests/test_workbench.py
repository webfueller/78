"""The workbench: the first commit in this repository that leaves the database.

Everything the kernel promised about a log -- atomic, previewable, reversible --
is easy while nothing outside the log moves. These tests are about the moment it
does. The interesting ones are the failures: a write that dies half way, a file
somebody edited behind the workbench's back, and a path trying to leave the room.
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rehearsal.store import TRUNK, EventStore, StoreError

from workbench import checks, commits, disk, observe, propose
from workbench.propose import Edit
from workbench.state import Tree

HOUR = 3600


def write(root, path, text):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with io.open(full, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def read(root, path):
    full = os.path.join(root, path)
    if not os.path.exists(full):
        return None
    with io.open(full, encoding="utf-8", newline="") as fh:
        return fh.read()


class WorkbenchCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(self.root)
        write(self.root, "app.py", "print('one')\n")
        write(self.root, "lib/util.py", "VALUE = 1\n")
        write(self.root, "README.md", "# repo\n")
        self.store = EventStore(os.path.join(self.tmp, "wb.db"))
        observe.observe(self.store, self.root, at=1000)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tree(self):
        return Tree.fold(self.store.read(TRUNK))

    def rehearse(self, edits, now=2000, **kw):
        return propose.rehearse(self.store, edits, now=now, **kw)


# ------------------------------------------------------------------ the basics


class TestObserving(WorkbenchCase):
    def test_the_log_holds_every_managed_file(self):
        t = self.tree()
        self.assertEqual(sorted(t.files), ["README.md", "app.py", "lib/util.py"])
        self.assertEqual(t.files["lib/util.py"]["content"], "VALUE = 1\n")

    def test_observing_twice_records_nothing_the_second_time(self):
        again = observe.observe(self.store, self.root, at=1001)
        self.assertEqual(again, {"added": [], "changed": [], "gone": []})

    def test_observing_picks_up_an_outside_edit_deliberately(self):
        write(self.root, "app.py", "print('two')\n")
        changed = observe.observe(self.store, self.root, at=1002)
        self.assertEqual(changed["changed"], ["app.py"])
        self.assertEqual(self.tree().files["app.py"]["content"], "print('two')\n")

    def test_a_removed_file_is_recorded_as_deleted(self):
        os.unlink(os.path.join(self.root, "README.md"))
        changed = observe.observe(self.store, self.root, at=1003)
        self.assertEqual(changed["gone"], ["README.md"])
        self.assertNotIn("README.md", self.tree().files)


# ------------------------------------------------------ preview, commit, undo


class TestTheRoundTrip(WorkbenchCase):
    def test_a_rehearsal_writes_nothing_to_disk(self):
        self.rehearse([Edit("app.py", "print('rehearsed')\n")])
        self.assertEqual(read(self.root, "app.py"), "print('one')\n")
        self.assertEqual(self.tree().files["app.py"]["content"], "print('one')\n")

    def test_committing_writes_the_files_and_the_receipt_agrees(self):
        plan = self.rehearse([
            Edit("app.py", "print('two')\n"),
            Edit("lib/new.py", "NEW = True\n"),
        ])
        branch = next(p["branch"] for p in plan["plans"] if p["id"] == "apply")
        rehearsed_hash = next(p["state_hash"] for p in plan["plans"] if p["id"] == "apply")

        receipt = commits.commit(self.store, branch, self.root)

        self.assertEqual(read(self.root, "app.py"), "print('two')\n")
        self.assertEqual(read(self.root, "lib/new.py"), "NEW = True\n")
        self.assertEqual(receipt["files"], ["app.py", "lib/new.py"])
        # What you previewed is what happened, hash for hash.
        self.assertEqual(receipt["state_after"], rehearsed_hash)
        self.assertFalse(disk.drift(self.root, self.tree()))

    def test_undo_puts_the_bytes_back_on_disk(self):
        before = {p: read(self.root, p) for p in ("app.py", "lib/util.py")}
        plan = self.rehearse([
            Edit("app.py", "print('two')\n"),
            Edit("lib/util.py", None),
            Edit("lib/new.py", "NEW = True\n"),
        ])
        branch = next(p["branch"] for p in plan["plans"] if p["id"] == "apply")
        receipt = commits.commit(self.store, branch, self.root)

        self.assertIsNone(read(self.root, "lib/util.py"))
        self.assertEqual(read(self.root, "lib/new.py"), "NEW = True\n")

        out = commits.undo(self.store, receipt["commit_id"], self.root)

        self.assertTrue(out["restored"])          # the log agrees
        self.assertTrue(out["disk_matches"])      # and so does the disk
        self.assertEqual(read(self.root, "app.py"), before["app.py"])
        self.assertEqual(read(self.root, "lib/util.py"), before["lib/util.py"])
        self.assertIsNone(read(self.root, "lib/new.py"))  # a created file is un-created

    def test_the_same_change_set_cannot_be_committed_twice(self):
        plan = self.rehearse([Edit("app.py", "print('two')\n")])
        branch = next(p["branch"] for p in plan["plans"] if p["id"] == "apply")
        commits.commit(self.store, branch, self.root)
        with self.assertRaises(StoreError):
            commits.commit(self.store, branch, self.root)

    def test_hold_is_offered_and_cannot_be_committed(self):
        plan = self.rehearse([Edit("app.py", "print('two')\n")])
        hold = next(p for p in plan["plans"] if p["id"] == "hold")
        self.assertEqual(hold["actions"], [])
        with self.assertRaises(StoreError):
            commits.commit(self.store, hold["branch"], self.root)


# -------------------------------------------------------------- the hard parts


class TestWhenItGoesWrong(WorkbenchCase):
    def test_a_write_that_dies_half_way_leaves_neither_disk_nor_log_changed(self):
        """The whole reason the execute hook runs inside the transaction."""
        plan = self.rehearse([
            Edit("app.py", "print('two')\n"),
            Edit("lib/util.py", "VALUE = 2\n"),
            Edit("README.md", "# rewritten\n"),
        ])
        branch = next(p["branch"] for p in plan["plans"] if p["id"] == "apply")
        before_hash = self.tree().state_hash()
        before_files = {p: read(self.root, p) for p in ("app.py", "lib/util.py", "README.md")}

        real = disk._write_atomic
        calls = {"n": 0}

        def explode(full, content):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("no space left on device")
            return real(full, content)

        disk._write_atomic = explode
        try:
            with self.assertRaises(OSError):
                commits.commit(self.store, branch, self.root)
        finally:
            disk._write_atomic = real

        for path, content in before_files.items():
            self.assertEqual(read(self.root, path), content, path)
        self.assertEqual(self.tree().state_hash(), before_hash)
        self.assertEqual(self.tree().commits, {})
        self.assertFalse(disk.drift(self.root, self.tree()))
        # And the branch is still open, so it can be committed once there is room.
        self.assertEqual(self.store.branch(branch)["status"], "open")

    def test_a_file_edited_behind_the_workbenchs_back_refuses_the_commit(self):
        plan = self.rehearse([Edit("app.py", "print('two')\n")])
        branch = next(p["branch"] for p in plan["plans"] if p["id"] == "apply")

        write(self.root, "app.py", "print('a human was here')\n")

        with self.assertRaises(StoreError) as caught:
            commits.commit(self.store, branch, self.root)
        self.assertIn("changed on disk", str(caught.exception))
        # The human's work is untouched.
        self.assertEqual(read(self.root, "app.py"), "print('a human was here')\n")

    def test_drift_names_what_disagrees(self):
        write(self.root, "app.py", "print('elsewhere')\n")
        os.unlink(os.path.join(self.root, "README.md"))
        found = {d["path"]: d["why"] for d in disk.drift(self.root, self.tree())}
        self.assertIn("changed on disk", found["app.py"])
        self.assertIn("missing from disk", found["README.md"])
        self.assertNotIn("lib/util.py", found)

    def test_undo_does_not_tidy_files_it_never_managed(self):
        plan = self.rehearse([Edit("app.py", "print('two')\n")])
        branch = next(p["branch"] for p in plan["plans"] if p["id"] == "apply")
        receipt = commits.commit(self.store, branch, self.root)

        write(self.root, "notes.txt", "mine, not yours\n")
        commits.undo(self.store, receipt["commit_id"], self.root)
        self.assertEqual(read(self.root, "notes.txt"), "mine, not yours\n")


class TestTheFence(WorkbenchCase):
    def test_a_path_cannot_climb_out(self):
        for bad in ("../escape.txt", "lib/../../escape.txt", "/etc/passwd", "~/escape"):
            with self.assertRaises(disk.DiskError, msg=bad):
                disk.resolve(self.root, bad)

    def test_a_symlink_cannot_be_used_as_a_door(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.root, "door"))
        with self.assertRaises(disk.DiskError):
            disk.resolve(self.root, "door/secret.txt")

    def test_a_symlink_is_not_scanned_as_a_file(self):
        target = os.path.join(self.tmp, "secret.txt")
        with io.open(target, "w", encoding="utf-8") as fh:
            fh.write("not yours\n")
        os.symlink(target, os.path.join(self.root, "link.txt"))
        self.assertNotIn("link.txt", disk.scan(self.root))

    def test_a_file_it_cannot_restore_is_not_managed(self):
        big = os.path.join(self.root, "big.txt")
        with io.open(big, "w", encoding="utf-8") as fh:
            fh.write("x" * (disk.MAX_BYTES + 1))
        with io.open(os.path.join(self.root, "blob.bin"), "wb") as fh:
            fh.write(b"\xff\xfe\x00binary")

        found = disk.scan(self.root)
        self.assertNotIn("big.txt", found)
        self.assertNotIn("blob.bin", found)
        with self.assertRaises(disk.DiskError):
            disk.read(self.root, "big.txt")
        with self.assertRaises(disk.DiskError):
            disk.read(self.root, "blob.bin")


# ------------------------------------------------------------ the ledger loop


class TestTheLedger(WorkbenchCase):
    def test_risk_is_measured_from_this_repositorys_own_history(self):
        fresh = propose.risk(self.tree(), "app.py")
        self.assertAlmostEqual(fresh, propose.PRIOR_FAILS / propose.PRIOR_N, places=6)

        # Three edits to app.py, each followed by a red build.
        ts = 2000
        for i in range(3):
            self.store.append(branch=TRUNK, kind="file.written", entity="app.py",
                              actor="agent", ts=ts + i * 100,
                              payload={"sha256": disk.sha(str(i)), "content": str(i)})
            checks.report(self.store, command="pytest", ok=False, at=ts + i * 100 + 50)

        learned = propose.risk(self.tree(), "app.py")
        self.assertGreater(learned, fresh)
        self.assertLess(propose.risk(self.tree(), "README.md"), learned)

    def test_a_claim_is_made_before_the_checks_run_and_scored_after(self):
        plan = self.rehearse([Edit("app.py", "print('two')\n")], horizon_days=1)
        branch = next(p["branch"] for p in plan["plans"] if p["id"] == "apply")
        commits.commit(self.store, branch, self.root)

        # Hold claims the build goes red anyway; apply claims that and the churn.
        self.assertEqual(len(checks.pending(self.store)), 3)

        checks.report(self.store, command="pytest", ok=False, at=2000 + 2 * HOUR)
        checks.report(self.store, command="pytest", ok=False, at=2000 + 25 * HOUR)

        out = checks.score(self.store)
        self.assertGreater(out["n"], 0)
        red = [r for r in out["per_resolver"].get("check_fails", {}).items()]
        self.assertTrue(red)

    def test_the_risky_plan_and_the_quiet_plan_are_scored_differently(self):
        ts = 2000
        for i in range(4):
            self.store.append(branch=TRUNK, kind="file.written", entity="app.py",
                              actor="agent", ts=ts + i * 100,
                              payload={"sha256": disk.sha(str(i)), "content": str(i)})
            checks.report(self.store, command="pytest", ok=False, at=ts + i * 100 + 50)

        plan = self.rehearse([
            Edit("app.py", "print('risky')\n"),
            Edit("README.md", "# quiet\n"),
        ], now=3000)
        ids = {p["id"] for p in plan["plans"]}
        self.assertIn("apply_safe", ids)

        apply_all = next(p for p in plan["plans"] if p["id"] == "apply")
        quiet = next(p for p in plan["plans"] if p["id"] == "apply_safe")
        self.assertGreater(apply_all["expected"]["check_risk"], quiet["expected"]["check_risk"])
        self.assertEqual([a["entity"] for a in quiet["actions"]], ["README.md"])

    def test_doing_nothing_is_not_a_guaranteed_green_build(self):
        """Risk the change set did not cause belongs to every plan, including hold.

        Otherwise the do-nothing plan is credited with preventing failures it has
        no power over, and every plan that touches a file is charged for them.
        """
        plan = self.rehearse([Edit("app.py", "print('two')\n")])
        hold = next(p for p in plan["plans"] if p["id"] == "hold")
        apply_all = next(p for p in plan["plans"] if p["id"] == "apply")

        self.assertGreater(hold["expected"]["check_risk"], 0.0)
        self.assertEqual(hold["expected"]["check_risk"], plan["background_risk"])
        self.assertGreater(apply_all["expected"]["check_risk"], hold["expected"]["check_risk"])
        # And it is a claim on the ledger like any other, not a footnote.
        self.assertEqual(len(hold["uncertain"]), 1)

    def test_the_background_rate_is_measured_when_there_is_history(self):
        ts = 2000
        for i in range(6):
            checks.report(self.store, command="pytest", ok=(i % 2 == 0), at=ts + i * 100)
        measured = propose.background_risk(self.tree())
        self.assertAlmostEqual(measured, (3 + propose.BG_FAILS) / (6 + propose.BG_N), places=6)

    def test_the_weights_start_as_a_guess_and_say_so(self):
        plan = self.rehearse([Edit("app.py", "print('two')\n")])
        self.assertEqual(plan["weights_from"]["source"], "hand-picked")
        self.assertIn("not enough to learn from yet", plan["weights_from"]["why"])
        self.assertEqual(sorted(plan["weights"]), ["applied", "check_risk", "churn", "per_action"])

    def test_committing_a_plan_records_which_one_was_chosen(self):
        plan = self.rehearse([Edit("app.py", "print('two')\n")])
        branch = next(p["branch"] for p in plan["plans"] if p["id"] == "apply")
        receipt = commits.commit(self.store, branch, self.root)
        self.assertEqual(receipt["learned_from"], plan["change_set"])


class TestTheRunner(WorkbenchCase):
    def test_a_real_command_is_run_and_its_verdict_recorded(self):
        good = checks.run(self.store, self.root, "python3 -c \"print('fine')\"")
        self.assertTrue(good["ok"])

        bad = checks.run(self.store, self.root, "python3 -c \"raise SystemExit(3)\"")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["code"], 3)

        t = self.tree()
        self.assertEqual([c["ok"] for c in t.checks], [True, False])

    def test_a_command_that_does_not_exist_is_an_error_not_a_red_build(self):
        with self.assertRaises(ValueError):
            checks.run(self.store, self.root, "definitely-not-a-real-command --version")
        self.assertEqual(self.tree().checks, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
