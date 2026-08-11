"""The cold-start path, the shareable card, and the shape of a first run."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from preflight import paste, rehearse, server, synthetic
from rehearsal.store import TRUNK, EventStore
from preflight.world import project

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "preflight", "web")

GMAIL = """Thanks Ana - can you confirm the numbers by Friday?

On Mon, Aug 3, 2026 at 9:12 AM Ana Reyes <ana@example.com> wrote:
> Sorry for the delay, digging into it now.
>
> On Fri, Jul 31, 2026 at 4:40 PM Markus F <me@example.com> wrote:
>> Any progress on the Q3 numbers?
>>
>> On Wed, Jul 29, 2026 at 11:02 AM Ana Reyes <ana@example.com> wrote:
>>> Got it, will look this week.
"""

APPLE = """Sounds good, see you then.

On 4 Aug 2026, at 09:12, Ana Reyes <ana@example.com> wrote:
Confirming Thursday works.

On 2 Aug 2026, at 14:05, Markus F <me@example.com> wrote:
Does Thursday work?
"""

HEADERS = """From: Ana Reyes <ana@example.com>
Date: Mon, 3 Aug 2026 09:12:00 +0000
Subject: Q3 numbers
To: me@example.com

Digging into it now.

From: Markus F <me@example.com>
Date: Fri, 31 Jul 2026 16:40:00 +0000
Subject: Q3 numbers
To: ana@example.com

