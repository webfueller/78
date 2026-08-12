"""What happened, in a form a person can read.

Every guarantee in this package is worth exactly as much as somebody's ability to
check it. A receipt nobody reads is a hash in a database; a chain nobody verifies
is a claim in a README. So this module turns the log into an account: what was
committed, what it touched, what was offered instead, what the agent predicted
and whether it was right, and whether the chain still verifies.

It knows nothing about any domain. An action is a kind and an entity, and a
caller who can say something better about it passes a `describe`.
"""

from __future__ import annotations

import datetime
import html as _html
from typing import Callable, Dict, List, Optional, Sequence

from . import events as E
from .preferences import PREFERENCES
from .projection import Projection
from .store import TRUNK, EventStore, StoreError

Describe = Callable[[dict], str]


def _when(ts: Optional[int]) -> str:
    if not ts:
        return "—"
    try:
        return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return str(ts)


def _short(h: Optional[str], n: int = 7) -> str:
    return (h or "")[:n] or "—"


def _default_describe(action: dict) -> str:
    return f"{action['kind']}  {action['entity']}"


# ------------------------------------------------------------------ gathering


def offers(store: EventStore) -> List[dict]:
    """What was on the table each time, and what was taken.

    Read straight off the preferences branch, which is kernel bookkeeping and so
    means the same thing in every domain. This is the part of an audit trail that
    is genuinely hard to get any other way: not just what the agent did, but what
    it considered and rejected.
    """
    if store.branch(PREFERENCES) is None:
        return []
    offered: Dict[str, dict] = {}
    for ev in store.read(PREFERENCES):
        if ev.kind == E.CHOICE_OFFERED:
            offered[ev.entity] = {
                "id": ev.entity,
                "at": ev.ts,
                "options": [
                    {"branch": o["branch"], "plan": o["plan"]} for o in ev.payload["options"]
                ],
                "chosen": None,
            }
        elif ev.kind == E.CHOICE_MADE and ev.entity in offered:
            offered[ev.entity]["chosen"] = ev.payload.get("branch")
    return sorted(offered.values(), key=lambda o: o["at"])


def history(store: EventStore, branch: str = TRUNK, limit: Optional[int] = None) -> List[dict]:
    """Every commit, newest last, with what it did and what it was chosen over."""
    events = store.read(branch)
    state = Projection.fold(events)

    promoted: Dict[str, List[dict]] = {}
    for ev in events:
        if ev.commit_id:
            promoted.setdefault(ev.commit_id, []).append(
                {"kind": ev.kind, "entity": ev.entity, "ts": ev.ts, "hash": ev.hash}
            )

    by_branch: Dict[str, dict] = {}
    for offer in offers(store):
        for option in offer["options"]:
            by_branch[option["branch"]] = offer

    rows = []
    for cid, c in sorted(state.commits.items(), key=lambda kv: kv[1]["opened_at"]):
        receipt = c.get("receipt", {})
        offer = by_branch.get(c["branch"])
        alternatives = []
        if offer:
            alternatives = [
                o["plan"] for o in offer["options"] if o["branch"] != c["branch"]
            ]
        rows.append({
            "id": cid,
            "branch": c["branch"],
            "state": c["state"],
            "opened_at": c["opened_at"],
            "sealed_at": c["sealed_at"],
            "undone_at": c["undone_at"],
            "actions": promoted.get(cid) or c.get("actions", []),
            "planned": len(c.get("actions", [])),
            "state_before": receipt.get("state_before"),
            "state_after": receipt.get("state_after"),
            "undo_until": receipt.get("undo_until"),
            "chosen_over": alternatives,
            "rehearsal": offer["id"] if offer else None,
        })
    return rows[-limit:] if limit else rows


