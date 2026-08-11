"""The app: a JSON API over the twin, and the page that draws it.

Standard library only, one SQLite connection per request.

Binding to loopback is not an authorisation boundary, and treating it as one was
a real hole: any web page the user happened to have open could POST here. A
cross-origin `text/plain` POST is a CORS *simple request*, so it needs no
preflight; the attacker cannot read the reply, but the side effects land. A page
served from another port cancelled three subscriptions and sent four messages on
a running instance with no interaction at all -- the branch name is a hash of
documented defaults, so it can be computed offline and fired blind.

Three checks close it, and they are cheap: a cross-origin `Origin` is refused, a
`Host` that is not this loopback address is refused (which is what stops DNS
rebinding turning `/api/world` into a mailbox reader), and writes are confined to
POST. This is still a single-user local program; it now behaves like one under
attack rather than only when nobody is trying.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

from . import commits, paste, predictions as P, preferences, rehearse
from .store import TRUNK, EventStore, StoreError
from .world import project

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
DAY = 24 * 3600


def world_payload(store: EventStore, branch: str = TRUNK) -> dict:
    w = project(store.read(branch))
    now = w.clock
    threads = sorted(w.open_threads(), key=lambda t: -t.last_ts)[:12]
    meetings = sorted(
        [m for m in w.meetings.values() if m.state == "scheduled" and m.start >= now],
        key=lambda m: m.start,
    )[:8]
    return {
        "branch": branch,
        "summary": w.summary(),
        "threads": [
            {
                "id": t.id,
                "subject": t.subject,
                "contact": t.counterparty,
                "name": w.contacts.get(t.counterparty, {}).get("name", t.counterparty),
                "waiting_days": round((now - t.last_ts) / DAY, 1),
                "messages": len(t.messages),
            }
            for t in threads
        ],
        "meetings": [
            {
                "id": m.id,
                "title": m.title,
                "start": m.start,
                "in_days": round((m.start - now) / DAY, 1),
                "moves": len(m.moves),
            }
            for m in meetings
        ],
        "subscriptions": [
            {"id": s.id, "merchant": s.merchant, "amount_cents": s.amount_cents,
             "state": s.state, "charges": len(s.charges)}
            for s in sorted(w.subscriptions.values(), key=lambda s: -s.amount_cents)
        ],
    }


def branch_payload(store: EventStore, branch: str) -> dict:
    row = store.require_branch(branch)
    events = [e for e in store.read(branch) if e.branch != TRUNK]
    raw = store.read(branch)
    return {
        "branch": branch,
        "status": row["status"],
        "note": row["note"],
        "state_hash": project(raw).state_hash(),
        # The fork with the invented parts taken back out. Committing can never
        # reproduce the full rehearsal -- the simulated replies are exactly what
        # does not happen for real -- but it must reproduce this exactly, and the
        # product should claim that and nothing larger.
        "state_hash_real": project(raw, include_simulated=False).state_hash(),
        "timeline": [
            {
                "ts": e.ts,
                "kind": e.kind,
                "entity": e.entity,
                "actor": e.actor,
                "simulated": e.simulated,
                "subject": e.payload.get("subject", ""),
                "body": e.payload.get("body", ""),
                "reason": e.payload.get("reason", ""),
                "merchant": e.payload.get("merchant", ""),
            }
            for e in events
        ],
        "committable": len(commits.promotable(store, branch)),
    }


MAX_HORIZON_DAYS = 90


class Handler(BaseHTTPRequestHandler):
    db = "preflight.db"
    server_version = "preflight"
    allowed_hosts: frozenset = frozenset()

    def log_message(self, fmt, *args):  # quieter than the default
        pass

    # ------------------------------------------------------------------ plumbing

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, default=str).encode("utf-8"), "application/json")

    MAX_BODY = 1 << 21

    def _body(self) -> dict:
        raw = self.headers.get("Content-Length") or "0"
        if not raw.strip().isdigit():
            raise ValueError("Content-Length is not a number")
        n = int(raw)
        if n > self.MAX_BODY:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

    def _hosts(self) -> frozenset:
        port = self.server.server_address[1]
        return frozenset(self.allowed_hosts) or frozenset({
            f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}",
        })

    def _own_host(self, host: str) -> bool:
        """Is this Host header us?

        What DNS rebinding needs is a *name* that can be pointed somewhere else,
        so the test is on the hostname. A bare loopback literal with no port is
        still unmistakably us; a port, when given, has to be ours.
        """
        name, _, port = host.rpartition(":") if ":" in host.rstrip("]") else (host, "", "")
        name = (name or host).strip("[]")
        if name not in {h.strip("[]") for h in self.LOOPBACK}:
            return False
        return not port or port == str(self.server.server_address[1])

    def _permitted(self) -> Optional[str]:
        """Why this request should not be served, or None."""
        hosts = self._hosts()
        host = (self.headers.get("Host") or "").strip().lower()
        if host and not self._own_host(host):
            return (
                f"Host {host!r} is not this server. Reach it at {sorted(hosts)[0]}."
            )
        origin = self.headers.get("Origin")
        if origin and origin.lower() not in {f"http://{h}" for h in hosts}:
            return (
                f"Refusing a request from {origin}. This server acts on somebody's mail; "
                "another site may not drive it."
            )
        if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
            return "Refusing a cross-site request."
        return None

    def _static(self, path: str) -> None:
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        name = os.path.basename(path) or "app.html"
        if "." not in name:
            name += ".html"
        full = os.path.join(WEB, name)
        if not os.path.isfile(full):
            self._send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype)

    # ------------------------------------------------------------------- routes

    def do_GET(self) -> None:
        refused = self._permitted()
        if refused:
            self._json({"error": refused}, 403)
            return
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            self._static(url.path)
            return
        self._dispatch(url.path, parse_qs(url.query), None)

    def do_POST(self) -> None:
        refused = self._permitted()
        if refused:
            self._json({"error": refused}, 403)
            return
        try:
            body = self._body()
        except (json.JSONDecodeError, ValueError):
            # Malformed framing used to escape to the socket layer and drop the
            # connection with no reply at all.
            self._json({"error": "body was not JSON"}, 400)
            return
        self._dispatch(urlparse(self.path).path, {}, body)

    def _dispatch(self, path: str, q: dict, body: Optional[dict]) -> None:
        # Opened after the route is known: `/api/paste` promises nothing is
        # stored, and creating the database file on the way past made that false
        # in the one way a person could check.
        store = EventStore(":memory:" if path == "/api/paste" else self.db)
        try:
            if path == "/api/world":
                self._json(world_payload(store, q.get("branch", [TRUNK])[0]))

            elif path == "/api/branch":
                self._json(branch_payload(store, q["branch"][0]))

            elif path == "/api/score":
                self._json({
                    "overall": P.score(store),
                    "rehearsal": P.score(store, predictor="rehearsal/per-contact-age"),
                    "open_claims": sum(
                        1 for r in P.ledger(store).values() if r["outcome"] is None
                    ),
                })

            elif path == "/api/ledger":
                recs = sorted(P.ledger(store).values(), key=lambda r: -r["made_at"])
                self._json([
                    {"claim": r["claim"], "p": r["p"], "outcome": r["outcome"],
                     "made_at": r["made_at"], "resolve_by": r["resolve_by"],
                     "predictor": r["predictor"]}
                    for r in recs[:40]
                ])

            elif path == "/api/paste":
                body = body or {}
                text = (body.get("text") or "").strip()
                if len(text) < 40:
                    raise ValueError("Paste a thread with at least a couple of messages in it.")
                if len(text) > 200_000:
                    raise ValueError("That is larger than a thread. Paste one conversation.")
                self._json(paste.rehearse_paste(
                    text, me=body.get("me", ""),
                    horizon_days=_horizon(body),
                ))

            elif path == "/api/rehearse":
                body = body or {}
                # A new rehearsal is the natural moment to settle the claims the
                # last one made, so the scoreboard moves without anyone asking.
                P.resolve_due(store)
                self._json(rehearse.rehearse(
                    store,
                    mandate=body.get("mandate") or ["chase", "prune", "defend"],
                    horizon_days=_horizon(body),
                ))

            elif path == "/api/commit":
                self._json(commits.commit(store, (body or {})["branch"]))

            elif path == "/api/decline":
                self._json(preferences.decline(store, (body or {})["branch"]))

            elif path == "/api/weights":
                weights, provenance = preferences.effective_weights(store)
                self._json({"weights": weights, "from": provenance})

            elif path == "/api/undo":
                self._json(commits.undo(store, (body or {})["commit_id"]))

            elif path == "/api/resolve":
                self._json({"settled": len(P.resolve_due(store))})

            else:
                self._json({"error": f"no route {path}"}, 404)

        except (StoreError, ValueError, KeyError) as exc:
            self._json({"error": str(exc)}, 400)
        except sqlite3.Error as exc:
            self._json({"error": f"the store refused that: {exc}"}, 409)
        except Exception as exc:  # noqa: BLE001 - surface it, do not swallow it
            traceback.print_exc()
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        finally:
            store.close()


def _horizon(body: dict) -> int:
    """A horizon is walked a day at a time, so an unbounded one is a free CPU burn."""
    try:
        days = int(body.get("horizon_days", 7))
    except (TypeError, ValueError):
        raise ValueError("horizon_days must be a number")
    if not 1 <= days <= MAX_HORIZON_DAYS:
        raise ValueError(f"horizon_days must be between 1 and {MAX_HORIZON_DAYS}")
    return days


def serve(db: str, host: str = "127.0.0.1", port: int = 8787) -> None:
    Handler.db = db
    Handler.allowed_hosts = frozenset({
        f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}",
    })
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"preflight on http://{host}:{port}  (db: {db})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
