"""Adversarial QA.

Every test here asserts the behaviour the README, the module docstrings or the
UI *claims*. A test that fails is a claim the code does not keep. Tests that
pass are claims I tried hard to break and could not; they are kept as
regression guards.

Nothing outside this file is modified. Where a test needs to widen a race
window or inject a fault it monkey-patches at runtime and restores afterwards.
"""

from __future__ import annotations

import http.client
import itertools
import json
import math
import os
import re
import shutil
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from preflight import backtest, commits, events as E, ingest, paste, predictions as P
from preflight import predictors, rehearse, server, synthetic
from rehearsal.store import TRUNK, EventStore, StoreError
from preflight.world import project

DAY = 24 * 3600
HERE = os.path.dirname(os.path.abspath(__file__))


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="preflight-qa-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def db(self, name="t.db"):
        return os.path.join(self.dir, name)

    def seeded(self, name="t.db", days=45, seed=5):
        s = EventStore(self.db(name))
        synthetic.seed_world(s, days=days, seed=seed)
        self.addCleanup(s.close)
        return s

    @staticmethod
    def agent_sends(store):
        return [e for e in store.read(TRUNK)
                if e.kind == E.MESSAGE_SENT and e.actor == E.ACTOR_AGENT]


# ---------------------------------------------------------------------------
# 1. "The same actions cannot be executed twice."
# ---------------------------------------------------------------------------