def claims(store: EventStore) -> dict:
    """How the agent's own predictions turned out.

    Reads the ledger branch through a bare projection: claims and outcomes are
    kernel events, so this needs no resolvers and no domain.
    """
    from .ledger import LEDGER, brier, leave_one_out_base_rates

    if store.branch(LEDGER) is None:
        return {"total": 0, "resolved": 0}
    recs = list(Projection.fold(store.read(LEDGER)).predictions.values())
    settled = [r for r in recs if r["outcome"] is not None]
    out = {"total": len(recs), "resolved": len(settled), "pending": len(recs) - len(settled)}
    if not settled:
        return out

    pairs = [(r["p"], bool(r["outcome"])) for r in settled]
    outcomes = [o for _, o in pairs]
    base = list(zip(leave_one_out_base_rates(outcomes), outcomes))
    b_model, b_base = brier(pairs), brier(base)
    out.update({
        "brier": round(b_model, 4),
        "baseline_brier": round(b_base, 4),
        "right": sum(1 for p, o in pairs if (p >= 0.5) == o),
        "verdict": "beats the base rate" if b_model < b_base else "does not beat the base rate",
    })
    return out


def integrity(store: EventStore, branch: str = TRUNK) -> dict:
    try:
        return {"branch": branch, "events": store.verify(branch), "ok": True, "why": ""}
    except StoreError as exc:
        return {"branch": branch, "events": 0, "ok": False, "why": str(exc)}


def summary(store: EventStore, branch: str = TRUNK, limit: Optional[int] = None) -> dict:
    rows = history(store, branch=branch, limit=limit)
    return {
        "branch": branch,
        "commits": rows,
        "committed": sum(1 for r in rows if r["state"] == "sealed"),
        "undone": sum(1 for r in rows if r["state"] == "undone"),
        "unfinished": sum(1 for r in rows if r["state"] == "open"),
        "claims": claims(store),
        "integrity": integrity(store, branch),
    }


# ------------------------------------------------------------------ rendering


STATE_WORD = {"sealed": "COMMITTED", "undone": "UNDONE", "open": "UNFINISHED"}


def render_text(
    store: EventStore,
    branch: str = TRUNK,
    limit: Optional[int] = None,
    describe: Optional[Describe] = None,
    now: Optional[int] = None,
) -> str:
    describe = describe or _default_describe
    data = summary(store, branch=branch, limit=limit)
    lines: List[str] = []

    head = (
        f"{data['committed']} committed"
        + (f", {data['undone']} undone" if data["undone"] else "")
        + (f", {data['unfinished']} unfinished" if data["unfinished"] else "")
    )
    lines.append(f"AUDIT — {branch} — {head}")
    lines.append("")

    if not data["commits"]:
        lines.append("  Nothing has been committed on this branch.")
    for row in data["commits"]:
        lines.append(f"{_when(row['sealed_at'] or row['opened_at'])}  {row['id']}  "
                     f"{STATE_WORD.get(row['state'], row['state'])}")
        if row["chosen_over"]:
            lines.append(f"    chosen over: {', '.join(row['chosen_over'])}")
        for a in row["actions"]:
            lines.append(f"    · {describe(a)}")
        if row["state_before"]:
            lines.append(f"    state {_short(row['state_before'])} → "
                         f"{_short(row['state_after'])}")
        if row["state"] == "undone":
            lines.append(f"    undone {_when(row['undone_at'])} — the state hash above "
                         f"was restored exactly")
        elif row["undo_until"]:
            shut = row["undo_until"] < (now if now is not None else _nowish())
            lines.append(f"    undo {'closed' if shut else 'open until'} "
                         f"{_when(row['undo_until'])}")
        lines.append("")

    c = data["claims"]
    if c.get("resolved"):
        lines.append(f"Predictions: {c['resolved']} settled, {c['right']} called correctly. "
                     f"Brier {c['brier']} against {c['baseline_brier']} for the base rate "
                     f"— {c['verdict']}.")
    elif c.get("total"):
        lines.append(f"Predictions: {c['total']} made, none settled yet.")

    i = data["integrity"]
    lines.append(
        f"Chain: {i['events']} events verified." if i["ok"]
        else f"Chain: FAILED — {i['why']}"
    )
    return "\n".join(lines)


def _nowish() -> int:
    import time
    return int(time.time())


