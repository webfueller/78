"""The kernel, exercised by a domain that has never heard of email.

If `takeback` is really a standalone package, then a second domain -- built here
in eighty lines, about deploying software, sharing not one concept with a
mailbox -- should get the log, the fork, the receipt, the undo, the ledger and
the learned weights for free. Everything below is that claim, made mechanical.

The last test is the one that keeps it true: the kernel may not mention the
product.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)                       # the engine
sys.path.insert(0, os.path.join(ROOT_DIR, "domains"))  # what is built on it

import takeback
from takeback import Kernel, Projection
from takeback.futures import count_distribution, enumerate_futures
from takeback.store import TRUNK, EventStore, StoreError

HOUR = 3600
ROOT = ROOT_DIR


# --------------------------------------------------------------- a tiny domain

SERVICE_SEEN = "fleet.service_seen"
RELEASE_CUT = "fleet.release_cut"
DEPLOYED = "fleet.deployed"
PAGED = "fleet.paged"


class Fleet(Projection):
    """Services, the release each is running, and who got woken up."""

    def __init__(self) -> None:
        super().__init__()
        self.services = {}

    def apply(self, ev):
        p = ev.payload
        if ev.kind == SERVICE_SEEN:
            self.services.setdefault(
                ev.entity, {"running": p.get("running", ""), "pending": [], "pages": []}
            )
        elif ev.kind == RELEASE_CUT:
            s = self.services.get(p["service"])
            if s is not None:
                s["pending"].append(ev.entity)
        elif ev.kind == DEPLOYED:
            s = self.services.get(ev.entity)
            if s is not None:
                s["running"] = p["release"]
                s["pending"] = [r for r in s["pending"] if r != p["release"]]
        elif ev.kind == PAGED:
            s = self.services.get(ev.entity)
            if s is not None:
                s["pages"].append(ev.ts)

    def shape(self):
        return {"services": {k: v for k, v in sorted(self.services.items())}}


def pages_within(f: Fleet, rec: dict) -> bool:
    s = f.services.get(rec["params"]["service"])
    if s is None:
        return False
    return any(rec["made_at"] < ts <= rec["resolve_by"] for ts in s["pages"])


PRIOR = {"shipped": 1.0, "pages": -2.0}

KERNEL = Kernel(projection=Fleet, resolvers={"pages_within": pages_within}, prior=PRIOR)


def a_fleet(store: EventStore) -> None:
    store.append(branch=TRUNK, kind=SERVICE_SEEN, entity="checkout", actor="world",
                 ts=1000, payload={"running": "v1"})
    store.append(branch=TRUNK, kind=SERVICE_SEEN, entity="search", actor="world",
                 ts=1000, payload={"running": "v3"})
    store.append(branch=TRUNK, kind=RELEASE_CUT, entity="v2", actor="world",
                 ts=2000, payload={"service": "checkout"})


class KernelCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = EventStore(os.path.join(self.dir, "fleet.db"))
        a_fleet(self.store)

    def tearDown(self):
        self.store.close()


# ------------------------------------------------------------------ the claims


class TestForeignDomain(KernelCase):
    def test_a_domain_that_is_not_mail_gets_a_projection_and_a_hash(self):
        f = KERNEL.project(self.store.read(TRUNK))
        self.assertEqual(f.services["checkout"]["running"], "v1")
        self.assertEqual(f.services["checkout"]["pending"], ["v2"])
        self.assertEqual(len(f.state_hash()), 64)

    def test_undo_restores_a_foreign_world_exactly(self):
        """The property the whole product rests on, on a domain it was not built for."""
        before = KERNEL.project(self.store.read(TRUNK)).state_hash()

        self.store.fork("plan", TRUNK, note="ship v2")
        self.store.append(branch="plan", kind=DEPLOYED, entity="checkout", actor="agent",
                          ts=3000, payload={"release": "v2"})

        receipt = KERNEL.commit(self.store, "plan")
        after = KERNEL.project(self.store.read(TRUNK))
        self.assertEqual(after.services["checkout"]["running"], "v2")
        self.assertNotEqual(receipt["state_after"], before)

        out = KERNEL.undo(self.store, receipt["commit_id"])
        self.assertTrue(out["restored"])
        self.assertEqual(out["state_hash"], before)
        # And nothing was deleted to achieve it.
        self.assertGreater(self.store.verify(TRUNK), 5)

    def test_a_simulated_actor_cannot_reach_a_foreign_trunk(self):
        with self.assertRaises(StoreError):
            self.store.append(branch=TRUNK, kind=PAGED, entity="checkout",
                              actor="sim:pagerduty", ts=4000, payload={})

    def test_only_the_agents_proposals_are_promoted(self):
        self.store.fork("plan", TRUNK)
        self.store.append(branch="plan", kind=DEPLOYED, entity="checkout", actor="agent",
                          ts=3000, payload={"release": "v2"})
        self.store.append(branch="plan", kind=PAGED, entity="checkout", actor="sim:oncall",
                          ts=3600, payload={})

        KERNEL.commit(self.store, "plan")
        trunk = KERNEL.project(self.store.read(TRUNK))
        self.assertEqual(trunk.services["checkout"]["running"], "v2")
        self.assertEqual(trunk.services["checkout"]["pages"], [])  # the simulation stayed home

    def test_the_same_action_cannot_be_committed_twice(self):
        self.store.fork("plan", TRUNK)
        self.store.append(branch="plan", kind=DEPLOYED, entity="checkout", actor="agent",
                          ts=3000, payload={"release": "v2"})
        self.store.fork("plan_a", "plan")
        self.store.fork("plan_b", "plan")

        KERNEL.commit(self.store, "plan_a")
        with self.assertRaises(StoreError):
            KERNEL.commit(self.store, "plan_b")

    def test_a_foreign_claim_is_scored_against_reality(self):
        KERNEL.ledger.record(
            self.store, origin_branch="plan", resolver="pages_within",
            params={"service": "checkout"}, p=0.8, claim="checkout pages after the deploy",
            made_at=3000, resolve_by=3000 + 24 * HOUR, predictor="test",
        )
        KERNEL.ledger.record(
            self.store, origin_branch="plan", resolver="pages_within",
            params={"service": "search"}, p=0.2, claim="search pages after the deploy",
            made_at=3000, resolve_by=3000 + 24 * HOUR, predictor="test",
        )
        self.store.append(branch=TRUNK, kind=PAGED, entity="checkout", actor="world",
                          ts=4000, payload={})
        self.store.append(branch=TRUNK, kind=SERVICE_SEEN, entity="search", actor="world",
                          ts=3000 + 25 * HOUR, payload={})

        settled = KERNEL.ledger.resolve_due(self.store)
        self.assertEqual(len(settled), 2)
        by_service = {r["params"]["service"]: r["outcome"] for r in settled}
        self.assertEqual(by_service, {"checkout": True, "search": False})

        out = KERNEL.ledger.score(self.store)
        self.assertEqual(out["n"], 2)
        self.assertEqual(out["verdict"], "beats baseline")

    def test_a_ledger_claim_needs_a_resolver_that_exists(self):
        with self.assertRaises(ValueError):
            KERNEL.ledger.record(
                self.store, origin_branch="plan", resolver="mail_reply_within",
                params={}, p=0.5, claim="borrowed from another domain",
                made_at=3000, resolve_by=4000, predictor="test",
            )

    def test_weights_are_this_domains_features_and_stay_a_guess_until_earned(self):
        prefs = KERNEL.preferences
        self.assertEqual(prefs.keys, ("pages", "shipped"))

        offer = [
            {"branch": "plan_ship", "plan": "ship", "features": {"shipped": 2.0, "pages": 0.4}},
            {"branch": "plan_hold", "plan": "hold", "features": {"shipped": 0.0, "pages": 0.0}},
        ]
        prefs.record_offer(self.store, "r_1", offer, at=3000)
        self.store.fork("plan_ship", TRUNK)
        prefs.record_choice(self.store, "plan_ship", at=3100)

        weights, why = prefs.effective_weights(self.store)
        self.assertEqual(weights, PRIOR)
        self.assertEqual(why["source"], "hand-picked")
        self.assertIn("not enough to learn from yet", why["why"])


class TestFutures(unittest.TestCase):
    def test_the_distribution_is_a_distribution(self):
        ps = [0.9, 0.5, 0.31, 0.02, 0.77]
        dist = count_distribution(ps)
        self.assertEqual(len(dist), len(ps) + 1)
        self.assertAlmostEqual(sum(dist), 1.0, places=12)
        self.assertAlmostEqual(sum(k * d for k, d in enumerate(dist)), sum(ps), places=12)

    def test_nothing_uncertain_is_one_certain_future(self):
        self.assertEqual(enumerate_futures([]), [{"outcomes": [], "p": 1.0, "count": 0}])

    def test_the_futures_shown_are_the_likeliest_counts(self):
        fs = enumerate_futures([0.9, 0.8, 0.1], keep=2)
        self.assertEqual(len(fs), 2)
        self.assertGreaterEqual(fs[0]["p"], fs[1]["p"])
        # Two of three landing means the two likeliest, not any two.
        two = next(f for f in fs if f["count"] == 2)
        self.assertEqual(two["outcomes"], [True, True, False])


class TestTheKernelIsStandalone(unittest.TestCase):
    def test_no_module_in_the_kernel_mentions_the_product(self):
        """The extraction is only real while this passes."""
        offenders = []
        for name in sorted(os.listdir(os.path.join(ROOT, "takeback"))):
            if not name.endswith(".py"):
                continue
            with io.open(os.path.join(ROOT, "takeback", name), encoding="utf-8") as fh:
                body = fh.read()
            for lineno, line in enumerate(body.splitlines(), 1):
                if "preflight" in line:
                    offenders.append(f"takeback/{name}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "the kernel has grown a dependency on the product")

    def test_importing_the_kernel_does_not_import_the_product(self):
        """Not just unmentioned -- unreachable. Checked in a clean interpreter."""
        code = (
            "import sys; import takeback; "
            "print(any(m == 'preflight' or m.startswith('preflight.') for m in sys.modules))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
        )
        self.assertEqual(out.stdout.strip(), "False")

    def test_the_kernel_ships_the_six_parts(self):
        for part in ("EventStore", "Projection", "Commits", "Ledger", "Preferences", "Kernel"):
            self.assertTrue(hasattr(takeback, part), part)
        self.assertTrue(callable(takeback.enumerate_futures))


if __name__ == "__main__":
    unittest.main(verbosity=2)
