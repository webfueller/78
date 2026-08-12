"""Weights learned from what gets committed — and the gate that refuses to."""

from __future__ import annotations

import os
import random
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)                       # the engine
sys.path.insert(0, os.path.join(ROOT_DIR, "domains"))  # what is built on it

from preflight import commits, events as E, preferences as PF, rehearse, synthetic
from rehearsal.store import TRUNK, EventStore, StoreError

DAY = 24 * 3600


def option(name, **f):
    # Built from PF.KEYS rather than a hardcoded list, so a feature added to the
    # model cannot silently sit at its prior through every test that claims to
    # exercise the fit.
    base = {k: 0.0 for k in PF.KEYS}
    base.update(f)
    return {"branch": name, "plan": name, "features": base}


def choice(options, chosen):
    return {"rehearsal": "r", "options": options, "chosen": chosen, "at": 0}


class TestTheFit(unittest.TestCase):
    def test_it_moves_the_weight_that_explains_the_choices(self):
        # Options differ in one feature only, and the low-action one always wins.
        data = [
            choice([option("a", reply=1.0, per_action=1.0),
                    option("b", reply=1.0, per_action=6.0)], 0)
            for _ in range(30)
        ]
        w = PF.fit(data)
        self.assertLess(w["per_action"], PF.PRIOR["per_action"])
        for other in ("reply", "burn_saved_per_1000c", "late_surprise"):
            self.assertAlmostEqual(w[other], PF.PRIOR[other], places=2,
                                   msg=f"{other} moved but nothing in the data was about it")

    def test_every_feature_is_identifiable(self):
        # If a weight cannot move when the data is unambiguously about it, the
        # model is deaf on that axis and nobody would notice until it mattered.
        for key in PF.KEYS:
            worse, better = {key: 5.0}, {key: 0.0}
            data = [choice([option("hi", **worse), option("lo", **better)], 1) for _ in range(30)]
            w = PF.fit(data)
            self.assertLess(w[key], PF.PRIOR[key] - 0.05,
                            f"{key} did not respond to data that was entirely about it")

    def test_no_data_leaves_the_guess_alone(self):
        self.assertEqual(PF.fit([]), PF.PRIOR)

    def test_the_ridge_stops_one_choice_from_owning_a_weight(self):
        one = [choice([option("a", per_action=0.0), option("b", per_action=9.0)], 0)]
        w = PF.fit(one)
        self.assertLess(abs(w["per_action"] - PF.PRIOR["per_action"]), 0.35)


class TestTheGate(unittest.TestCase):
    def test_leave_one_out_does_not_let_the_fit_see_its_own_answer(self):
        data = [choice([option("a", reply=2.0), option("b", reply=0.0)], 0) for _ in range(12)]
        verdict = PF.evaluate(data)
        self.assertEqual(verdict["n"], 12)
        self.assertTrue(verdict["beats_prior"])

    def test_noise_produces_indifference_not_an_invented_preference(self):
        # A person choosing at random has no preference to find. The fit does beat
        # the guess here -- by flattening, because a confidently wrong prior loses
        # to near-uniform predictions -- and that is correct. What must not happen
        # is the product calling that "we learned what you want".
        rng = random.Random(7)
        data = []
        for _ in range(24):
            # Random across everything the model looks at: a person with no
            # preference has none about any of it.
            opts = [option(f"o{i}", **{k: rng.random() * 3 for k in PF.KEYS})
                    for i in range(4)]
            data.append(choice(opts, rng.randrange(4)))

        verdict = PF.evaluate(data)
        fitted = PF.fit(data)
        self.assertLessEqual(verdict["learned_top1"], verdict["chance_top1"] + 0.05,
                             "no preference exists, so it must not predict better than chance")
        self.assertLess(PF._norm(fitted), PF._norm(PF.PRIOR) * 0.6,
                        "the honest response to noise is to become less confident")

    def test_a_choice_between_one_thing_is_not_a_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(os.path.join(tmp, "p.db"))
            PF.record_offer(store, "r1", [option("only")], at=100)
            store.append(branch=PF.PREFERENCES, kind=PF.CHOSEN, entity="r1",
                         actor=E.ACTOR_USER, ts=101, allow_backdate=True,
                         payload={"branch": "only", "plan": "only"})
            self.assertEqual(PF.choices(store), [])
            store.close()

    def test_the_same_rehearsal_is_offered_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(os.path.join(tmp, "p.db"))
            opts = [option("a"), option("b")]
            self.assertTrue(PF.record_offer(store, "r1", opts, at=100))
            self.assertFalse(PF.record_offer(store, "r1", opts, at=100))
            store.close()