def render_html(
    store: EventStore,
    branch: str = TRUNK,
    limit: Optional[int] = None,
    describe: Optional[Describe] = None,
    title: str = "Audit",
) -> str:
    """A single self-contained page. No scripts, no fonts, nothing fetched.

    It is going to be read by somebody deciding whether to trust an agent with
    their filesystem, and a page that phones home while making that argument
    would be answering the question the wrong way.
    """
    describe = describe or _default_describe
    data = summary(store, branch=branch, limit=limit)
    e = _html.escape

    rows = []
    for row in reversed(data["commits"]):
        actions = "".join(
            f"<li><code>{e(describe(a))}</code></li>" for a in row["actions"]
        ) or "<li class=q>no actions recorded</li>"
        alts = (
            f"<p class=q>chosen over {e(', '.join(row['chosen_over']))}</p>"
            if row["chosen_over"] else ""
        )
        hashes = (
            f"<p class=q>state <code>{_short(row['state_before'], 12)}</code> → "
            f"<code>{_short(row['state_after'], 12)}</code></p>"
            if row["state_before"] else ""
        )
        undo = ""
        if row["state"] == "undone":
            undo = (f"<p class=q>undone {_when(row['undone_at'])}; the state hash above "
                    f"was restored exactly</p>")
        elif row["undo_until"]:
            undo = f"<p class=q>undo window until {_when(row['undo_until'])}</p>"

        rows.append(f"""
        <article class="c {e(row['state'])}">
          <header>
            <span class=state>{e(STATE_WORD.get(row['state'], row['state']))}</span>
            <time>{_when(row['sealed_at'] or row['opened_at'])}</time>
            <code class=id>{e(row['id'])}</code>
          </header>
          {alts}
          <ul>{actions}</ul>
          {hashes}{undo}
        </article>""")

    c = data["claims"]
    if c.get("resolved"):
        claims_line = (f"{c['resolved']} settled, {c['right']} called correctly. "
                       f"Brier {c['brier']} against {c['baseline_brier']} for the base "
                       f"rate — {c['verdict']}.")
    elif c.get("total"):
        claims_line = f"{c['total']} made, none settled yet."
    else:
        claims_line = "none recorded."

    i = data["integrity"]
    chain = (f"{i['events']} events verified" if i["ok"] else f"FAILED — {e(i['why'])}")

    return f"""<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<style>
:root {{ --bg:#fbfbfa; --fg:#1a1a18; --q:#6b6b66; --line:#e2e2dd; --card:#fff;
         --ok:#2b6b3f; --undone:#8a6d1f; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#141414; --fg:#eceae4; --q:#98968f; --line:#2c2c2a; --card:#1c1c1b;
           --ok:#7fbf90; --undone:#d6b45a; }}
}}
* {{ box-sizing:border-box }}
body {{ margin:0; padding:2.5rem 1.25rem 4rem; background:var(--bg); color:var(--fg);
        font:15px/1.55 ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif; }}
main {{ max-width:46rem; margin:0 auto }}
h1 {{ font-size:1.35rem; margin:0 0 .25rem }}
.sub {{ color:var(--q); margin:0 0 2rem }}
.c {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
      padding:1rem 1.15rem; margin:0 0 .9rem }}
.c header {{ display:flex; gap:.6rem; align-items:baseline; flex-wrap:wrap;
             margin-bottom:.5rem }}
.state {{ font-size:.7rem; letter-spacing:.08em; font-weight:700; color:var(--ok) }}
.undone .state {{ color:var(--undone) }}
time {{ color:var(--q); font-size:.85rem }}
.id {{ color:var(--q); font-size:.8rem; margin-left:auto }}
ul {{ margin:.4rem 0; padding-left:1.1rem }}
li {{ margin:.15rem 0 }}
code {{ font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
        overflow-wrap:anywhere }}
.q {{ color:var(--q); font-size:.85rem; margin:.35rem 0 0 }}
footer {{ margin-top:2rem; padding-top:1rem; border-top:1px solid var(--line);
          color:var(--q); font-size:.9rem }}
footer b {{ color:var(--fg); font-weight:600 }}
</style></head><body><main>
<h1>{e(title)}</h1>
<p class=sub>{data['committed']} committed &middot; {data['undone']} undone
&middot; branch <code>{e(branch)}</code></p>
{''.join(rows) or '<p class=q>Nothing has been committed on this branch.</p>'}
<footer>
<p><b>Predictions.</b> {e(claims_line)}</p>
<p><b>Chain.</b> {chain}. Recomputing every hash detects a rewritten payload or a
deleted event; the branch also carries a count and a head hash, so a truncated
tail is caught too. It is not a signature — someone with write access to the file
can update both.</p>
</footer>
</main></body></html>"""