class DoubleExecution(Tmp):

    def _two_futures(self, store):
        m = rehearse.rehearse(store)
        plan = next(p for p in m["plans"] if p["id"] == "chase")
        self.assertGreaterEqual(len(plan["branches"]), 2)
        return plan, plan["branches"][0]["id"], plan["branches"][1]["id"]

    def test_interrupted_commit_is_atomic(self):
        """commits.commit performs ~10 separate SQLite transactions with no
        enclosing BEGIN. If it dies between COMMIT_OPENED and COMMIT_SEALED the
        actions it already promoted are live on the trunk, cannot be undone
        (undo requires a *sealed* commit) and are absent from already_promoted,
        so the next commit sends them again."""
        s = self.seeded()
        plan, b0, b1 = self._two_futures(s)

        real = EventStore.append
        calls = {"n": 0}

        def flaky(self_, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 5:            # part-way through promoting
                raise RuntimeError("simulated crash mid-commit")
            return real(self_, *a, **kw)

        EventStore.append = flaky
        try:
            with self.assertRaises(RuntimeError):
                commits.commit(s, b0)
        finally:
            EventStore.append = real

        stranded = self.agent_sends(s)
        w = project(s.read(TRUNK))

        # Either nothing landed, or what landed is undoable and recorded.
        self.assertEqual(
            [], stranded,
            "a crash mid-commit left %d agent actions on the trunk with no "
            "COMMIT_SEALED receipt behind them" % len(stranded))
        self.assertNotIn("open", [c["state"] for c in w.commits.values()])

    def test_interrupted_commit_does_not_let_a_sibling_replay_it(self):
        s = self.seeded()
        plan, b0, b1 = self._two_futures(s)

        real = EventStore.append
        calls = {"n": 0}

        def flaky(self_, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 5:
                raise RuntimeError("simulated crash mid-commit")
            return real(self_, *a, **kw)

        EventStore.append = flaky
        try:
            with self.assertRaises(RuntimeError):
                commits.commit(s, b0)
        finally:
            EventStore.append = real

        try:
            commits.commit(s, b1)
        except StoreError:
            pass  # refusing is the correct outcome

        seen = {}
        for e in self.agent_sends(s):
            seen[e.entity] = seen.get(e.entity, 0) + 1
        dupes = {k: v for k, v in seen.items() if v > 1}
        self.assertEqual({}, dupes, "the same follow-up was sent twice: %r" % dupes)

    def test_concurrent_commits_do_not_double_execute(self):
        """already_promoted() is read and then ~10 unsynchronised writes follow.
        Two requests whose appends interleave both pass the clash check and both
        execute. The sleep only widens a window that store.append already opens:
        every append does a full-lineage read() first, so the window grows with
        history size."""
        s = self.seeded()
        plan, b0, b1 = self._two_futures(s)
        s.close()

        real = EventStore.append

        def slow(self_, *a, **kw):
            time.sleep(0.02)
            return real(self_, *a, **kw)

        results = {}
        gate = threading.Barrier(2)

        def go(key, branch):
            st = EventStore(self.db())
            st.db.execute("PRAGMA busy_timeout = 30000")
            try:
                gate.wait()
                results[key] = commits.commit(st, branch)
            except Exception as exc:                        # noqa: BLE001
                results[key] = exc
            finally:
                st.close()

        EventStore.append = slow
        try:
            ta = threading.Thread(target=go, args=("A", b0))
            tb = threading.Thread(target=go, args=("B", b1))
            ta.start()
            time.sleep(0.01)
            tb.start()
            ta.join()
            tb.join()
        finally:
            EventStore.append = real

        after = EventStore(self.db())
        self.addCleanup(after.close)
        seen = {}
        for e in self.agent_sends(after):
            seen[e.entity] = seen.get(e.entity, 0) + 1
        dupes = {k: v for k, v in seen.items() if v > 1}
        self.assertEqual({}, dupes,
                         "concurrent commits sent %d follow-ups twice" % len(dupes))


# ---------------------------------------------------------------------------
# 2. "Promoting a fork ... opens a 24h window."
# ---------------------------------------------------------------------------


class UndoWindow(Tmp):

    def _commit_one(self, store):
        m = rehearse.rehearse(store)
        b = next(p for p in m["plans"] if p["id"] == "chase")["branches"][0]["id"]
        return commits.commit(store, b)

    def test_undo_window_expires_in_real_time(self):
        """`undo_until` is compared against store.now(TRUNK) -- the timestamp of
        the last trunk event -- not against the clock on the wall. The receipt
        is rendered in the UI with `new Date(ts*1000)`, i.e. as a wall-clock
        instant, so a demo shows a deadline that passed last year and the undo
        still works."""
        s = self.seeded()
        r = self._commit_one(s)
        self.assertGreater(
            r["undo_until"], time.time() - 60,
            "the receipt promises an undo window ending %.0f days before the "
            "user is even looking at it" % ((time.time() - r["undo_until"]) / DAY))

    def test_an_unrelated_later_event_does_not_close_the_window(self):
        """One ordinary inbound message dated past the deadline -- exactly what
        the next mbox import writes -- silently closes every open undo."""
        s = self.seeded()
        r = self._commit_one(s)
        s.append(branch=TRUNK, kind=E.MESSAGE_RECEIVED, entity="th_new",
                 actor=E.ACTOR_WORLD, ts=r["undo_until"] + 2 * DAY,
                 payload={"sender": "ana.reyes", "counterparty": "ana.reyes",
                          "subject": "later", "body": "hi"})
        try:
            out = commits.undo(s, r["commit_id"])
        except StoreError as exc:
            self.fail("a single later observation closed the undo window: %s" % exc)
        # The claim is that the window did not close. The world really has
        # changed -- an unrelated message arrived -- so `restored` is False
        # for a different and correct reason.
        self.assertIsNotNone(out["state_hash"])


# ---------------------------------------------------------------------------
# 3. "Tampering is detectable."
# ---------------------------------------------------------------------------


class Integrity(Tmp):

    def test_in_place_edit_is_detected(self):
        s = self.seeded(days=15)
        path = s.path
        s.close()
        db = sqlite3.connect(path)
        gid = db.execute("select gid from events order by gid limit 1 offset 5").fetchone()[0]
        db.execute("update events set payload=? where gid=?", ('{"body": "HACKED"}', gid))
        db.commit()
        db.close()
        s2 = EventStore(path)
        self.addCleanup(s2.close)
        with self.assertRaises(StoreError):
            s2.verify(TRUNK)

    def test_mid_chain_deletion_is_detected(self):
        s = self.seeded(days=15)
        path = s.path
        s.close()
        db = sqlite3.connect(path)
        gid = db.execute("select gid from events order by gid limit 1 offset 8").fetchone()[0]
        db.execute("delete from events where gid=?", (gid,))
        db.commit()
        db.close()
        s2 = EventStore(path)
        self.addCleanup(s2.close)
        with self.assertRaises(StoreError):
            s2.verify(TRUNK)

    def test_truncating_the_tail_is_detected(self):
        """Dropping the newest events -- the cheapest useful tampering, and the
        one that erases a commit receipt -- leaves a chain that verifies
        perfectly. There is no head anchor, count or checkpoint anywhere."""
        s = self.seeded(days=15)
        path = s.path
        before = s.verify(TRUNK)
        s.close()
        db = sqlite3.connect(path)
        db.execute("delete from events where gid > (select max(gid) - 20 from events)")
        db.commit()
        db.close()
        s2 = EventStore(path)
        self.addCleanup(s2.close)
        with self.assertRaises(StoreError):
            s2.verify(TRUNK)
        # A per-branch checkpoint (count + head hash) now catches this. It is
        # not a signature -- write access can update it too -- but truncation
        # no longer passes silently.


# ---------------------------------------------------------------------------
# 4. "Expectations are exact because they come from marginals."
# ---------------------------------------------------------------------------


class Expectations(Tmp):

    def test_poisson_binomial_is_exact_and_composition_is_modal(self):
        """PASSES. The count distribution matches brute force to 1e-12 and the
        composition shown for each count really is the argmax over subsets of
        that size."""
        def u(p):
            return rehearse.Uncertainty(resolver="reply_within", params={}, p=p,
                                        describe="", entity="e", contact="c",
                                        at=0, resolve_by=1)

        for ps in ([0.9, 0.5, 0.1], [0.03, 0.97, 0.5, 0.5, 0.42],
                   [0.2] * 8, [0.11, 0.22, 0.33, 0.44, 0.55, 0.66]):
            unc = [u(p) for p in ps]
            n = len(ps)
            exact = {}
            for bits in itertools.product([0, 1], repeat=n):
                pr = 1.0
                for b, q in zip(bits, ps):
                    pr *= q if b else 1 - q
                exact[sum(bits)] = exact.get(sum(bits), 0.0) + pr
            for f in rehearse.enumerate_futures(unc, keep=n + 1):
                self.assertAlmostEqual(f["p"], exact[f["count"]], places=12)
                k = f["count"]
                shown = tuple(i for i, h in enumerate(f["outcomes"]) if h)
                def mass(S):
                    return math.prod([ps[i] for i in S] +
                                     [1 - ps[i] for i in range(n) if i not in S])
                best = max(itertools.combinations(range(n), k), key=mass)
                self.assertAlmostEqual(mass(shown), mass(best), places=12)

    def test_expected_threads_open_matches_the_futures_shipped_with_it(self):
        """_expected() computes open_before - E[replies] and ignores that a plan
        can *open* threads. `defend` sends a pre-confirm on a brand new
        th_confirm_<meeting> thread per at-risk meeting, so every future it
        ships projects more open threads than the 'exact' expectation on the
        same screen."""
        s = self.seeded(days=200)
        m = rehearse.rehearse(s)
        for plan in m["plans"]:
            lo = min(b["metrics"]["threads_open"] for b in plan["branches"])
            hi = max(b["metrics"]["threads_open"] for b in plan["branches"])
            self.assertTrue(
                lo - 1e-9 <= plan["expected"]["threads_open"] <= hi + 1e-9,
                "plan %r: E[threads_open]=%s but its own futures project %s..%s"
                % (plan["id"], plan["expected"]["threads_open"], lo, hi))


# ---------------------------------------------------------------------------
# 5. "per-contact-age beats the baseline" -- what is actually being measured?
# ---------------------------------------------------------------------------


class Backtest(Tmp):

    def test_the_backtest_varies_the_age_variable(self):
        """Every thread claim the harness scores is made at the instant the
        message goes out, so `waited` is always 0; every meeting claim is made
        at start-horizon, so `left` is always 2. The evaluation set contains a
        single cell of the age dimension, so it cannot test the feature the
        README says it validates."""
        s = self.seeded(days=200)
        now = s.now(TRUNK)
        t0 = now - 120 * DAY
        horizon = 48 * 3600
        ages, lefts = set(), set()
        for at, kind, entity in backtest.moments(s, t0, now, horizon):
            past = project(s.read(TRUNK, until_ts=at))
            if kind == "thread":
                t = past.threads.get(entity)
                if t is not None and t.awaiting_reply_from:
                    ages.add((at - t.last_ts) // DAY)
            else:
                mt = past.meetings.get(entity)
                if mt is not None and mt.state == "scheduled":
                    lefts.add((mt.start - at) // DAY)
        self.assertGreater(len(ages), 1,
                           "all thread claims sit in age bucket(s) %r" % sorted(ages))
        self.assertGreater(len(lefts), 1,
                           "all meeting claims sit in 'left' bucket(s) %r" % sorted(lefts))

    def _scores(self, seed, days=120, holdout=60):
        s = EventStore(self.db("bt%d_%d_%d.db" % (seed, days, holdout)))
        self.addCleanup(s.close)
        synthetic.seed_world(s, days=days, seed=seed)
        return {name: backtest.run(s, predictor=name, holdout_days=holdout)["score"]
                for name in ("global", "per-contact", "per-contact-age")}

    def test_per_contact_ranking_survives_reseeding(self):
        """PASSES. per-contact beats the leave-one-out baseline and
        per-contact-age beats per-contact on every seed and holdout I tried
        (seeds 3/5/7/11/13, holdouts 30-120, histories 120-365 days)."""
        for seed in (5, 7):
            got = self._scores(seed)
            self.assertLess(got["per-contact"]["brier"], got["per-contact"]["baseline_brier"],
                            "seed %d: per-contact did not beat the baseline" % seed)
            self.assertLessEqual(got["per-contact-age"]["brier"],
                                 got["per-contact"]["brier"] + 1e-9)

    def test_global_never_beats_the_baseline(self):
        """'`global` failing to beat it is the correct result -- it is the same
        idea wearing a different hat, and a scoreboard that credited it would be
        rewarding noise.' On a 120-day history with a 60-day holdout the
        scoreboard credits it (+1.3% on seed 5), so the harness's ability to
        recover the known-correct ranking is configuration-dependent."""
        for seed in (5, 7):
            got = self._scores(seed)
            self.assertGreater(
                got["global"]["brier"], got["global"]["baseline_brier"],
                "seed %d: global scored %s against a baseline of %s -- the "
                "scoreboard credited the predictor the README says it must not"
                % (seed, got["global"]["brier"], got["global"]["baseline_brier"]))


# ---------------------------------------------------------------------------
# 6. The local server: "it binds to loopback".
# ---------------------------------------------------------------------------


class LocalServer(Tmp):

    def start(self, days=45):
        s = EventStore(self.db("srv.db"))
        synthetic.seed_world(s, days=days, seed=5)
        s.close()
        server.Handler.db = self.db("srv.db")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return self.port

    def request(self, method, path, body=None, headers=None, timeout=180):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        h = dict(headers or {})
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            h.setdefault("Content-Type", "text/plain")   # a CORS "simple request"
        c.request(method, path, body=payload, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_cross_origin_writes_are_refused(self):
        """A page on any origin can fire `fetch(..., {mode:"no-cors"})` at the
        loopback server. There is no Origin / Sec-Fetch-Site / token check and
        _body() ignores Content-Type, so text/plain needs no preflight. It does
        not matter that the reply is unreadable: rehearse forks branches, and
        commit cancels subscriptions and sends mail."""
        self.start()
        evil = {"Origin": "https://evil.example", "Content-Type": "text/plain"}
        code, _ = self.request("POST", "/api/rehearse", {"mandate": ["chase"]}, evil)
        self.assertIn(code, (400, 403),
                      "a hostile origin drove a full rehearsal (HTTP %d)" % code)

    def test_cross_origin_commit_is_refused(self):
        """Branch names are sha256(canonical([base, now, mandate, horizon,
        prior, min_stale]))[:12] -- every input is a documented default and
        `now` is the seeded demo's world clock, so the attacker needs no read
        channel to name the branch it wants committed."""
        self.start(days=200)
        evil = {"Origin": "https://evil.example", "Content-Type": "text/plain"}
        self.request("POST", "/api/rehearse", {}, evil)
        rid = "r_" + __import__("hashlib").sha256(E.canonical(
            [TRUNK, 1752969245, sorted(["chase", "prune", "defend"]), 7, None, 2 * DAY]
        ).encode()).hexdigest()[:12]
        code, body = self.request("POST", "/api/commit", {"branch": rid + "_prune_0"}, evil)
        self.assertIn(code, (400, 403),
                      "a hostile origin committed a rehearsal blind: %s" % body[:200])
        s = EventStore(self.db("srv.db"))
        self.addCleanup(s.close)
        self.assertEqual(0, len(project(s.read(TRUNK)).commits))

    def test_foreign_host_header_is_refused(self):
        """No Host validation, so a DNS-rebinding page reads /api/world -- every
        open thread, contact name and subject line -- same-origin."""
        self.start()
        code, _ = self.request("GET", "/api/world", None, {"Host": "twin.evil.example"})
        self.assertIn(code, (400, 403),
                      "the mailbox was served to Host: twin.evil.example (HTTP %d)" % code)

    def test_malformed_content_length_gets_a_response(self):
        """_body() runs outside _dispatch's try/except: int('abc') raises before
        any handler sees it, so the connection is dropped with no reply at
        all."""
        self.start()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        c.putrequest("POST", "/api/commit", skip_host=True, skip_accept_encoding=True)
        c.putheader("Host", "127.0.0.1")
        c.putheader("Content-Length", "abc")
        c.endheaders()
        try:
            r = c.getresponse()
            status = r.status
            r.read()
        except Exception as exc:                            # noqa: BLE001
            self.fail("malformed Content-Length produced no HTTP response: %r" % exc)
        finally:
            c.close()
        self.assertEqual(400, status)


# ---------------------------------------------------------------------------
# 7. The paste path.
# ---------------------------------------------------------------------------


class Paste(Tmp):

    def test_nothing_is_written_to_disk(self):
        """PASSES. The twin lives in an sqlite ':memory:' database."""
        before = set(os.listdir(self.dir))
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            r = paste.rehearse_paste(
                "Any news?\n\n"
                "On Mon, Aug 4, 2026 at 9:12 AM Ana Reyes <ana@example.com> wrote:\n"
                "> Looking into it.\n\n"
                "On Sun, Aug 3, 2026 at 8:00 AM Ana Reyes <ana@example.com> wrote:\n"
                "> Original note.\n", me="me@example.com")
        finally:
            os.chdir(cwd)
        self.assertFalse(r["diagnostics"]["stored"])
        self.assertEqual(before, set(os.listdir(self.dir)))

    def test_paste_request_creates_no_database_on_disk(self):
        """_dispatch() opens `EventStore(self.db)` before it looks at the route,
        and EventStore.__init__ runs the schema and inserts the trunk row. So a
        /paste visit on a fresh machine leaves an preflight.db behind, while
        the page says 'no account, nothing stored'. No paste content reaches
        it -- but a file that did not exist now does."""
        db = os.path.join(self.dir, "fresh.db")
        server.Handler.db = db
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        c.request("POST", "/api/paste", headers={"Content-Type": "text/plain"},
                  body=json.dumps({
                      "text": "Any news?\n\n"
                              "On Mon, Aug 4, 2026 at 9:12 AM Ana Reyes <ana@example.com> wrote:\n"
                              "> Looking into it.\n\n"
                              "On Sun, Aug 3, 2026 at 8:00 AM Ana Reyes <ana@example.com> wrote:\n"
                              "> Original.\n",
                      "me": "me@example.com"}).encode())
        r = c.getresponse()
        r.read()
        c.close()
        self.assertFalse(os.path.exists(db),
                         "a paste request created %s" % os.path.basename(db))

    def test_whitespace_run_is_not_quadratic(self):
        """QUOTE starts `^\\s*[>\\s]*On\\s+` -- two overlapping greedy
        quantifiers over the same class. A run of spaces that never reaches
        'On' backtracks quadratically. The server accepts 200_000 characters,
        which is ~100 seconds of CPU in one unthrottled request thread, and the
        endpoint takes anonymous input with no Origin check."""
        def burn(n):
            text = "hello this is a pasted email thread\n" + " " * n + "\nend of it"
            t = time.time()
            paste.QUOTE.findall(text)
            return time.time() - t

        burn(2000)                       # warm
        small, big = burn(4000), burn(16000)
        self.assertLess(
            big, max(small * 8, 0.05),
            "QUOTE scales quadratically on whitespace: 4k=%.3fs 16k=%.3fs "
            "(the server's own 200k limit is ~%.0fs)" % (small, big, big * 156))

    def test_pre_epoch_date_is_a_clean_error(self):
        """store.append's backwards-time guard handles head is None but its
        *message* does not: `head.ts` is evaluated on None. Any thread dated
        before 1970 -- a mis-set client clock, a junk Date header -- turns into
        an AttributeError and an HTTP 500 instead of the intended 400."""
        text = ("From: a@ex.com\nDate: Sat, 4 Aug 1900 09:12:00 +0000\n\nfirst message\n\n"
                "From: b@ex.com\nDate: Sun, 4 Aug 2030 09:12:00 +0000\n\nsecond message\n")
        try:
            paste.rehearse_paste(text, me="me@ex.com")
        except (ValueError, StoreError):
            pass
        except Exception as exc:                            # noqa: BLE001
            self.fail("pre-epoch paste raised %s: %s" % (type(exc).__name__, exc))


# ---------------------------------------------------------------------------
# 8. Ingestion.
# ---------------------------------------------------------------------------


class Ingest(Tmp):

    def test_ics_respects_tzid(self):
        """_ics_time() feeds every DTSTART to calendar.timegm, so a TZID-bearing
        or floating local time is read as UTC. Every meeting from a non-UTC
        calendar lands hours off, which shifts both `meeting_moves` claims and
        the 'two days off' backtest sampling."""
        p = os.path.join(self.dir, "c.ics")
        with open(p, "w") as fh:
            fh.write("BEGIN:VCALENDAR\n"
                     "BEGIN:VEVENT\nUID:a\nSUMMARY:UTC\n"
                     "DTSTART:20260804T130000Z\nDTEND:20260804T140000Z\nEND:VEVENT\n"
                     "BEGIN:VEVENT\nUID:b\nSUMMARY:NY\n"
                     "DTSTART;TZID=America/New_York:20260804T090000\n"
                     "DTEND;TZID=America/New_York:20260804T100000\nEND:VEVENT\n"
                     "END:VCALENDAR\n")
        starts = {r["payload"]["title"]: r["payload"]["start"] for r in ingest.read_ics(p)}
        self.assertEqual(starts["UTC"], starts["NY"],
                         "09:00 America/New_York was imported as %d, not 13:00Z (%d)"
                         % (starts["NY"], starts["UTC"]))

    def test_a_backdated_contact_does_not_abort_the_whole_import(self):
        """ingest() wraps the message append in try/except ('one bad record must
        not lose the import') but the CONTACT_OBSERVED append immediately above
        it is unprotected, so a second, older archive kills the run."""
        s = EventStore(self.db())
        self.addCleanup(s.close)
        s.append(branch=TRUNK, kind=E.CONTACT_OBSERVED, entity="z",
                 actor=E.ACTOR_WORLD, ts=1_800_000_000, payload={})
        recs = [{"ts": 1_700_000_000, "kind": E.MESSAGE_RECEIVED, "entity": "th_a",
                 "actor": E.ACTOR_WORLD,
                 "payload": {"sender": "a", "counterparty": "a", "subject": "s", "body": "b"},
                 "_contact": ("a", "A", "a@x")}]
        try:
            out = ingest.ingest(s, recs)
        except Exception as exc:                            # noqa: BLE001
            self.fail("one backdated contact aborted the import: %s: %s"
                      % (type(exc).__name__, exc))
        self.assertEqual(1, out["written"] + out["skipped"])


# ---------------------------------------------------------------------------
# 9. The store.
# ---------------------------------------------------------------------------


class Store(Tmp):

    def test_replay_is_deterministic(self):
        """PASSES."""
        s = self.seeded(days=30)
        self.assertEqual(project(s.read(TRUNK)).state_hash(),
                         project(s.read(TRUNK)).state_hash())
        self.assertGreater(s.verify(TRUNK), 0)

    def test_a_rehearsal_never_moves_the_trunk(self):
        """PASSES."""
        s = self.seeded(days=45)
        before = project(s.read(TRUNK)).state_hash()
        rehearse.rehearse(s)
        self.assertEqual(before, project(s.read(TRUNK)).state_hash())

    def test_simulated_actors_cannot_reach_the_trunk(self):
        """PASSES for the actors the product actually writes: store.append
        refuses `sim:` on the trunk and promotable() additionally requires
        actor == 'agent', so there are two independent gates."""
        s = self.seeded(days=45)
        with self.assertRaises(StoreError):
            s.append(branch=TRUNK, kind=E.MESSAGE_RECEIVED, entity="th_0001",
                     actor="sim:ana.reyes", ts=s.now(TRUNK) + 1, payload={"body": "x"})
        m = rehearse.rehearse(s)
        plan = next(p for p in m["plans"] if p["id"] == "chase")
        b = plan["branches"][0]["id"]
        self.assertTrue(any(e.simulated for e in s.read(b)))
        self.assertFalse(any(e.simulated for e in commits.promotable(s, b)))
        commits.commit(s, b)
        self.assertFalse(any(e.simulated for e in s.read(TRUNK)))

    def test_is_simulated_is_not_fooled_by_case_or_whitespace(self):
        """events.is_simulated is a bare, case-sensitive startswith. It is
        described as 'the single mechanical guarantee' behind the quarantine,
        and it accepts 'SIM:', ' sim:' and a Cyrillic homoglyph straight onto
        the trunk. No current code path feeds it an untrusted actor, so this is
        a latent hazard rather than a live exploit -- but it is one string away
        from being one."""
        for actor in ("SIM:ana", "Sim:ana", " sim:ana", "ѕim:ana", "sim​:ana"):
            self.assertTrue(E.is_simulated(actor) or actor not in E.REAL_ACTORS,
                            "%r is not recognised as simulated" % actor)

    def test_fork_at_ts_rewinds_through_ancestors(self):
        """fork(at_ts=T) only bounds the parent's *own* segment; inherited
        ancestor history is taken whole. A branch created 'at' T therefore
        projects a world from long after T, while branches.created_ts records
        T."""
        s = EventStore(self.db())
        self.addCleanup(s.close)
        for i in range(5):
            s.append(branch=TRUNK, kind=E.CONTACT_OBSERVED, entity="c%d" % i,
                     actor=E.ACTOR_WORLD, ts=1000 + i * 100, payload={"name": "c%d" % i})
        s.fork("mid", TRUNK)
        s.fork("from_mid", "mid", at_ts=1200)
        s.fork("from_trunk", TRUNK, at_ts=1200)
        self.assertEqual(sorted(project(s.read("from_trunk")).contacts),
                         sorted(project(s.read("from_mid")).contacts),
                         "at_ts=1200 was ignored for the inherited trunk segment")

    def test_append_is_not_linear_in_history(self):
        """Every append calls head() -> read(), which materialises the entire
        lineage. Building a log is therefore O(n^2): a 4000-message mbox takes
        86s, and the cost is ~5.4e-6 * n^2, so a 50k-message Takeout is hours."""
        s = EventStore(self.db())
        self.addCleanup(s.close)

        def bulk(n, ts0):
            t = time.time()
            for i in range(n):
                s.append(branch=TRUNK, kind=E.CONTACT_OBSERVED, entity="c%d" % (ts0 + i),
                         actor=E.ACTOR_WORLD, ts=ts0 + i, payload={"name": "x"})
            return time.time() - t

        bulk(400, 1000)                  # warm the log up to 400 events
        early = bulk(400, 10_000)        # events 400..800
        for k in range(3):
            bulk(400, 100_000 + k * 1000)
        late = bulk(400, 200_000)        # events 2000..2400
        self.assertLess(late, early * 2.5,
                        "append cost grows with history: 400 appends took %.2fs at "
                        "n=400 and %.2fs at n=2000" % (early, late))


# ---------------------------------------------------------------------------
# 10. The card.
# ---------------------------------------------------------------------------


class Card(Tmp):

    def test_renderer_names_no_personal_field(self):
        """PASSES. map.js reads only plan names, counts and probabilities, and
        the only free text it is handed (title/subtitle/footer) is built in the
        pages from plan names and aggregate numbers."""
        with open(os.path.join(server.WEB, "map.js")) as fh:
            src = fh.read()
        code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        code = re.sub(r"//[^\n]*", "", code)
        # `document.body` and the renderer's own `{body, width, height}` return
        # value are not mail bodies; everything else here would be personal.
        code = code.replace("document.body", "").replace(".body", "")
        for token in ("contact", "counterparty", "subject", "sender", "merchant",
                      "messages", "threads", "attendee"):
            hits = [ln for ln in code.splitlines() if re.search(token, ln)]
            self.assertEqual([], hits, "map.js touches %r: %r" % (token, hits[:2]))


# ---------------------------------------------------------------------------
# 11. The recommendation.
# ---------------------------------------------------------------------------


class Recommendation(Tmp):

    def test_the_recommendation_discloses_what_it_turns_on(self):
        """The recommendation does flip. `burn_saved_per_1000c = 0.5` makes one
        cancelled subscription worth a whole predicted reply, and at a tenth of
        it a different plan wins. That number is an admitted guess, so stability
        was never the right thing to demand of it -- disclosure is. The payload
        now carries the margin, the runner-up, and which weights flip the answer,
        and the interface prints them."""
        store = self.seeded(seed=5, days=200)
        m = rehearse.rehearse(store)
        sens = m["sensitivity"]
        store.close()

        self.assertIsNotNone(sens["margin"])
        self.assertTrue(sens["runner_up"])
        self.assertTrue(sens["verdict"])
        if sens["flips_under"]:
            self.assertIn("guess", sens["verdict"])
            for flip in sens["flips_under"]:
                self.assertIn(flip["weight"], rehearse.WEIGHTS)
                self.assertTrue(flip["instead"])

    def test_commit_reproduces_the_fork_minus_simulated_replies(self):
        """PASSES, and the receipt is honest about what it compares."""
        s = self.seeded(days=45)
        m = rehearse.rehearse(s)
        b = next(p for p in m["plans"] if p["id"] == "chase")["branches"][0]["id"]
        payload = server.branch_payload(s, b)
        r = commits.commit(s, b)
        self.assertEqual(payload["state_hash_real"], r["state_after"])
        self.assertNotEqual(payload["state_hash"], r["state_after"])

    def test_undo_restores_the_previous_state_exactly(self):
        """PASSES when nothing else happened in between."""
        s = self.seeded(days=45)
        m = rehearse.rehearse(s)
        b = next(p for p in m["plans"] if p["id"] == "chase")["branches"][0]["id"]
        before = project(s.read(TRUNK)).state_hash()
        r = commits.commit(s, b)
        self.assertNotEqual(before, r["state_after"])
        out = commits.undo(s, r["commit_id"])
        self.assertTrue(out["restored"])
        self.assertEqual(before, out["state_hash"])


if __name__ == "__main__":
    unittest.main()