class TestTheLoop(unittest.TestCase):
    """End to end: rehearse, decide, and see whether the scoring learns."""

    @classmethod
    def setUpClass(cls):
        cls.truth = {"reply": 1.2, "per_action": -0.35,
                     "burn_saved_per_1000c": 1.4, "late_surprise": -0.6}
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = EventStore(os.path.join(cls.tmp.name, "loop.db"))
        synthetic.seed_world(cls.store, days=200, seed=5)
        cls.states = []
        for _ in range(22):
            ts = cls.store.now(TRUNK) + 6 * DAY
            cls.store.append(branch=TRUNK, kind=E.MESSAGE_RECEIVED, entity=f"th_n{ts}",
                             actor=E.ACTOR_WORLD, ts=ts,
                             payload={"sender": "ana.reyes", "counterparty": "ana.reyes",
                                      "subject": "x", "body": "y"})
            m = rehearse.rehearse(cls.store)
            if len(m["plans"]) < 2:
                continue
            pick = max(m["plans"], key=lambda p: PF.utility(p["features"], cls.truth))
            branch = pick["branches"][0]["id"]
            if pick["actions"]:
                commits.commit(cls.store, branch)
            else:
                PF.decline(cls.store, branch)
            cls.states.append((len(PF.choices(cls.store)), m["weights_from"]["source"]))

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.tmp.cleanup()

    def test_it_will_not_learn_before_it_has_enough_to_learn_from(self):
        early = [src for n, src in self.states if n < PF.MIN_CHOICES]
        self.assertTrue(early)
        self.assertEqual(set(early), {"hand-picked"})

    def test_it_learns_once_it_does(self):
        _, provenance = PF.effective_weights(self.store)
        self.assertEqual(provenance["source"], "learned")
        self.assertTrue(provenance["beats_prior"])
        self.assertGreater(provenance["learned_top1"], provenance["prior_top1"])

    def test_the_learned_weights_point_the_right_way(self):
        learned, _ = PF.effective_weights(self.store)
        for key in ("reply", "burn_saved_per_1000c"):
            self.assertGreater(learned[key], PF.PRIOR[key],
                               f"{key} matters more to this person than the guess assumed")
        self.assertLess(learned["per_action"], PF.PRIOR["per_action"])

    def test_doing_nothing_is_recorded_as_a_decision(self):
        held = [c for c in PF.choices(self.store)
                if c["options"][c["chosen"]]["plan"] == "hold"]
        self.assertTrue(held, "a week left alone must still count as a choice")

    def test_declining_executes_nothing(self):
        trunk_kinds = {e.kind for e in self.store.read(TRUNK)}
        declined = [b["name"] for b in self.store.branches() if b["status"] == "declined"]
        self.assertTrue(declined)
        for name in declined:
            self.assertEqual(commits.promotable(self.store, name), [])
        self.assertNotIn(PF.CHOSEN, trunk_kinds)  # the ledger is not the world

    def test_a_plan_with_nothing_to_do_cannot_be_committed(self):
        m = rehearse.rehearse(self.store)
        hold = next(p for p in m["plans"] if p["id"] == "hold")
        with self.assertRaises(StoreError) as caught:
            commits.commit(self.store, hold["branches"][0]["id"])
        self.assertIn("decline", str(caught.exception))

    def test_the_payload_says_where_its_weights_came_from(self):
        m = rehearse.rehearse(self.store)
        self.assertIn("weights_from", m)
        self.assertIn(m["weights_from"]["source"], ("hand-picked", "learned"))
        self.assertTrue(m["weights_from"]["why"])
        self.assertEqual(set(m["weights"]), set(PF.KEYS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
