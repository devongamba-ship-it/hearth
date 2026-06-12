"""Notification delivery for the cascade.

Two implementations behind one interface:

* :class:`DryRunNotifier` — the default and the only path CI/tests exercise. It
  records every action in memory and makes **no external call** and requires
  **no credentials** (CLAUDE.md #6, #7).
* :class:`TwilioNotifier` — live SMS/voice via Twilio, selected only when a human
  sets ``ESCALATION_MODE=live``. The Twilio SDK is lazy-imported so it is never a
  CI dependency, and credentials come from env, never code.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from .cascade import is_dry_run

logger = logging.getLogger("hearth.escalation")

SMS = "sms"
PUSH = "push"
VOICE = "voice"


@dataclass
class SentMessage:
    channel: str
    to: str
    body: str


class Notifier:
    """Interface: send one notification on a channel to a recipient."""

    def send(self, channel: str, to: str, body: str) -> SentMessage:  # pragma: no cover
        raise NotImplementedError


@dataclass
class DryRunNotifier(Notifier):
    """Records actions instead of contacting anyone. Side-effect free."""

    sent: list[SentMessage] = field(default_factory=list)

    def send(self, channel: str, to: str, body: str) -> SentMessage:
        msg = SentMessage(channel=channel, to=to, body=body)
        self.sent.append(msg)
        logger.info("DRY-RUN %s -> %s: %s", channel, to, body,
                    extra={"event": "notify_dry_run", "status": "dry_run"})
        return msg


class TwilioNotifier(Notifier):
    """Live Twilio delivery. Human-set only; never used in CI.

    Credentials are read from the environment:
    ``TWILIO_ACCOUNT_SID``, ``TWILIO_AUTH_TOKEN``, ``TWILIO_FROM_NUMBER``.
    """

    def __init__(self) -> None:
        self._sid = os.environ.get("TWILIO_ACCOUNT_SID")
        self._token = os.environ.get("TWILIO_AUTH_TOKEN")
        self._from = os.environ.get("TWILIO_FROM_NUMBER")
        if not all([self._sid, self._token, self._from]):
            raise RuntimeError(
                "live escalation requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
                "and TWILIO_FROM_NUMBER in the environment"
            )

    def _client(self):
        # Lazy import: the SDK is an optional [live] dependency, absent in CI.
        from twilio.rest import Client  # type: ignore

        return Client(self._sid, self._token)

    def send(self, channel: str, to: str, body: str) -> SentMessage:  # pragma: no cover
        client = self._client()
        if channel == VOICE:
            twiml = f"<Response><Gather numDigits='1'><Say>{body}</Say></Gather></Response>"
            client.calls.create(to=to, from_=self._from, twiml=twiml)
        else:
            client.messages.create(to=to, from_=self._from, body=body)
        return SentMessage(channel=channel, to=to, body=body)


def make_notifier() -> Notifier:
    """Pick the notifier for the current mode. Dry-run is the safe default."""
    if is_dry_run():
        return DryRunNotifier()
    return TwilioNotifier()
