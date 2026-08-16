"""What can happen in a life made of mail, meetings and money.

The kinds below are this domain's whole vocabulary. Everything structural --
what an event *is*, how it is hashed, who is allowed to author one, what a
commit or a claim looks like -- belongs to the kernel and is imported from
`takeback`, not redefined here.

Anything an agent proposes in a fork uses the same kinds a real observation
would, so a rehearsed week and a lived week project through identical code.
"""

from __future__ import annotations

from takeback.events import (  # noqa: F401  (this module is the domain's event namespace)
    ACTOR_AGENT,
    ACTOR_USER,
    ACTOR_WORLD,
    COMMIT_OPENED,
    COMMIT_SEALED,
    COMMIT_UNDONE,
    GENESIS,
    KERNEL_KINDS,
    PREDICTION_MADE,
    PREDICTION_RESOLVED,
    REAL_ACTORS,
    SIM_PREFIX,
    Event,
    canonical,
    digest,
    is_simulated,
)

# Mail.
MESSAGE_RECEIVED = "message.received"
MESSAGE_SENT = "message.sent"
CONTACT_OBSERVED = "contact.observed"

# Calendar.
CALENDAR_SCHEDULED = "calendar.scheduled"
CALENDAR_MOVED = "calendar.moved"
CALENDAR_CANCELLED = "calendar.cancelled"

# Money.
SUBSCRIPTION_OBSERVED = "money.subscription_observed"
SUBSCRIPTION_CHARGED = "money.charged"
SUBSCRIPTION_CANCELLED = "money.cancelled"

DOMAIN_KINDS = frozenset({
    MESSAGE_RECEIVED, MESSAGE_SENT, CONTACT_OBSERVED,
    CALENDAR_SCHEDULED, CALENDAR_MOVED, CALENDAR_CANCELLED,
    SUBSCRIPTION_OBSERVED, SUBSCRIPTION_CHARGED, SUBSCRIPTION_CANCELLED,
})
