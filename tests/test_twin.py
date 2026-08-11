"""What weeks 1-2 claim, stated as things that can fail."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from preflight import backtest, commits, events as E, predictions as P, synthetic
from rehearsal.store import TRUNK, EventStore, StoreError
from preflight.world import project

DAY = 24 * 3600


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def seeded(self, days=120, seed=7):
        synthetic.seed_world(self.store, days=days, seed=seed)
        return self.store


class TestLog(Base):
    def test_chain_verifies(self):
        self.seeded(days=40)
        self.assertGreater(self.store.verify(TRUNK), 100)

    def test_tampering_is_detected(self):
        self.seeded(days=20)
        self.store.db.execute(
            "UPDATE events SET payload = ? WHERE gid = (SELECT MIN(gid) FROM events WHERE kind = ?)",
            ('{"sender":"forged","counterparty":"forged","subject":"x","body":"x"}', E.MESSAGE_RECEIVED),
        )
        self.store.db.commit()
        with self.assertRaises(StoreError):
            self.store.verify(TRUNK)

    def test_world_time_cannot_run_backwards(self):
        self.seeded(days=10)
        with self.assertRaises(StoreError):
            self.store.append(
                branch=TRUNK, kind=E.MESSAGE_SENT, entity="th_0001", actor=E.ACTOR_USER,
                ts=0, payload={"sender": "me", "counterparty": "ana.reyes"},
            )

    def test_simulated_actors_are_refused_on_the_trunk(self):
        self.seeded(days=10)
        with self.assertRaises(StoreError):
            self.store.append(
                branch=TRUNK, kind=E.MESSAGE_RECEIVED, entity="th_0001",
                actor="sim:ana.reyes", ts=self.store.now(TRUNK),
                payload={"sender": "ana.reyes", "body": "invented"},
            )


class TestReplay(Base):
    def test_replay_is_deterministic(self):
        self.seeded(days=60)
        a = project(self.store.read(TRUNK)).state_hash()
        b = project(self.store.read(TRUNK)).state_hash()
        self.assertEqual(a, b)

    def test_replay_survives_a_reopen(self):
        self.seeded(days=60)
        a = project(self.store.read(TRUNK)).state_hash()
        path = self.store.path
        self.store.close()
        self.store = EventStore(path)
        self.assertEqual(project(self.store.read(TRUNK)).state_hash(), a)

    def test_rewind_yields_a_smaller_past(self):
        self.seeded(days=60)
        now = self.store.now(TRUNK)
        self.store.fork("past", TRUNK, at_ts=now - 30 * DAY)
        past = project(self.store.read("past"))
        present = project(self.store.read(TRUNK))
        self.assertLess(past.applied, present.applied)
        self.assertLessEqual(past.clock, now - 30 * DAY)


class TestFork(Base):
    def _fork_with_work(self):
        self.seeded(days=60)
        now = self.store.now(TRUNK)
        self.store.fork("wk", TRUNK)
        thread = next(iter(project(self.store.read("wk")).threads))
        self.store.append(
            branch="wk", kind=E.MESSAGE_SENT, entity=thread, actor=E.ACTOR_AGENT,
            ts=now + 60, payload={"sender": "me", "counterparty": "ana.reyes",
                                  "subject": "x", "body": "proposed by the agent"},
        )
        self.store.append(
            branch="wk", kind=E.MESSAGE_RECEIVED, entity=thread, actor="sim:ana.reyes",
            ts=now + 120, payload={"sender": "ana.reyes", "counterparty": "ana.reyes",
                                   "subject": "x", "body": "invented reply"},
        )
        return now, thread

    def test_a_fork_does_not_touch_the_trunk(self):
        before = None
        self.seeded(days=60)
        before = project(self.store.read(TRUNK)).state_hash()
        self.store.fork("wk", TRUNK)
        self.store.append(
            branch="wk", kind=E.MESSAGE_SENT, entity="th_0001", actor=E.ACTOR_AGENT,
            ts=self.store.now(TRUNK) + 60,
            payload={"sender": "me", "counterparty": "ana.reyes"},
        )
        self.assertEqual(project(self.store.read(TRUNK)).state_hash(), before)

    def test_forks_are_isolated_from_each_other(self):
        self.seeded(days=40)
        now = self.store.now(TRUNK)
        self.store.fork("a", TRUNK)
        self.store.fork("b", TRUNK)
        self.store.append(branch="a", kind=E.CALENDAR_CANCELLED, entity="mt_010",
                          actor=E.ACTOR_AGENT, ts=now + 10, payload={})
        self.assertNotEqual(
            project(self.store.read("a")).state_hash(),
            project(self.store.read("b")).state_hash(),
        )

    def test_commit_promotes_only_agent_authored_events(self):
        now, thread = self._fork_with_work()
        receipt = commits.commit(self.store, "wk")
        self.assertEqual(receipt["actions"], 1)

        trunk = project(self.store.read(TRUNK))
        bodies = [m["body"] for m in trunk.threads[thread].messages]
        self.assertIn("proposed by the agent", bodies)
        self.assertNotIn("invented reply", bodies)
        self.assertFalse(any(m["simulated"] for m in trunk.threads[thread].messages))

    def test_undo_restores_the_previous_world_exactly(self):
        self._fork_with_work()
        receipt = commits.commit(self.store, "wk")
        self.assertNotEqual(receipt["state_before"], receipt["state_after"])
        result = commits.undo(self.store, receipt["commit_id"])
        self.assertTrue(result["restored"])
        self.assertEqual(result["state_hash"], receipt["state_before"])

    def test_undo_refuses_once_the_window_closes(self):
        self._fork_with_work()
        receipt = commits.commit(self.store, "wk")
        late = receipt["undo_until"] + 1
        with self.assertRaises(StoreError):
            commits.undo(self.store, receipt["commit_id"], at_ts=late)

    def test_undone_history_is_kept(self):
        self._fork_with_work()
        receipt = commits.commit(self.store, "wk")
        commits.undo(self.store, receipt["commit_id"])
        kinds = [e.kind for e in self.store.read(TRUNK)]
        self.assertIn(E.COMMIT_SEALED, kinds)
        self.assertIn(E.COMMIT_UNDONE, kinds)
        self.assertEqual(project(self.store.read(TRUNK)).commits[receipt["commit_id"]]["state"], "undone")


class TestLedger(Base):
    def test_a_claim_is_scored_against_the_trunk(self):
        self.seeded(days=60)
        now = self.store.now(TRUNK)
        w = project(self.store.read(TRUNK))
        thread = next(iter(w.threads.values()))
        pid = P.record(
            self.store, origin_branch=TRUNK, resolver="reply_within",
            params={"thread": thread.id, "contact": thread.counterparty},
            p=0.7, claim="test", made_at=now - 10 * DAY, resolve_by=now - 8 * DAY,
            predictor="test",
        )
        settled = P.resolve_due(self.store, now=now)
        self.assertEqual([s["id"] for s in settled], [pid])
        self.assertIn(P.ledger(self.store)[pid]["outcome"], (True, False))

    def test_the_ledger_never_touches_the_world(self):
        self.seeded(days=40)
        before = project(self.store.read(TRUNK)).state_hash()
        w = project(self.store.read(TRUNK))
        thread = next(iter(w.threads.values()))
        P.record(self.store, origin_branch=TRUNK, resolver="reply_within",
                 params={"thread": thread.id, "contact": thread.counterparty},
                 p=0.5, claim="test", made_at=self.store.now(TRUNK) - DAY,
                 resolve_by=self.store.now(TRUNK), predictor="test")
        P.resolve_due(self.store)
        self.assertEqual(project(self.store.read(TRUNK)).state_hash(), before)

    def test_a_claim_must_be_falsifiable(self):
        self.seeded(days=20)
        with self.assertRaises(ValueError):
            P.record(self.store, origin_branch=TRUNK, resolver="reply_within",
                     params={"thread": "th_0001", "contact": "ana.reyes"}, p=0.5,
                     claim="x", made_at=100, resolve_by=100, predictor="test")
        with self.assertRaises(ValueError):
            P.record(self.store, origin_branch=TRUNK, resolver="nonsense",
                     params={}, p=0.5, claim="x", made_at=100, resolve_by=200,
                     predictor="test")

    def test_scoring_a_perfect_and_a_hopeless_predictor(self):
        pairs_good = [(1.0, True)] * 5 + [(0.0, False)] * 5
        pairs_bad = [(0.0, True)] * 5 + [(1.0, False)] * 5
        self.assertEqual(P.brier(pairs_good), 0.0)
        self.assertEqual(P.brier(pairs_bad), 1.0)

    def test_base_rate_does_not_peek_at_its_own_answer(self):
        rates = P.leave_one_out_base_rates([True, True, False, False])
        self.assertEqual(rates, [1 / 3, 1 / 3, 2 / 3, 2 / 3])


class TestBacktest(Base):
    """The instrument has to rank predictors correctly on lift it can verify.

    The synthetic world gives every contact a different reply rate and latency,
    so the true ordering is known in advance: knowing the contact beats knowing
    nothing, and knowing the contact plus how long they have already kept you
    waiting beats both. If the scoreboard cannot recover an ordering that is
    designed into the data, it cannot be trusted to judge a real model.
    """

    # A backtest walks the whole holdout, so it is built once for the class
    # rather than once per assertion.
    tmp = None
    store = None
    results = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = EventStore(os.path.join(cls.tmp.name, "b.db"))
        synthetic.seed_world(cls.store, days=400, seed=11)
        cls.results = {
            name: backtest.run(cls.store, predictor=name, holdout_days=120)
            for name in ("global", "per-contact", "per-contact-age")
        }

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.tmp.cleanup()

    def setUp(self):  # the class owns the fixture; do not build a per-test one
        pass

    def tearDown(self):
        pass

    def brier(self, name):
        return self.results[name]["score"]["brier"]

    def test_every_predictor_is_judged_on_the_same_claims(self):
        counts = {n: r["claims_made"] for n, r in self.results.items()}
        self.assertEqual(len(set(counts.values())), 1, f"unequal evaluation sets: {counts}")
        self.assertGreater(
            self.results["global"]["claims_made"], 50, "too few claims to conclude anything"
        )

    def test_better_specified_models_score_better(self):
        self.assertLess(self.brier("per-contact"), self.brier("global"))
        self.assertLess(self.brier("per-contact-age"), self.brier("per-contact"))

    def test_a_constant_predictor_does_not_beat_the_constant_baseline(self):
        # `global` is a single number applied to everyone; the leave-one-out base
        # rate is the same idea. Reporting lift here would mean the scoreboard is
        # rewarding noise.
        self.assertLessEqual(self.results["global"]["score"]["lift"], 0.05)

    def test_the_well_specified_model_clears_the_kill_criterion(self):
        best = self.results["per-contact-age"]["score"]
        self.assertGreater(best["lift"], 0.2, "known signal is not showing up as lift")
        self.assertEqual(best["verdict"], "beats baseline")

    def test_a_backtest_cannot_see_past_its_own_fork(self):
        result = self.results["per-contact"]
        forked = project(self.store.read("backtest_per-contact_120d"))
        self.assertLessEqual(forked.clock, result["holdout_from"])
        self.assertLessEqual(result["holdout_from"], result["now"] - 120 * DAY)

    def test_only_settled_windows_are_evaluated(self):
        now = self.results["global"]["now"]
        for rec in P.ledger(self.store).values():
            self.assertLessEqual(rec["resolve_by"], now)
            self.assertIsNotNone(rec["outcome"])


class TestLedgerTiming(Base):
    def test_claims_that_reality_has_not_reached_are_not_scored(self):
        self.seeded(days=120)
        now = self.store.now(TRUNK)
        w = project(self.store.read(TRUNK))
        P.record(self.store, origin_branch=TRUNK, resolver="reply_within",
                 params={"thread": next(iter(w.threads)), "contact": "ana.reyes"},
                 p=0.5, claim="future", made_at=now, resolve_by=now + 10 * DAY,
                 predictor="test")
        self.assertEqual(P.resolve_due(self.store, now=now), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
