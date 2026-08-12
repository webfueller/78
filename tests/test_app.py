"""What the product claims, stated as things that can fail."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)                       # the engine
sys.path.insert(0, os.path.join(ROOT_DIR, "domains"))  # what is built on it

from preflight import commits, events as E, ingest, predictions as P, rehearse, server, synthetic
from preflight.rehearse import Uncertainty
from takeback.store import TRUNK, EventStore, StoreError
from preflight.world import project

DAY = 24 * 3600
HERE = os.path.dirname(os.path.abspath(__file__))


def _u(p: float, resolver: str = "reply_within", **kw) -> Uncertainty:
    return Uncertainty(resolver=resolver, params={"thread": "t"}, p=p, describe="x",
                       entity="t", contact="c", at=100, resolve_by=200, **kw)


class TestFutures(unittest.TestCase):
    def test_the_count_distribution_is_a_real_distribution(self):
        unc = [_u(p) for p in (0.9, 0.5, 0.2, 0.05, 0.7)]
        futures = rehearse.enumerate_futures(unc, keep=len(unc) + 1)
        self.assertAlmostEqual(sum(f["p"] for f in futures), 1.0, places=9)

    def test_the_shown_future_for_a_count_is_the_likeliest_one(self):
        unc = [_u(0.9), _u(0.1), _u(0.6)]
        one = next(f for f in rehearse.enumerate_futures(unc, keep=4) if f["count"] == 1)
        self.assertEqual(one["outcomes"], [True, False, False])  # the 0.9, not the 0.1

    def test_five_futures_cover_most_of_the_mass(self):
        # The reason for counting instead of enumerating combinations: with eight
        # open questions there are 256 of them and the top five are a rounding error.
        unc = [_u(p) for p in (0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1)]
        shown = sum(f["p"] for f in rehearse.enumerate_futures(unc, keep=5))
        self.assertGreater(shown, 0.85)

    def test_no_uncertainty_means_one_certain_future(self):
        self.assertEqual(rehearse.enumerate_futures([]), [{"outcomes": [], "p": 1.0, "count": 0}])


class TestRehearsal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = EventStore(os.path.join(cls.tmp.name, "r.db"))
        synthetic.seed_world(cls.store, days=200, seed=5)
        cls.map = rehearse.rehearse(cls.store)
        cls.plans = {p["id"]: p for p in cls.map["plans"]}

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.tmp.cleanup()

    def test_the_same_rehearsal_twice_is_the_same_rehearsal(self):
        again = rehearse.rehearse(self.store)
        self.assertEqual(again["rehearsal"], self.map["rehearsal"])
        self.assertEqual(
            [b["state_hash"] for p in again["plans"] for b in p["branches"]],
            [b["state_hash"] for p in self.map["plans"] for b in p["branches"]],
        )

    def test_doing_nothing_is_always_on_the_table(self):
        self.assertIn("hold", self.plans)
        self.assertEqual(self.plans["hold"]["actions"], [])

    def test_doing_nothing_is_not_free(self):
        # The calendar risk belongs to every plan, including the one that ignores
        # it. Loading it only onto the plan that addresses it would make the plan
        # that addresses it look the worst.
        self.assertGreater(self.plans["hold"]["expected"]["late_surprises"], 0)

    def test_pre_confirming_moves_the_surprise_not_the_meeting(self):
        base, defend = self.plans["prune"], self.plans["defend"]
        self.assertAlmostEqual(
            base["expected"]["meetings_moved"], defend["expected"]["meetings_moved"], places=6,
            msg="defending must not invent a reduction in how often people move meetings",
        )
        self.assertLess(defend["expected"]["late_surprises"], base["expected"]["late_surprises"])

    def test_chasing_selectively_costs_less_and_gets_most_of_it(self):
        every, some = self.plans["chase"], self.plans["chase_likely"]
        self.assertLess(len(some["actions"]), len(every["actions"]))
        self.assertGreater(
            some["expected"]["replies"] / every["expected"]["replies"], 0.6,
            "half the messages should still be collecting most of the replies",
        )

    def test_every_plan_records_its_claims(self):
        claims = [r for r in P.ledger(self.store).values()
                  if r["predictor"].startswith("takeback/")]
        self.assertGreaterEqual(len(claims), sum(len(p["uncertain"]) for p in self.map["plans"]))
        for r in claims:
            self.assertGreater(r["resolve_by"], r["made_at"])

    def test_futures_cover_most_of_the_probability(self):
        for p in self.map["plans"]:
            self.assertGreater(p["coverage"], 0.85, f"{p['id']} shows too little of the mass")

    def test_simulated_replies_exist_in_the_fork(self):
        best = self.plans[self.map["recommended"]]
        busiest = max(best["branches"], key=lambda b: b["metrics"]["replies"])
        actors = {e.actor for e in self.store.read(busiest["id"]) if e.branch == busiest["id"]}
        self.assertTrue(any(a.startswith(E.SIM_PREFIX) for a in actors))


class TestCommittingAFuture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(os.path.join(self.tmp.name, "c.db"))
        synthetic.seed_world(self.store, days=200, seed=5)
        self.map = rehearse.rehearse(self.store)
        self.plan = {p["id"]: p for p in self.map["plans"]}[self.map["recommended"]]

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_committing_a_future_runs_the_plan_not_the_fiction(self):
        branch = self.plan["branches"][0]["id"]
        rehearsed = project(self.store.read(branch), include_simulated=False).state_hash()
        receipt = commits.commit(self.store, branch)

        self.assertEqual(receipt["actions"], len(self.plan["actions"]))
        self.assertEqual(
            receipt["state_after"], rehearsed,
            "what happened must equal what was rehearsed, minus the invented parts",
        )
        trunk = self.store.read(TRUNK)
        self.assertFalse(any(e.simulated for e in trunk))

    def test_the_full_rehearsal_is_not_what_gets_committed(self):
        # The simulated replies are precisely the part that does not come true,
        # so the product must never claim the whole fork was reproduced.
        branch = self.plan["branches"][0]["id"]
        whole = project(self.store.read(branch)).state_hash()
        receipt = commits.commit(self.store, branch)
        self.assertNotEqual(receipt["state_after"], whole)

    def test_picking_a_second_future_cannot_send_the_same_message_twice(self):
        commits.commit(self.store, self.plan["branches"][0]["id"])
        with self.assertRaises(StoreError) as caught:
            commits.commit(self.store, self.plan["branches"][1]["id"])
        self.assertIn("already executed", str(caught.exception))

    def test_undo_restores_the_world(self):
        receipt = commits.commit(self.store, self.plan["branches"][0]["id"])
        self.assertTrue(commits.undo(self.store, receipt["commit_id"])["restored"])


class TestApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(os.path.join(self.tmp.name, "s.db"))
        synthetic.seed_world(self.store, days=120, seed=5)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_the_world_payload_is_what_a_person_recognises(self):
        w = server.world_payload(self.store)
        self.assertTrue(w["threads"] and w["subscriptions"])
        for t in w["threads"]:
            self.assertGreaterEqual(t["waiting_days"], 0)
            self.assertTrue(t["name"])

    def test_a_branch_payload_shows_the_actions_and_flags_the_fiction(self):
        m = rehearse.rehearse(self.store)
        plan = {p["id"]: p for p in m["plans"]}[m["recommended"]]
        best = max(plan["branches"], key=lambda b: b["metrics"]["replies"])
        payload = server.branch_payload(self.store, best["id"])

        self.assertEqual(payload["committable"], len(plan["actions"]))
        self.assertTrue(any(e["simulated"] for e in payload["timeline"]))
        self.assertTrue(any(not e["simulated"] for e in payload["timeline"]))
        self.assertNotEqual(payload["state_hash"], payload["state_hash_real"])


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(os.path.join(self.tmp.name, "i.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_an_mbox_becomes_a_conversation(self):
        rows = ingest.read_mbox(os.path.join(HERE, "fixtures", "sample.mbox"), ["me@example.net"])
        ingest.ingest(self.store, rows)
        w = project(self.store.read(TRUNK))

        self.assertEqual(len(w.threads), 1, "a reply chain is one thread, not six")
        t = next(iter(w.threads.values()))
        self.assertEqual(len(t.messages), 6)
        self.assertEqual({m["direction"] for m in t.messages}, {"in", "out"})
        self.assertEqual(t.counterparty, "ana@example.net")

    def test_an_ics_becomes_meetings(self):
        rows = ingest.read_ics(os.path.join(HERE, "fixtures", "sample.ics"))
        ingest.ingest(self.store, rows)
        w = project(self.store.read(TRUNK))
        self.assertEqual(len(w.meetings), 3)
        for m in w.meetings.values():
            self.assertIn("me", m.attendees)
            self.assertGreater(m.end, m.start)

    def test_imported_events_keep_world_time_in_order(self):
        rows = (ingest.read_mbox(os.path.join(HERE, "fixtures", "sample.mbox"), ["me@example.net"])
                + ingest.read_ics(os.path.join(HERE, "fixtures", "sample.ics")))
        ingest.ingest(self.store, rows)
        self.assertGreater(self.store.verify(TRUNK), 5)
        stamps = [e.ts for e in self.store.read(TRUNK)]
        self.assertEqual(stamps, sorted(stamps))

    def test_a_real_twin_can_be_rehearsed(self):
        rows = (ingest.read_mbox(os.path.join(HERE, "fixtures", "sample.mbox"), ["me@example.net"])
                + ingest.read_ics(os.path.join(HERE, "fixtures", "sample.ics")))
        ingest.ingest(self.store, rows)
        m = rehearse.rehearse(self.store)
        self.assertTrue(m["plans"])
        self.assertTrue(m["recommended"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
