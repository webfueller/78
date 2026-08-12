"""The risk numbers, and whether they know anything.

`experiment-004` is the measurement; this is the part of it that has to keep
being true. Three of these tests exist because the first version of the
experiment got them wrong: evidence thrown away with the warm-up, a rate shrunk
toward a constant instead of the population, and a predictor allowed to see
edits whose windows had not closed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)                       # the engine
sys.path.insert(0, os.path.join(ROOT_DIR, "domains"))  # what is built on it

from takeback.store import TRUNK, EventStore

from workbench import backtest, churn, gitlog, synthetic
from workbench.state import Tree

DAY = 24 * 3600


def touches(store, rows):
    with store.transaction():
        for ts, path in rows:
            store.append(branch=TRUNK, kind="file.touched", entity=path,
                         actor="world", ts=ts, payload={"commit": "x", "author": "t"})


class ChurnCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = EventStore(os.path.join(self.tmp, "c.db"))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tree(self):
        return Tree.fold(self.store.read(TRUNK))


class TestTheWalk(ChurnCase):
    def test_an_edit_too_near_the_end_is_not_scored(self):
        """Otherwise 'was it touched again' measures where the export stopped."""
        start = 1_000_000
        touches(self.store, [
            (start, "a.py"),
            (start + 2 * DAY, "a.py"),
            (start + 20 * DAY, "b.py"),      # nothing after it for a week
        ])
        got = churn.moments(self.tree(), horizon=7 * DAY)
        self.assertEqual([m["path"] for m in got], ["a.py", "a.py"])
        self.assertEqual([m["churned"] for m in got], [True, False])

    def test_evidence_is_not_thrown_away_with_the_warm_up(self):
        """An early edit is poor material to be scored on and fine to learn from."""
        start = 1_000_000
        rows = []
        for i in range(40):
            rows.append((start + i * DAY, "hot.py"))
            rows.append((start + i * DAY + DAY // 2, "hot.py"))
        for i in range(40):
            rows.append((start + i * 3 * DAY, "cold.py"))
        touches(self.store, sorted(rows))

        out = backtest.run(self.store, predictor="per-path", horizon=2 * DAY)
        self.assertGreater(out["n"], 0)
        # The warm-up removes scored moments, never the history behind them.
        everything = churn.moments(self.tree(), horizon=2 * DAY)
        self.assertGreater(len(everything), out["n"])

    def test_a_predictor_cannot_see_a_window_that_is_still_open(self):
        start = 1_000_000
        touches(self.store, [
            (start, "a.py"), (start + DAY, "a.py"),
            (start + 30 * DAY, "a.py"),
        ])
        t = self.tree()
        # At the moment of the third edit, only the first edit's window has
        # closed -- the second one's answer was still in the future.
        past = [m for m in churn.moments(t, 7 * DAY) if m["at"] + 7 * DAY <= start + 30 * DAY]
        self.assertEqual(len(past), 2)
        self.assertLessEqual(max(m["at"] for m in past), start + DAY)


class TestTheModels(ChurnCase):
    def test_a_rate_is_shrunk_toward_the_population_not_a_constant(self):
        past = [{"at": 0, "path": "other.py", "churned": True} for _ in range(50)]
        # Everything churns; a file nobody has seen should inherit that, not 0.25.
        self.assertGreater(churn.per_path(past, "new.py", 7 * DAY), 0.8)

        quiet = [{"at": 0, "path": "other.py", "churned": False} for _ in range(50)]
        self.assertLess(churn.per_path(quiet, "new.py", 7 * DAY), 0.2)

    def test_the_hierarchy_inherits_a_directory_that_carries_signal(self):
        past = (
            [{"at": 0, "path": f"src/{i}.py", "churned": True} for i in range(20)]
            + [{"at": 0, "path": f"docs/{i}.md", "churned": False} for i in range(20)]
        )
        hot = churn.hierarchical(past, "src/brand_new.py", 7 * DAY)
        cold = churn.hierarchical(past, "docs/brand_new.md", 7 * DAY)
        self.assertGreater(hot, 0.6)
        self.assertLess(cold, 0.4)
        # A per-path model has nothing to say about a file it has never seen.
        self.assertAlmostEqual(
            churn.per_path(past, "src/brand_new.py", 7 * DAY),
            churn.per_path(past, "docs/brand_new.md", 7 * DAY),
            places=6,
        )

    def test_the_shipped_estimate_is_the_model_the_backtest_scored(self):
        self.assertEqual(churn.DEFAULT, "hierarchical")
        self.assertIn(churn.DEFAULT, backtest.REGISTRY)
        from workbench import propose
        touches(self.store, [(1_000_000 + i * DAY, "a.py") for i in range(20)])
        t = self.tree()
        self.assertAlmostEqual(
            propose.churn_risk(t, "a.py", at=1_000_000 + 30 * DAY),
            churn.estimate(t, "a.py", at=1_000_000 + 30 * DAY),
            places=9,
        )


class TestAgainstAnAnswerKey(ChurnCase):
    def test_the_harness_recovers_a_ranking_it_was_handed(self):
        """If it cannot pass a test it wrote itself, its verdict on real history is noise."""
        synthetic.seed_repo(self.store, days=800, paths=24, seed=3)
        out = backtest.compare(self.store, horizon=7 * DAY)

        self.assertGreater(out["global"]["n"], 400)
        # A constant scored against a constant should land on the baseline.
        self.assertLess(abs(out["global"]["lift"]), 0.02)
        # Per-path knowledge is worth something, because it was built in.
        self.assertGreater(out["per-path"]["lift"], 0.02)
        # Directories were assigned round-robin, so they carry nothing.
        self.assertLess(out["per-dir"]["lift_vs_global"], 0.01)
        # And the shipped model captures the signal that is actually there.
        self.assertGreater(out["hierarchical"]["lift_vs_global"], 0.02)

    def test_a_history_too_short_to_judge_says_so(self):
        touches(self.store, [(1_000_000, "a.py"), (1_000_000 + DAY, "a.py")])
        out = backtest.run(self.store, horizon=30 * DAY)
        self.assertEqual(out["n"], 0)
        self.assertIn("verdict", out)


class TestGitImport(ChurnCase):
    def _repo(self):
        root = os.path.join(self.tmp, "repo")
        os.makedirs(root)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, env=env)
        # Three commits: the first adds two files, the next two revise one of
        # them, so the history has both an untouched file and a churning one.
        plan = [["a.py", "b.py"], ["a.py"], ["a.py"]]
        for i, paths in enumerate(plan):
            for path in paths:
                with open(os.path.join(root, path), "w") as fh:
                    fh.write(f"x = {i}\n")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True, env=env)
            subprocess.run(
                ["git", "commit", "-q", "-m", f"c{i}", "--date", f"2024-01-0{i + 1}T10:00:00"],
                cwd=root, check=True,
                env=dict(env, GIT_COMMITTER_DATE=f"2024-01-0{i + 1}T10:00:00"),
            )
        return root

    def test_a_repositorys_history_comes_in_as_evidence_not_as_bytes(self):
        root = self._repo()
        info = gitlog.ingest(self.store, root)
        self.assertEqual(info["commits"], 3)
        self.assertEqual(info["touches"], 4)   # a.py three times, b.py once

        t = self.tree()
        self.assertEqual([w["path"] for w in t.writes], ["a.py", "b.py", "a.py", "a.py"])
        # Crucially: no contents, and therefore nothing claimed to be restorable.
        self.assertEqual(t.files, {})
        self.assertEqual(t.managed(), [])

    def test_importing_twice_does_not_double_the_history(self):
        root = self._repo()
        gitlog.ingest(self.store, root)
        second = gitlog.ingest(self.store, root)
        self.assertEqual(second["touches"], 0)
        self.assertEqual(second["skipped"], 4)
        self.assertEqual(len(self.tree().writes), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
