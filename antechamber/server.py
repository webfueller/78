"""The app: a JSON API over the twin, and the page that draws it.

Standard library only, one SQLite connection per request. This is a single-user
local server -- the twin holds somebody's mail, so it binds to loopback and
carries no auth story it cannot honour. Multi-tenant hosting is a different
program with a different threat model, and pretending otherwise in a comment
would be how that gets forgotten.
"""

from __future__ import annotations

import json
import mimetypes
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

from . import commits, predictions as P, rehearse
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


class Handler(BaseHTTPRequestHandler):
    db = "antechamber.db"
    server_version = "antechamber"

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

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _static(self, path: str) -> None:
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        name = os.path.basename(path) or "app.html"
        full = os.path.join(WEB, name)
        if not os.path.isfile(full):
            self._send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype)

    # ------------------------------------------------------------------- routes

    def do_GET(self) -> None:
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if not url.path.startswith("/api/"):
            self._static(url.path)
            return
        self._dispatch(url.path, q, None)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        try:
            body = self._body()
        except json.JSONDecodeError:
            self._json({"error": "body was not JSON"}, 400)
            return
        self._dispatch(url.path, {}, body)

    def _dispatch(self, path: str, q: dict, body: Optional[dict]) -> None:
        store = EventStore(self.db)
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

            elif path == "/api/rehearse":
                body = body or {}
                self._json(rehearse.rehearse(
                    store,
                    mandate=body.get("mandate") or ["chase", "prune", "defend"],
                    horizon_days=int(body.get("horizon_days", 7)),
                ))

            elif path == "/api/commit":
                self._json(commits.commit(store, (body or {})["branch"]))

            elif path == "/api/undo":
                self._json(commits.undo(store, (body or {})["commit_id"]))

            elif path == "/api/resolve":
                self._json({"settled": len(P.resolve_due(store))})

            else:
                self._json({"error": f"no route {path}"}, 404)

        except (StoreError, ValueError, KeyError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001 - surface it, do not swallow it
            traceback.print_exc()
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        finally:
            store.close()


def serve(db: str, host: str = "127.0.0.1", port: int = 8787) -> None:
    Handler.db = db
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"antechamber on http://{host}:{port}  (db: {db})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
