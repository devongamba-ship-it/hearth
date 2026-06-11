"""Keyword-spotter seam for the local voice check-in.

The recognizer is the one piece a real install needs an offline model for, so it
lives behind its own pluggable seam: ``poll`` captures + windows audio, then asks
a :class:`KeywordSpotter` "what, if anything, was said?". On a Pi that is
:class:`VoskKeywordSpotter` (small offline en model, lazy-imported). In CI it is a
deterministic spotter so the acceptance fixtures run through the *real*
capture → VAD → window pipeline with no model download and no audio device
(BAT-30 / CANONICAL DESIGN UPDATE).

Recognition is yes/no-class only — a handful of keywords — never transcription
or storage of free speech (CLAUDE.md privacy invariant).
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Spotted:
    """What the recognizer heard. Empty ``text`` means "nothing recognised"."""

    text: str
    confidence: float


class KeywordSpotter(Protocol):
    """Maps a captured utterance to recognised keyword text (or empty)."""

    def recognize(self, pcm: bytes, samplerate: int) -> Spotted:
        ...


def dominant_freq(pcm: bytes, samplerate: int) -> float:
    """Estimate the dominant frequency (Hz) of 16-bit mono PCM by zero crossings.

    For a tone this lands near its frequency; for broadband noise it lands near
    ``samplerate/4`` — i.e. far outside any keyword band, which is exactly how the
    deterministic spotter rejects TV-noise fixtures.
    """
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if len(samples) < 2:
        return 0.0
    crossings = 0
    prev = samples[0]
    for s in samples[1:]:
        if (prev < 0) != (s < 0):
            crossings += 1
        prev = s
    return crossings * samplerate / (2 * len(samples))


# Deterministic fixture vocabulary: tone frequency band -> spoken phrase. The
# acceptance fixtures synthesise these tones, so the recognizer genuinely derives
# the phrase from the PCM rather than from any out-of-band label.
_TONE_BANDS: tuple[tuple[float, float, str], ...] = (
    (200.0, 90.0, "i'm fine"),
    (440.0, 90.0, "help"),
)


class ToneKeywordSpotter:
    """Deterministic, model-free recognizer for the CI fixtures.

    Estimates the dominant frequency of the captured audio and matches it to a
    keyword band, with a confidence that falls off with distance from band
    centre. Broadband noise lands in no band → empty result.
    """

    def recognize(self, pcm: bytes, samplerate: int) -> Spotted:
        freq = dominant_freq(pcm, samplerate)
        best = Spotted("", 0.0)
        for centre, tol, phrase in _TONE_BANDS:
            confidence = 1.0 - abs(freq - centre) / tol
            if confidence > best.confidence:
                best = Spotted(phrase if confidence > 0 else "", max(confidence, 0.0))
        return best


class VoskKeywordSpotter:  # pragma: no cover - requires the offline model
    """Offline Vosk recognizer for real Pi installs.

    ``vosk`` and the model are loaded lazily on first use, so this is never a CI
    or import-time dependency (the issue: "Do not bundle Vosk into CI"). Restrict
    recognition to the keyword grammar so it stays a yes/no-class spotter.
    """

    _GRAMMAR = '["i\'m fine", "i\'m ok", "yes", "help", "[unk]"]'

    def __init__(self, model_path: str, *, samplerate: int = 16000) -> None:
        self.model_path = model_path
        self._samplerate = samplerate
        self._rec = None

    def _recognizer(self):
        if self._rec is None:
            from vosk import KaldiRecognizer, Model  # lazy: optional dependency

            self._rec = KaldiRecognizer(
                Model(self.model_path), self._samplerate, self._GRAMMAR,
            )
        return self._rec

    def recognize(self, pcm: bytes, samplerate: int) -> Spotted:
        import json

        rec = self._recognizer()
        rec.AcceptWaveform(pcm)
        result = json.loads(rec.FinalResult())
        text = (result.get("text") or "").strip()
        if not text or text == "[unk]":
            return Spotted("", 0.0)
        # Vosk's small grammar mode does not emit per-word confidence; treat any
        # in-grammar hit as confident and let the provider apply its threshold.
        return Spotted(text, 1.0)
