"""``LocalAudioCheckInProvider`` — the real voice check-in (BAT-30).

Pipeline per poll: drain whatever the mic captured since the last poll, energy-
gate it (VAD), accumulate the speech frames, and once there's a real utterance
ask the keyword spotter what was said. Route the answer through the shared
three-way :class:`CheckInResult`:

* a clear/weak "I'm fine" / "yes" → ``OK`` (recovered)
* "help" → ``HELP`` (the machine escalates immediately)
* silence / TV-noise / anything unrecognised → ``PENDING`` until the window
  elapses, then the machine's timeout confirms — same as the stub

Privacy invariants enforced here (CLAUDE.md / brand commitments):

* The mic is opened only on :meth:`start` and hard-released the instant the
  listen window elapses, an answer is recognised, or :meth:`cancel` fires —
  whichever comes first. Open/close are logged as audited events with the
  reading timestamp, so capture is provably bounded by the window.
* Audio is read, classified, and discarded in memory. Nothing is written to
  disk and no audio leaves the device — only the three-way outcome does.

The provider is hardware-agnostic: it pulls PCM from an injected
:class:`AudioSource` factory and asks an injected :class:`KeywordSpotter`. Tests
wire WAV fixtures + a deterministic spotter through this very code; the Pi wires
a live ``sounddevice`` source + Vosk via :func:`ingest.checkin.factory`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from .audio import AudioSource, rms
from .base import CheckInResult
from .spotter import KeywordSpotter

log = logging.getLogger("hearth.ingest.checkin")

# A "help" keyword anywhere in the utterance wins; otherwise any reassurance
# keyword is an OK. Kept as plain sets so adding a phrase is data, not logic.
HELP_WORDS = frozenset({"help"})
OK_WORDS = frozenset({
    "im fine", "i'm fine", "i am fine", "im ok", "i'm ok", "i am ok",
    "im okay", "i'm okay", "yes", "ok", "okay", "fine",
})


def classify(text: str) -> CheckInResult:
    """Map recognised keyword text to a check-in outcome."""
    norm = " ".join(text.lower().replace(".", " ").replace(",", " ").split())
    if not norm:
        return CheckInResult.PENDING
    words = set(norm.split())
    if words & HELP_WORDS or "help" in norm:
        return CheckInResult.HELP
    if norm in OK_WORDS or words & OK_WORDS:
        return CheckInResult.OK
    return CheckInResult.PENDING


@dataclass(frozen=True)
class AuditEvent:
    """An audited microphone lifecycle event (no audio, only metadata)."""

    node_id: int
    event: str  # "mic_open" | "mic_close"
    at_ms: int
    reason: str | None = None


@dataclass
class _Session:
    source: AudioSource
    start_ms: int
    speech: bytearray = field(default_factory=bytearray)
    samplerate: int = 16000
    open: bool = True


class LocalAudioCheckInProvider:
    """Voice check-in backed by a real audio source + keyword spotter."""

    def __init__(
        self,
        *,
        source_factory: Callable[[int], AudioSource],
        spotter: KeywordSpotter,
        listen_window_secs: int = 45,
        confidence_threshold: float = 0.6,
        vad_energy_threshold: float = 350.0,
        min_speech_ms: int = 150,
    ) -> None:
        self._source_factory = source_factory
        self._spotter = spotter
        self._window_ms = listen_window_secs * 1000
        self._confidence_threshold = confidence_threshold
        self._vad_threshold = vad_energy_threshold
        self._min_speech_ms = min_speech_ms
        self._sessions: dict[int, _Session] = {}
        self.audit_events: list[AuditEvent] = []

    # -- audited lifecycle ---------------------------------------------------

    def _audit(self, node_id: int, event: str, at_ms: int, reason: str | None) -> None:
        self.audit_events.append(AuditEvent(node_id, event, at_ms, reason))
        log.info("checkin %s node=%s at=%dms%s", event, node_id, at_ms,
                 f" ({reason})" if reason else "")

    def start(self, node_id: int, at_ms: int) -> None:
        if node_id in self._sessions:
            return  # idempotent per incident
        source = self._source_factory(node_id)
        source.open()
        self._sessions[node_id] = _Session(
            source=source, start_ms=at_ms, samplerate=source.samplerate,
        )
        self._audit(node_id, "mic_open", at_ms, None)

    def _close(self, node_id: int, at_ms: int, reason: str) -> None:
        session = self._sessions.get(node_id)
        if session is None or not session.open:
            return
        session.source.close()
        session.open = False
        session.speech = bytearray()  # discard captured audio
        self._audit(node_id, "mic_close", at_ms, reason)

    def cancel(self, node_id: int) -> None:
        # Reuse the session's last-known time for the audit if we have it.
        session = self._sessions.get(node_id)
        if session is not None:
            self._close(node_id, session.start_ms + self._window_ms, "cancelled")
        self._sessions.pop(node_id, None)

    def is_capturing(self, node_id: int) -> bool:
        session = self._sessions.get(node_id)
        return bool(session and session.open)

    # -- the poll ------------------------------------------------------------

    def poll(self, node_id: int, now_ms: int) -> CheckInResult:
        session = self._sessions.get(node_id)
        if session is None or not session.open:
            return CheckInResult.PENDING

        # Hard mic release the moment the window is up — capture is bounded.
        if now_ms - session.start_ms >= self._window_ms:
            self._close(node_id, now_ms, "window_elapsed")
            return CheckInResult.PENDING

        self._capture(session)

        min_speech_bytes = int(session.samplerate * self._min_speech_ms / 1000) * 2
        if len(session.speech) < min_speech_bytes:
            return CheckInResult.PENDING

        spotted = self._spotter.recognize(bytes(session.speech), session.samplerate)
        if not spotted.text or spotted.confidence < self._confidence_threshold:
            return CheckInResult.PENDING

        outcome = classify(spotted.text)
        if outcome is CheckInResult.PENDING:
            return CheckInResult.PENDING
        self._close(node_id, now_ms,
                    "help_keyword" if outcome is CheckInResult.HELP else "ok_keyword")
        return outcome

    def _capture(self, session: _Session) -> None:
        """Drain everything buffered since the last poll, keeping speech frames."""
        while True:
            chunk = session.source.read()
            if not chunk:
                break
            if rms(chunk) >= self._vad_threshold:  # energy-gate VAD
                session.speech.extend(chunk)
