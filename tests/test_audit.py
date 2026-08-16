"""The audit trail.

Every guarantee in this package is worth what somebody's ability to check it is
worth, so these tests are about whether the account is *true*, not whether it is
pretty: an undone commit must say it was undone, a commit must carry what it was
chosen over, and a tampered log must fail loudly rather than render nicely.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from takeback import Kernel, Projection, audit, cli
from takeback.store import TRUNK, EventStore

SEEN = "thing.seen"
CHANGED = "thing.changed"


def _fingerprint(path):
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class Things(Projection):
    def __init__(self):
        super().__init__()
        self.things = {}

    def apply(self, ev):
        if ev.kind in (SEEN, CHANGED):
            self.things[ev.entity] = ev.payload.get("value", "")

    def shape(self):
        return {"things": dict(sorted(self.things.items()))}


KERNEL = Kernel(
    projection=Things,
    prior={"good": 1.0, "bad": -1.0},
)


class AuditCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "a.db")
        self.store = EventStore(self.db)
        self.store.append(branch=TRUNK, kind=SEEN, entity="alpha", actor="world",
                          ts=1000, payload={"value": "one"})

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def a_choice(self, taken="bold", at=2000):
        """Two plans offered, one committed — the shape every domain produces."""
        for name in ("bold", "timid"):
            self.store.fork(f"plan_{name}", TRUNK, note=name)
            self.store.append(branch=f"plan_{name}", kind=CHANGED, entity="alpha",
                              actor="agent", ts=at, payload={"value": name})
        KERNEL.preferences.record_offer(self.store, "r_1", [
            {"branch": "plan_bold", "plan": "bold", "features": {"good": 2.0, "bad": 1.0}},
            {"branch": "plan_timid", "plan": "timid", "features": {"good": 1.0, "bad": 0.0}},
        ], at=at)
        return KERNEL.commit(self.store, f"plan_{taken}")


class TestTheAccount(AuditCase):
    def test_a_commit_carries_what_it_was_chosen_over(self):
        """The part of an audit trail that is hard to get any other way."""
        self.a_choice(taken="bold")
        row = audit.history(self.store)[0]
        self.assertEqual(row["state"], "sealed")
        self.assertEqual(row["chosen_over"], ["timid"])
        self.assertEqual([a["entity"] for a in row["actions"]], ["alpha"])
        self.assertNotEqual(row["state_before"], row["state_after"])

    def test_an_undone_commit_says_so_and_keeps_its_hashes(self):
        receipt = self.a_choice(taken="bold")
        KERNEL.undo(self.store, receipt["commit_id"])

        row = audit.history(self.store)[0]
        self.assertEqual(row["state"], "undone")
        self.assertTrue(row["undone_at"])
        # The record of what was almost done survives the undo.
        self.assertEqual([a["entity"] for a in row["actions"]], ["alpha"])

        text = audit.render_text(self.store)
        self.assertIn("UNDONE", text)
        self.assertIn("restored exactly", text)

    def test_the_account_of_an_empty_log_is_empty_not_broken(self):
        text = audit.render_text(self.store)
        self.assertIn("Nothing has been committed", text)
        self.assertIn("Chain: 1 events verified", text)

    def test_predictions_are_summarised_without_any_domain(self):
        """Claims are kernel events, so the auditor needs no resolvers."""
        for i, (p, outcome) in enumerate([(0.9, True), (0.8, True), (0.2, False)]):
            pid = f"p_{i}"
            self.store.root("ledger")
            self.store.append(branch="ledger", kind="prediction.made", entity=pid,
                              actor="agent", ts=1000 + i, allow_backdate=True,
                              payload={"claim": "x", "resolver": "r", "params": {},
                                       "p": p, "predictor": "t", "resolve_by": 9999})
            self.store.append(branch="ledger", kind="prediction.resolved", entity=pid,
                              actor="world", ts=1000 + i, allow_backdate=True,
                              payload={"outcome": outcome, "resolver": "r"})

        out = audit.claims(self.store)
        self.assertEqual(out["resolved"], 3)
        self.assertEqual(out["right"], 3)
        self.assertIn("beats the base rate", out["verdict"])
        self.assertIn("called correctly", audit.render_text(self.store))

    def test_a_tampered_log_fails_the_account_rather_than_decorating_it(self):
        self.a_choice(taken="bold")
        self.store.db.execute(
            "UPDATE events SET payload = ? WHERE kind = ? AND branch = ?",
            ('{"value": "forged"}', CHANGED, TRUNK),
        )
        out = audit.integrity(self.store)
        self.assertFalse(out["ok"])
        self.assertIn("tampered", out["why"])
        self.assertIn("Chain: FAILED", audit.render_text(self.store))


class TestTheHtml(AuditCase):
    def test_the_page_is_self_contained(self):
        """It argues an agent can be trusted; it should not phone home while doing so."""
        self.a_choice(taken="bold")
        page = audit.render_html(self.store)
        self.assertFalse(re.search(r"""(?:src|href)=["']?(?:https?:)?//""", page))
        self.assertNotIn("<script", page.lower())
        self.assertIn("<!doctype html>", page.lower())

    def test_the_page_survives_content_that_looks_like_markup(self):
        self.store.fork("plan_x", TRUNK)
        self.store.append(branch="plan_x", kind=CHANGED, entity="<script>alert(1)</script>",
                          actor="agent", ts=2000, payload={"value": "x"})
        KERNEL.commit(self.store, "plan_x")

        page = audit.render_html(self.store)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_a_domain_can_say_something_better_about_its_own_actions(self):
        self.a_choice(taken="bold")
        text = audit.render_text(
            self.store, describe=lambda a: f"set {a['entity']} to something new")
        self.assertIn("set alpha to something new", text)


