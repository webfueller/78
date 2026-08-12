"""The anchor: the head, recorded where the log cannot reach.

The test that matters is `test_a_rewrite_with_recomputed_hashes_is_caught`.
Everything the chain can prove, it proves to itself — an attacker who edits an
event and then recomputes every hash after it produces a log that verifies
perfectly, and who can write the events table can write the checkpoints table.
That attack is why this module exists, and the test performs it.

The limits are tested too, deliberately: a matched rollback is *not* caught, and
a test says so rather than leaving somebody to find out.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from rehearsal import Kernel, Projection, anchor, audit, cli
from rehearsal.anchor import Anchor, AnchorError
from rehearsal.store import TRUNK, EventStore

SEEN = "thing.seen"
CHANGED = "thing.changed"


class Things(Projection):
    def __init__(self):
        super().__init__()
        self.things = {}

    def apply(self, ev):
        if ev.kind in (SEEN, CHANGED):
            self.things[ev.entity] = ev.payload.get("value", "")

    def shape(self):
        return {"things": dict(sorted(self.things.items()))}


class AnchorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.keys = tempfile.mkdtemp()          # elsewhere, as it should be
        self.db = os.path.join(self.tmp, "a.db")
        self.key_path = os.path.join(self.keys, "anchor.key")
        anchor.create_key(self.key_path)

        self.store = EventStore(self.db)
        self.store.append(branch=TRUNK, kind=SEEN, entity="alpha", actor="world",
                          ts=1000, payload={"value": "one"})
        self.anchor = Anchor.open(self.db, key_path=self.key_path)
        self.kernel = Kernel(projection=Things, anchor=self.anchor)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.keys, ignore_errors=True)

    def a_commit(self, value="two", at=2000):
        name = f"plan_{value}"
        self.store.fork(name, TRUNK)
        self.store.append(branch=name, kind=CHANGED, entity="alpha", actor="agent",
                          ts=at, payload={"value": value})
        return self.kernel.commit(self.store, name)

    def rewrite_history(self, forged="forged"):
        """The attack: change an event, then make the whole chain agree again.

        This is what somebody with write access to the file can do, and what the
        chain alone cannot see — afterwards `verify` passes and the checkpoint
        matches, because both were updated too.
        """
        from rehearsal.events import GENESIS, digest

        rows = list(self.store.db.execute(
            "SELECT * FROM events WHERE branch = ? ORDER BY gid", (TRUNK,)))
        prev = GENESIS
        for row in rows:
            payload = json.loads(row["payload"])
            if row["kind"] == CHANGED:
                payload = {"value": forged}
            body = json.dumps(payload, sort_keys=True)
            h = digest(prev, row["branch"], row["seq"], row["ts"], row["kind"],
                       row["entity"], row["actor"], payload)
            self.store.db.execute(
                "UPDATE events SET payload = ?, prev = ?, hash = ? WHERE gid = ?",
                (body, prev, h, row["gid"]))
            prev = h
        self.store.db.execute(
            "UPDATE checkpoints SET head_hash = ? WHERE branch = ?", (prev, TRUNK))


# ----------------------------------------------------------------------- keys


class TestTheKey(AnchorCase):
    def test_a_new_key_is_not_readable_by_anyone_else(self):
        mode = stat.S_IMODE(os.stat(self.key_path).st_mode)
        self.assertEqual(mode & 0o077, 0, f"mode is {mode:o}")

    def test_a_key_is_never_overwritten(self):
        """Overwriting one makes every line written under the old one unverifiable."""
        with self.assertRaises(AnchorError) as caught:
            anchor.create_key(self.key_path)
        self.assertIn("unverifiable", str(caught.exception))

    def test_a_missing_or_malformed_key_says_which(self):
        with self.assertRaises(AnchorError):
            anchor.load_key(os.path.join(self.keys, "nope.key"))

        junk = os.path.join(self.keys, "junk.key")
        with io.open(junk, "w", encoding="ascii") as fh:
            fh.write("not hex at all\n")
        with self.assertRaises(AnchorError) as caught:
            anchor.load_key(junk)
        self.assertIn("hex", str(caught.exception))

    def test_the_setup_is_criticised_when_it_deserves_it(self):
        beside = os.path.join(self.tmp, "beside.key")
        anchor.create_key(beside)
        os.chmod(beside, 0o644)
        notes = anchor.key_warnings(beside, self.db, anchor.default_path(self.db))
        self.assertTrue(any("readable by others" in n for n in notes))
        self.assertTrue(any("same directory" in n for n in notes))
        # And a properly placed key draws no complaints.
        self.assertEqual(anchor.key_warnings(self.key_path, self.db,
                                             anchor.default_path(self.db)), [])


# --------------------------------------------------------------- the guarantee


class TestWhatItCatches(AnchorCase):
    def test_a_commit_stamps_itself(self):
        receipt = self.a_commit()
        self.assertTrue(receipt["anchored"])
        records = self.anchor.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["branch"], TRUNK)
        self.assertTrue(self.anchor.verify(self.store)["ok"])

    def test_a_rewrite_with_recomputed_hashes_is_caught(self):
        """The attack the chain cannot see, and the reason this module exists."""
        self.a_commit(value="two")
        self.rewrite_history(forged="forged")

        # The log now lies convincingly to itself.
        self.assertGreater(self.store.verify(TRUNK), 0)
        self.assertEqual(
            Things.fold(self.store.read(TRUNK)).things["alpha"], "forged")

        out = self.anchor.verify(self.store)
        self.assertFalse(out["ok"])
        self.assertIn("rewritten", out["why"])

    def test_removing_history_is_caught_even_with_the_checkpoint_fixed(self):
        self.a_commit(value="two")
        self.store.db.execute("DELETE FROM events WHERE kind = ? AND branch = ?",
                              (CHANGED, TRUNK))
        remaining = list(self.store.db.execute(
            "SELECT hash FROM events WHERE branch = ? ORDER BY gid", (TRUNK,)))
        self.store.db.execute(
            "UPDATE checkpoints SET events = ?, head_hash = ? WHERE branch = ?",
            (len(remaining), remaining[-1]["hash"], TRUNK))

        out = self.anchor.verify(self.store)
        self.assertFalse(out["ok"])
        self.assertIn("removed", out["why"])

    def test_an_anchor_line_forged_without_the_key_is_caught(self):
        self.a_commit()
        with io.open(self.anchor.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"v": 1, "seq": 1, "branch": TRUNK, "events": 99,
                                 "head": "0" * 64, "ts": 1, "prev": "x"},
                                sort_keys=True, separators=(",", ":"))
                     + "\t" + "0" * 64 + "\n")
        with self.assertRaises(AnchorError) as caught:
            self.anchor.verify(self.store)
        self.assertIn("not written with this key", str(caught.exception))

    def test_a_removed_anchor_line_breaks_the_anchors_own_chain(self):
        self.a_commit(value="two")
        self.a_commit(value="three", at=3000)
        with io.open(self.anchor.path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        with io.open(self.anchor.path, "w", encoding="utf-8") as fh:
            fh.write(lines[-1] + "\n")          # keep only the last

        with self.assertRaises(AnchorError) as caught:
            self.anchor.verify(self.store)
        self.assertIn("does not follow", str(caught.exception))

    def test_the_wrong_key_is_told_apart_from_a_forgery(self):
        self.a_commit()
        other = os.path.join(self.keys, "other.key")
        anchor.create_key(other)
        wrong = Anchor.open(self.db, key_path=other)
        with self.assertRaises(AnchorError) as caught:
            wrong.verify(self.store)
        self.assertIn("the key is the wrong one", str(caught.exception))


class TestWhatItDoesNotCatch(AnchorCase):
    def test_a_matched_rollback_is_not_caught(self):
        """A documented limit, kept honest by a test that performs it.

        Truncate the log *and* remove the anchor lines written after that point,
        and what is left verifies. Detecting it needs memory outside both files —
        a backup of the anchor, or a witness who remembers the count.
        """
        self.a_commit(value="two")
        with io.open(self.anchor.path, encoding="utf-8") as fh:
            first_anchor = fh.read()
        state_then = Things.fold(self.store.read(TRUNK)).state_hash()

        self.a_commit(value="three", at=3000)
        self.assertNotEqual(Things.fold(self.store.read(TRUNK)).state_hash(), state_then)

        # Roll both back together.
        self.store.db.execute("DELETE FROM events WHERE gid > (SELECT MIN(gid) FROM "
                              "events WHERE kind = ? AND branch = ? AND payload LIKE ?)",
                              ("commit.sealed", TRUNK, '%"two"%'))
        rows = list(self.store.db.execute(
            "SELECT hash FROM events WHERE branch = ? ORDER BY gid", (TRUNK,)))
        self.store.db.execute(
            "UPDATE checkpoints SET events = ?, head_hash = ? WHERE branch = ?",
            (len(rows), rows[-1]["hash"], TRUNK))
        with io.open(self.anchor.path, "w", encoding="utf-8") as fh:
            fh.write(first_anchor)

        out = self.anchor.verify(self.store)
        self.assertTrue(out["ok"], "if this ever fails, the limit has been closed — "
                                   "update the documentation, it is good news")

    def test_a_log_ahead_of_its_anchor_is_reported_not_alarmed_about(self):
        """Work since the last stamp is normal; an anchor that has stopped is not."""
        self.a_commit(value="two")
        self.store.append(branch=TRUNK, kind=SEEN, entity="beta", actor="world",
                          ts=4000, payload={"value": "new"})

        out = self.anchor.verify(self.store)
        self.assertTrue(out["ok"])
        self.assertTrue(out["behind"])
        self.assertIn("since the last anchor", out["why"])


# ------------------------------------------------------------- what it reports


class TestHowItIsReported(AnchorCase):
    def test_the_audit_says_which_mode_it_is_in(self):
        self.a_commit()
        os.environ[anchor.ENV_KEY] = self.key_path
        self.addCleanup(os.environ.pop, anchor.ENV_KEY, None)
        text = audit.render_text(self.store)
        self.assertIn("chain + anchor", text)

        page = audit.render_html(self.store)
        self.assertIn("needs the key, not just write access", page)

    def test_without_an_anchor_the_weaker_guarantee_is_stated(self):
        bare_db = os.path.join(self.tmp, "bare.db")
        bare = EventStore(bare_db)
        bare.append(branch=TRUNK, kind=SEEN, entity="x", actor="world", ts=1,
                    payload={"value": "y"})
        try:
            text = audit.render_text(bare)
            self.assertIn("chain only", text)
            self.assertIn("not a rewrite that recomputes the hashes", text)
            self.assertIn("It is not a signature", audit.render_html(bare))
        finally:
            bare.close()

    def test_check_reports_both_halves(self):
        self.a_commit()
        out = anchor.check(self.store, self.db, key_path=self.key_path)
        self.assertTrue(out["chain_ok"])
        self.assertTrue(out["anchor_ok"])
        self.assertEqual(out["mode"], "chain + anchor")

        self.rewrite_history()
        out = anchor.check(self.store, self.db, key_path=self.key_path)
        self.assertTrue(out["chain_ok"])        # the chain is fooled
        self.assertFalse(out["anchor_ok"])      # the anchor is not
        self.assertFalse(out["ok"])


class TestTheCommand(AnchorCase):
    def test_init_write_show_and_verify(self):
        self.a_commit()
        self.store.close()

        self.assertEqual(cli.main(["--db", self.db, "anchor", "--show",
                                   "--key", self.key_path]), 0)
        self.assertEqual(cli.main(["--db", self.db, "verify", "--key", self.key_path]), 0)

        fresh = os.path.join(self.keys, "made-by-cli.key")
        self.assertEqual(cli.main(["--db", self.db, "anchor", "--init",
                                   "--key", fresh]), 0)
        self.assertTrue(os.path.exists(fresh))

        self.store = EventStore(self.db)

    def test_verify_exits_two_when_the_anchor_disagrees(self):
        self.a_commit()
        self.rewrite_history()
        self.store.close()

        self.assertEqual(cli.main(["--db", self.db, "verify", "--key", self.key_path]), 2)
        self.store = EventStore(self.db)

    def test_init_without_a_destination_explains_rather_than_guessing(self):
        env = os.environ.pop(anchor.ENV_KEY, None)
        try:
            self.assertEqual(cli.main(["--db", self.db, "anchor", "--init"]), 1)
        finally:
            if env is not None:
                os.environ[anchor.ENV_KEY] = env


class TestFromTheEnvironment(AnchorCase):
    def test_setting_one_variable_is_the_whole_setup(self):
        os.environ[anchor.ENV_KEY] = self.key_path
        try:
            found = Anchor.from_env(self.db)
            self.assertIsNotNone(found)
            self.assertEqual(found.path, anchor.default_path(self.db))
        finally:
            del os.environ[anchor.ENV_KEY]

        self.assertIsNone(Anchor.from_env(self.db))


if __name__ == "__main__":
    unittest.main(verbosity=2)
