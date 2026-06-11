"""Resident check-in providers for the fall-confirmation machine.

Public seam (BAT-22, extended for BAT-30):

* :class:`CheckInProvider` / :class:`CheckInResult` — the three-way contract
* :class:`StubCheckInProvider` — v1, always ``PENDING`` (times out)
* :class:`LocalAudioCheckInProvider` — real voice check-in
* :func:`build_checkin_provider` — config-only ``stub`` ↔ ``local_audio`` swap

Audio/spotter building blocks are re-exported for tests and the Pi runtime.
"""

from __future__ import annotations

from .audio import (
    AudioSource,
    SoundDeviceAudioSource,
    WavFileAudioSource,
    rms,
)
from .base import CheckInProvider, CheckInResult, StubCheckInProvider
from .factory import build_checkin_provider
from .local import AuditEvent, LocalAudioCheckInProvider, classify
from .spotter import (
    KeywordSpotter,
    Spotted,
    ToneKeywordSpotter,
    VoskKeywordSpotter,
    dominant_freq,
)

__all__ = [
    "AudioSource",
    "AuditEvent",
    "CheckInProvider",
    "CheckInResult",
    "KeywordSpotter",
    "LocalAudioCheckInProvider",
    "SoundDeviceAudioSource",
    "Spotted",
    "StubCheckInProvider",
    "ToneKeywordSpotter",
    "VoskKeywordSpotter",
    "WavFileAudioSource",
    "build_checkin_provider",
    "classify",
    "dominant_freq",
    "rms",
]