class TestTheCommand(AuditCase):
    def test_audit_prints_and_writes_a_page(self):
        self.a_choice(taken="bold")
        self.store.close()

        out = os.path.join(self.tmp, "page.html")
        self.assertEqual(cli.main(["--db", self.db, "audit", "--html", out]), 0)
        self.assertTrue(os.path.getsize(out) > 500)
        self.assertEqual(cli.main(["--db", self.db, "audit"]), 0)
        self.assertEqual(cli.main(["--db", self.db, "audit", "--json"]), 0)

        self.store = EventStore(self.db)

    def test_verify_exits_non_zero_on_a_broken_chain(self):
        self.a_choice(taken="bold")
        self.assertEqual(cli.main(["--db", self.db, "verify"]), 0)

        self.store.db.execute("DELETE FROM events WHERE kind = ? AND branch = ?",
                              (CHANGED, TRUNK))
        self.store.close()
        self.assertEqual(cli.main(["--db", self.db, "verify"]), 2)
        self.store = EventStore(self.db)

    def test_the_auditor_cannot_write_to_the_log(self):
        """A tool you check the record with must not be able to alter it."""
        self.a_choice(taken="bold")
        self.store.close()

        before = (os.path.getsize(self.db), _fingerprint(self.db))
        for argv in (["audit"], ["audit", "--json"], ["verify"], ["branches"], ["log"]):
            cli.main(["--db", self.db] + argv)
        self.assertEqual((os.path.getsize(self.db), _fingerprint(self.db)), before)

        self.store = EventStore(self.db)


class TestTheFrontPage(unittest.TestCase):
    """The first code anybody sees has to run.

    A quickstart that does not work is worse than no quickstart: it is the one
    piece of the documentation a reader will definitely try, and the only one
    whose failure they will read as "this project does not work".
    """

    def test_the_install_line_names_the_thing_this_repository_builds(self):
        """The defect that forced a rename, turned into a test.

        The README said `pip install rehearsal` for months. That name belongs to
        somebody else on PyPI, so the one command on the front page would have
        installed a stranger's package — the worst possible first impression, and
        invisible to every test in the suite because none of them read the README.
        """
        import io as _io
        import re as _re

        with _io.open(os.path.join(ROOT_DIR, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        with _io.open(os.path.join(ROOT_DIR, "pyproject.toml"), encoding="utf-8") as fh:
            project = fh.read()

        declared = _re.search(r'^name\s*=\s*"([^"]+)"', project, _re.M).group(1)
        installs = set(_re.findall(r"pip install ([a-z][a-z0-9_-]*)\b", readme))
        installs.discard("-e")

        self.assertIn(declared, installs,
                      f"the README never says `pip install {declared}`")
        self.assertEqual(installs, {declared},
                         "the README offers an install name this repository does not build")
        self.assertTrue(os.path.isdir(os.path.join(ROOT_DIR, declared)),
                        f"{declared} is the distribution name but not a package here")

    def test_the_readme_example_runs(self):
        import io as _io
        import re as _re

        with _io.open(os.path.join(ROOT_DIR, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        blocks = _re.findall(r"```python\n(.*?)```", readme, _re.DOTALL)
        self.assertTrue(blocks, "the README has lost its example")

        tmp = tempfile.mkdtemp()
        try:
            source = blocks[0].replace('EventStore("tickets.db")',
                                       f'EventStore({os.path.join(tmp, "t.db")!r})')
            scope = {}
            exec(compile(source, "README.md", "exec"), scope)
            self.assertTrue(scope["receipt"]["commit_id"].startswith("c_"))
            self.assertEqual(scope["receipt"]["actions"], 3)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
