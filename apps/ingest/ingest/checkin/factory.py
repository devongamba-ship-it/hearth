"""Build a :class:`CheckInProvider` from config — the config-only provider swap.

AC#3 / the BAT-22 invariant: switching ``stub`` ↔ ``local_audio`` is a one-line
``detection.yaml`` change, no code edits. The runtime wiring
(``ingest.fall_machine.main``) calls this; the machine itself never names a
concrete provider.

Both ``SoundDeviceAudioSource`` and ``VoskKeywordSpotter`` defer their heavy
imports to first use, so constructing the local-audio provider here is
dependency-free — the choice can be exercised in CI without sounddevice/Vosk.
"""

from __future__ import annotations

from .audio import SoundDeviceAudioSource
from .base import CheckInProvider, StubCheckInProvider
from .local import LocalAudioCheckInProvider
from .spotter import VoskKeywordSpotter


def build_checkin_provider(config) -> CheckInProvider:
    """Return the provider named by ``config.checkin.provider``."""
    cc = config.checkin
    if cc.provider == "stub":
        return StubCheckInProvider()
    if cc.provider == "local_audio":
        if not cc.model_path:
            raise ValueError(
                "checkin.provider=local_audio needs checkin.model_path "
                "(path to the offline Vosk model)"
            )
        spotter = VoskKeywordSpotter(cc.model_path, samplerate=cc.samplerate)

        def source_factory(node_id: int) -> SoundDeviceAudioSource:  # noqa: ARG001
            return SoundDeviceAudioSource(device=cc.device, samplerate=cc.samplerate)

        return LocalAudioCheckInProvider(
            source_factory=source_factory,
            spotter=spotter,
            listen_window_secs=cc.listen_window_secs,
            confidence_threshold=cc.confidence_threshold,
            vad_energy_threshold=cc.vad_energy_threshold,
        )
    raise ValueError(f"unknown checkin provider: {cc.provider!r}")
