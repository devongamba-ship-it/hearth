"""Contact-circle configuration for the escalation cascade.

The cascade is config-driven (``contacts.yaml``). A contact has a name, an SMS
number, an optional voice number (defaults to the SMS number), and a numeric
``priority`` (lower = called first). ``channels`` selects which T+0 broadcast
channels reach the contact; voice calls always use the voice number.

Schema (see ``contacts.example.yaml``):

    home_name: "Rosewood"
    contacts:
      - name: "Dana"
        sms: "+15555550101"
        voice: "+15555550101"   # optional; defaults to sms
        priority: 1
        channels: ["sms", "push"]   # optional; defaults to ["sms", "push"]
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CHANNELS = ("sms", "push")


@dataclass(frozen=True)
class Contact:
    name: str
    sms: str
    priority: int
    voice: str
    channels: tuple[str, ...] = field(default=DEFAULT_CHANNELS)


@dataclass(frozen=True)
class CircleConfig:
    home_name: str
    contacts: tuple[Contact, ...]

    def by_priority(self) -> list[Contact]:
        """Contacts ordered for the voice-call ladder (lowest priority first)."""
        return sorted(self.contacts, key=lambda c: (c.priority, c.name))

    def find_by_phone(self, phone: str) -> Contact | None:
        """Resolve an inbound caller/sender number to a known contact."""
        digits = _normalize(phone)
        for contact in self.contacts:
            if _normalize(contact.sms) == digits or _normalize(contact.voice) == digits:
                return contact
        return None


def _normalize(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def _parse(data: dict) -> CircleConfig:
    if not isinstance(data, dict):
        raise ValueError("contacts config must be a mapping")
    home_name = data.get("home_name")
    if not home_name:
        raise ValueError("contacts config requires a non-empty 'home_name'")
    raw_contacts = data.get("contacts") or []
    if not raw_contacts:
        raise ValueError("contacts config requires at least one contact")

    contacts: list[Contact] = []
    for entry in raw_contacts:
        name = entry.get("name")
        sms = entry.get("sms")
        if not name or not sms:
            raise ValueError("each contact requires 'name' and 'sms'")
        channels = tuple(entry.get("channels") or DEFAULT_CHANNELS)
        contacts.append(
            Contact(
                name=name,
                sms=sms,
                voice=entry.get("voice") or sms,
                priority=int(entry.get("priority", 100)),
                channels=channels,
            )
        )
    return CircleConfig(home_name=home_name, contacts=tuple(contacts))


def load_config(path: str | os.PathLike | None = None) -> CircleConfig:
    """Load the contact circle from ``path`` or ``$HEARTH_CONTACTS_FILE``."""
    resolved = path or os.environ.get("HEARTH_CONTACTS_FILE")
    if not resolved:
        raise FileNotFoundError(
            "no contacts file: set HEARTH_CONTACTS_FILE or pass a path "
            "(see contacts.example.yaml)"
        )
    text = Path(resolved).read_text(encoding="utf-8")
    return _parse(yaml.safe_load(text))


def load_config_from_text(text: str) -> CircleConfig:
    """Parse a config from a YAML string (used by tests)."""
    return _parse(yaml.safe_load(text))