Any progress on the Q3 numbers?
"""


class TestReadingAPaste(unittest.TestCase):
    def test_a_gmail_reply_chain(self):
        msgs, d = paste.parse_thread(GMAIL, me="me@example.com")
        self.assertEqual(d["shape"], "quoted reply chain")
        self.assertEqual(d["messages"], 4)
        self.assertEqual(d["counterparty"], "ana@example.com")
        self.assertEqual([m["mine"] for m in msgs], [False, True, False, True])
        self.assertEqual([m["ts"] for m in msgs], sorted(m["ts"] for m in msgs))

    def test_an_apple_mail_reply_chain(self):
        # A different client, a different date format, the same thread.
        _, d = paste.parse_thread(APPLE, me="me@example.com")
        self.assertEqual(d["messages"], 3)
        self.assertEqual(d["counterparty"], "ana@example.com")

    def test_raw_headers_from_show_original(self):
        _, d = paste.parse_thread(HEADERS, me="me@example.com")
        self.assertEqual(d["shape"], "headers")
        self.assertEqual(d["messages"], 2)
        self.assertEqual(d["subject"], "Q3 numbers")

    def test_the_text_above_the_quotes_is_yours(self):
        # Nothing in a reply chain says who wrote the top message; it is the
        # person doing the pasting, and it must not be attributed to the other.
        msgs, d = paste.parse_thread(GMAIL, me="")
        self.assertTrue(msgs[-1]["mine"])
        self.assertEqual(d["counterparty"], "ana@example.com")

    def test_it_says_how_thin_the_evidence_is(self):
        _, d = paste.parse_thread(GMAIL, me="me@example.com")
        self.assertTrue(d["thin"])
        self.assertEqual(d["observed_replies"], 1)

    def test_nonsense_is_refused_with_something_useful(self):
        for text in ("hello there", "no dates here at all, just prose about a meeting"):
            with self.assertRaises(ValueError) as caught:
                paste.rehearse_paste(text)
            self.assertIn("paste", str(caught.exception).lower())


class TestRehearsingAPaste(unittest.TestCase):
    def test_one_thread_produces_a_choice(self):
        r = paste.rehearse_paste(GMAIL, me="me@example.com")
        ids = [p["id"] for p in r["plans"]]
        self.assertIn("hold", ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotEqual(r["recommended"], "hold")

    def test_identical_plans_are_not_shown_twice(self):
        # With a single thread, "chase everything" and "chase who answers" are
        # the same action. Offering both would imply a decision that is not there.
        r = paste.rehearse_paste(GMAIL, me="me@example.com")
        signatures = [tuple(a["entity"] for a in p["actions"]) for p in r["plans"]]
        self.assertEqual(len(signatures), len(set(signatures)))

    def test_thin_evidence_falls_back_to_the_stated_prior(self):
        r = paste.rehearse_paste(GMAIL, me="me@example.com")
        best = next(p for p in r["plans"] if p["id"] == r["recommended"])
        claim = best["uncertain"][0]
        self.assertAlmostEqual(claim["p"], r["diagnostics"]["prior_used"], places=2)
        self.assertTrue(r["diagnostics"]["thin"])

    def test_nothing_is_written_down(self):
        r = paste.rehearse_paste(GMAIL, me="me@example.com")
        self.assertFalse(r["diagnostics"]["stored"])

    def test_a_pasted_rehearsal_does_not_touch_a_real_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(os.path.join(tmp, "p.db"))
            synthetic.seed_world(store, days=60, seed=5)
            before = project(store.read(TRUNK)).state_hash()
            paste.rehearse_paste(GMAIL, me="me@example.com")
            self.assertEqual(project(store.read(TRUNK)).state_hash(), before)
            store.close()


class TestTheShareableCard(unittest.TestCase):
    """The card is the growth mechanic, so what it may contain is a hard rule."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(WEB, "map.js"), encoding="utf-8") as fh:
            cls.js = fh.read()
        cls.result = paste.rehearse_paste(GMAIL, me="me@example.com")

    def test_the_renderer_is_shared_by_both_pages(self):
        for page in ("app.html", "paste.html"):
            with open(os.path.join(WEB, page), encoding="utf-8") as fh:
                html = fh.read()
            self.assertIn('src="/map.js"', html, f"{page} must not fork the renderer")
            self.assertIn("buildBranchMap", html)

    def test_the_card_carries_no_css_variables(self):
        # An exported SVG has no stylesheet to inherit from. A var() in the card
        # renders as nothing on someone else's timeline.
        card = self.js[self.js.index("function branchMapCard"):]
        self.assertNotIn("var(--", card)

    def test_nothing_personal_can_reach_the_card(self):
        # The renderer is handed plan names, counts and probabilities. If it ever
        # started reading a contact, a subject or a message body off the payload,
        # this catches it. (The function's own `body` return field is not that.)
        build = self.js[self.js.index("function buildBranchMap"):self.js.index("function branchMapCard")]
        leak = re.compile(
            r"\b(?:map|p|b|plan|branch|opts)\s*(?:\.|\[[\"\'])\s*"
            r"(?:body|subject|contact|counterparty|describe|uncertain|sender|merchant)\b"
        )
        self.assertIsNone(leak.search(build), "the map must not read anything personal")

    def test_the_payload_the_card_uses_names_nobody(self):
        shown = []
        for p in self.result["plans"]:
            shown.append(p["name"])
            shown += [f"{b['metrics']['replies']} {b['p']}" for b in p["branches"]]
        blob = " ".join(shown)
        self.assertNotIn("Ana", blob)
        self.assertNotIn("example.com", blob)


class TestFirstRun(unittest.TestCase):
    def test_an_empty_store_reports_itself_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(os.path.join(tmp, "e.db"))
            payload = server.world_payload(store)
            self.assertEqual(payload["summary"]["contacts"], 0)
            self.assertEqual(payload["threads"], [])
            store.close()

    def test_hidden_panels_are_actually_hidden(self):
        # `display:flex` on a component beats the user-agent rule for [hidden].
        # Without an explicit override the onboarding and the results render on
        # top of each other on first load.
        for page in ("app.html", "paste.html"):
            with open(os.path.join(WEB, page), encoding="utf-8") as fh:
                css = fh.read()
            self.assertRegex(css, r"\[hidden\]\s*\{\s*display:\s*none\s*!important")

    def test_the_package_declares_its_entry_point_and_its_pages(self):
        with open(os.path.join(os.path.dirname(WEB), "..", "pyproject.toml"), encoding="utf-8") as fh:
            cfg = fh.read()
        self.assertIn("preflight = \"preflight.cli:main\"", cfg)
        self.assertIn('preflight = ["web/*"]', cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
