"""Audio capture seam for the local voice check-in.

The provider does not care whether PCM comes from a live USB speakerphone or a
WAV fixture — it pulls 16-bit mono frames from an :class:`AudioSource` and runs
them through the same VAD → window → recognizer pipeline. That is exactly what
lets the acceptance tests inject fixtures "through the same code path as live
capture" (BAT-30) without an audio device in CI.

Privacy invariant (CLAUDE.md): a source is opened only inside an active
check-in window and hard-released on ``stop()``. Nothing here writes audio to
disk — frames are read, classified, and discarded in memory.

Only stdlib (`wave`, `array`) is used for the fixture path so CI needs no
numpy/sounddevice. The live source imports ``sounddevice`` lazily, so merely
constructing it (e.g. via the config factory) never pulls the dependency.
"""

from __future__ import annotations

import wave
from array import array
from typing import Protocol


def rms(pcm: bytes) -> float:
    """Root-mean-square energy of signed 16-bit little-endian mono PCM."""
    if not pcm:
        return 0.0
    samples = array("h")
    # Drop a trailing odd byte rather than raising on a short read.
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return 0.0
    acc = 0
    for s in samples:
        acc += s * s
    return (acc / len(samples)) ** 0.5


class AudioSource(Protocol):
    """A mono 16-bit PCM source bounded by an explicit open/close lifecycle."""

    samplerate: int

    def open(self) -> None:
        """Begin capture (the audited mic-open)."""

    def read(self) -> bytes:
        """Return PCM captured since the last read (``b""`` when nothing new)."""

    def close(self) -> None:
        """Hard-release capture (the audited mic-close)."""


class WavFileAudioSource:
    """Replays a WAV file (or raw PCM) as if it were a live capture.

    The whole clip is decoded into memory on :meth:`open` and handed out in
    ``chunk_ms`` slices across successive :meth:`read` calls, so a poller draining
    it frame-by-frame exercises the identical path a live mic would. Used by the
    BAT-30 acceptance fixtures; works for any real ``.wav`` too.
    """

    def __init__(
        self,
        *,
        path: str | None = None,
        pcm: bytes | None = None,
        samplerate: int = 16000,
        chunk_ms: int = 100,
    ) -> None:
        if (path is None) == (pcm is None):
            raise ValueError("provide exactly one of path= or pcm=")
        self._path = path
        self._pcm = pcm
        self.samplerate = samplerate
        self._chunk_ms = chunk_ms
        self._buf = b""
        self._pos = 0
        self._open = False

    def open(self) -> None:
        if self._path is not None:
            with wave.open(self._path, "rb") as w:
                if w.getsampwidth() != 2 or w.getnchannels() != 1:
                    raise ValueError("fixture WAVs must be 16-bit mono")
                self.samplerate = w.getframerate()
                self._buf = w.readframes(w.getnframes())
        else:
            self._buf = self._pcm or b""
        self._pos = 0
        self._open = True

    def read(self) -> bytes:
        if not self._open:
            return b""
        step = int(self.samplerate * self._chunk_ms / 1000) * 2  # bytes per chunk
        chunk = self._buf[self._pos : self._pos + step]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        self._open = False
        self._buf = b""
        self._pos = 0


class SoundDeviceAudioSource:  # pragma: no cover - requires hardware
    """Live capture from a USB speakerphone via ``sounddevice`` (Pi runtime).

    Imported lazily so the dependency is needed only when actually capturing —
    constructing this (config factory, type checks) stays import-free, keeping CI
    and the stub path clean.
    """

    def __init__(self, *, device: str | int | None = None, samplerate: int = 16000,
                 blocksize_ms: int = 100) -> None:
        self.device = device
        self.samplerate = samplerate
        self._blocksize_ms = blocksize_ms
        self._stream = None

    def open(self) -> None:
        import sounddevice as sd  # lazy: hardware-only dependency

        blocksize = int(self.samplerate * self._blocksize_ms / 1000)
        self._stream = sd.RawInputStream(
            samplerate=self.samplerate, channels=1, dtype="int16",
            device=self.device, blocksize=blocksize,
        )
        self._stream.start()

    def read(self) -> bytes:
        if self._stream is None:
            return b""
        available = self._stream.read_available
        if not available:
            return b""
        data, _overflowed = self._stream.read(available)
        return bytes(data)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
