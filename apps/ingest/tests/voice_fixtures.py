"""Synthetic voice-check-in audio fixtures (BAT-30).

The acceptance criteria call for WAV fixtures — clear "I'm fine", a weak/distant
"I'm fine", "help", silence, and TV noise — injected through the *same* code path
as a live capture. We synthesise them deterministically so CI needs no recorded
audio, no model, and no sound device:

* Spoken keywords are pure tones at distinct frequencies. The deterministic
  ``ToneKeywordSpotter`` derives the phrase from the tone, so recognition runs on
  the real PCM (not an out-of-band label) through the real VAD → window pipeline.
    - 200 Hz  -> "I'm fine"      (loud = clear, quiet = weak/distant but audible)
    - 440 Hz  -> "help"
* Silence is zeros — gated out by the energy VAD, so it never reaches the spotter
  (it must time out, exactly like a real unanswered check-in).
* TV noise is broadband: loud enough to pass the VAD but with no dominant tone,
  so the spotter recognises nothing (it must also time out).

Everything is 16-bit mono, the format both the WAV source and a live mic yield.
"""

from __future__ import annotations

import math
import random
import wave
from array import array

SAMPLERATE = 16000


def tone(freq: float, ms: int, amplitude: int, samplerate: int = SAMPLERATE) -> bytes:
    """A pure sine tone as signed 16-bit little-endian mono PCM."""
    n = int(samplerate * ms / 1000)
    samples = array(
        "h",
        (int(amplitude * math.sin(2 * math.pi * freq * i / samplerate)) for i in range(n)),
    )
    return samples.tobytes()


def silence(ms: int, samplerate: int = SAMPLERATE) -> bytes:
    return array("h", bytes(int(samplerate * ms / 1000) * 2)).tobytes()


def noise(ms: int, amplitude: int, *, seed: int, samplerate: int = SAMPLERATE) -> bytes:
    """Deterministic broadband noise (a TV in the background)."""
    rng = random.Random(seed)
    n = int(samplerate * ms / 1000)
    samples = array("h", (rng.randint(-amplitude, amplitude) for _ in range(n)))
    return samples.tobytes()


def write_wav(path, pcm: bytes, samplerate: int = SAMPLERATE) -> str:
    """Write mono 16-bit PCM to ``path`` as a real .wav and return its path."""
    path = str(path)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm)
    return path


# The five named fixtures. 600 ms each — comfortably above the provider's
# min-speech gate. Amplitudes are chosen around the default VAD RMS (~350):
# clear/help/noise are well above it, the weak voice is audibly above it, and
# silence is below it.
CLEAR_FINE = tone(200, 600, amplitude=8000)
WEAK_FINE = tone(200, 600, amplitude=900)
HELP = tone(440, 600, amplitude=8000)
SILENCE = silence(600)
TV_NOISE = noise(600, amplitude=6000, seed=7)
