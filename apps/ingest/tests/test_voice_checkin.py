"""Voice check-in provider tests (BAT-30).

Every acceptance criterion is covered here, all against *simulated* audio routed
through the real capture → VAD → window → spotter → audit pipeline (no audio
device, no model in CI):

* fixture outcomes: clear & weak "I'm fine" -> recovered; "help" -> immediate
  CONFIRMED_FALL; silence / TV-noise -> timeout -> CONFIRMED_FALL
* mic-open / mic-close audited with timestamps; capture bounded by the window
* state-machine integration: swapping providers is config-only, no machine edits

The deterministic ``ToneKeywordSpotter`` stands in for Vosk; the WAV fixtures are
synthesised in :mod:`tests.voice_fixtures`.
"""

from __future__ import annotations

import pytest

from ingest import db
from ingest.checkin import (
    AuditEvent,
    CheckInResult,
    LocalAudioCheckInProvider,
    StubCheckInProvider,
    ToneKeywordSpotter,
    VoskKeywordSpotter,
    WavFileAudioSource,
    build_checkin_provider,
    classify,
    dominant_freq,
    rms,
)
from ingest.detection import DetectionConfig, load_config_from_text
from ingest.escalator import NullEscalator
from ingest.fall_machine import FallMachine, Frame, State

from tests import voice_fixtures as vf

SR = vf.SAMPLERATE


# -- low-level building blocks ------------------------------------------------


def test_rms_gates_silence_below_speech():
    assert rms(vf.SILENCE) == 0.0
    assert rms(vf.WEAK_FINE) > 350.0  # audibly above the default VAD floor
    assert rms(vf.CLEAR_FINE) > rms(vf.WEAK_FINE)


def test_dominant_freq_tracks_tone_and_rejects_noise():
    assert dominant_freq(vf.CLEAR_FINE, SR) == pytest.approx(200, abs=15)
    assert dominant_freq(vf.HELP, SR) == pytest.approx(440, abs=15)
    # Broadband noise lands far outside any keyword band.
    assert dominant_freq(vf.TV_NOISE, SR) > 1500


def test_tone_spotter_recognises_keywords():
    sp = ToneKeywordSpotter()
    assert sp.recognize(vf.CLEAR_FINE, SR).text == "i'm fine"
    assert sp.recognize(vf.WEAK_FINE, SR).text == "i'm fine"  # frequency, not volume
    assert sp.recognize(vf.HELP, SR).text == "help"
    assert sp.recognize(vf.TV_NOISE, SR).text == ""


@pytest.mark.parametrize(
    "text, expected",
    [
        ("help", CheckInResult.HELP),
        ("help me", CheckInResult.HELP),
        ("i'm fine", CheckInResult.OK),
        ("im ok", CheckInResult.OK),
        ("yes", CheckInResult.OK),
        ("", CheckInResult.PENDING),
        ("the weather", CheckInResult.PENDING),
    ],
)
def test_classify(text, expected):
    assert classify(text) is expected


# -- provider, exercised directly through poll() ------------------------------


def make_provider(pcm: bytes, **kw) -> LocalAudioCheckInProvider:
    return LocalAudioCheckInProvider(
        source_factory=lambda node_id: WavFileAudioSource(pcm=pcm, samplerate=SR),
        spotter=ToneKeywordSpotter(),
        **kw,
    )


def test_help_recognised_immediately():
    p = make_provider(vf.HELP)
    p.start(1, 0)
    # First poll drains the buffered utterance — recognised at once, no waiting.
    assert p.poll(1, 100) is CheckInResult.HELP
    assert not p.is_capturing(1)  # mic hard-released on recognition


@pytest.mark.parametrize("pcm", [vf.CLEAR_FINE, vf.WEAK_FINE])
def test_reassurance_recovers(pcm):
    p = make_provider(pcm)
    p.start(1, 0)
    assert p.poll(1, 100) is CheckInResult.OK


@pytest.mark.parametrize("pcm", [vf.SILENCE, vf.TV_NOISE])
def test_no_answer_stays_pending_until_window(pcm):
    p = make_provider(pcm, listen_window_secs=45)
    p.start(1, 0)
    assert p.poll(1, 100) is CheckInResult.PENDING
    assert p.poll(1, 44_000) is CheckInResult.PENDING
    assert p.is_capturing(1)  # still inside the window
    # At the window boundary the mic is released and stays PENDING.
    assert p.poll(1, 45_000) is CheckInResult.PENDING
    assert not p.is_capturing(1)


def test_audit_events_bracket_the_capture_with_timestamps():
    p = make_provider(vf.SILENCE, listen_window_secs=45)
    p.start(1, 1_000)
    for t in (1_100, 46_000):  # last poll crosses the 45s window
        p.poll(1, t)
    opens = [e for e in p.audit_events if e.event == "mic_open"]
    closes = [e for e in p.audit_events if e.event == "mic_close"]
    assert len(opens) == 1 and len(closes) == 1
    assert opens[0] == AuditEvent(1, "mic_open", 1_000, None)
    assert closes[0].reason == "window_elapsed"
    # Capture provably bounded: open->close span never exceeds the window.
    assert closes[0].at_ms - opens[0].at_ms <= 45 * 1000


def test_help_audit_reason_and_single_close():
    p = make_provider(vf.HELP)
    p.start(1, 0)
    p.poll(1, 100)
    p.cancel(1)  # the machine also cancels — must not double-log a close
    closes = [e for e in p.audit_events if e.event == "mic_close"]
    assert len(closes) == 1
    assert closes[0].reason == "help_keyword"


def test_low_confidence_is_rejected():
    # A spotter that always answers, but below the provider's threshold.
    class Unsure:
        def recognize(self, pcm, samplerate):
            from ingest.checkin import Spotted

            return Spotted("help", 0.2)

    p = LocalAudioCheckInProvider(
        source_factory=lambda n: WavFileAudioSource(pcm=vf.HELP, samplerate=SR),
        spotter=Unsure(),
        confidence_threshold=0.6,
    )
    p.start(1, 0)
    assert p.poll(1, 100) is CheckInResult.PENDING


def test_fixture_reads_from_a_real_wav_file(tmp_path):
    path = vf.write_wav(tmp_path / "help.wav", vf.HELP, SR)
    p = LocalAudioCheckInProvider(
        source_factory=lambda n: WavFileAudioSource(path=path),
        spotter=ToneKeywordSpotter(),
    )
    p.start(1, 0)
    assert p.poll(1, 100) is CheckInResult.HELP


# -- state-machine integration: the same path a live mic would drive ----------


def frame(node_id, t, *, motion=20.0, fall=False, presence=True):
    return Frame(node_id=node_id, ts=t * 1000, presence=presence, motion=motion,
                 fall_flag=fall, breathing_bpm=15.0, hr_bpm=72.0, confidence=0.9,
                 occupancy=1)


def machine_with(provider) -> FallMachine:
    return FallMachine(DetectionConfig(), escalator=NullEscalator(), checkin=provider)


def drive_to_checkin(m: FallMachine) -> int:
    """Fall + 20s stillness -> CHECK_IN_ACTIVE. Returns the check-in start second."""
    m.step(frame(1, 0, motion=9500, fall=True))
    for t in range(1, 21):
        m.step(frame(1, t))
    assert m.state_of(1) is State.CHECK_IN_ACTIVE
    return 20


@pytest.mark.parametrize("pcm", [vf.CLEAR_FINE, vf.WEAK_FINE])
def test_machine_recovers_when_resident_says_fine(pcm):
    m = machine_with(make_provider(pcm))
    drive_to_checkin(m)
    m.step(frame(1, 21))  # first check-in poll hears "I'm fine"
    assert m.state_of(1) is State.NORMAL
    assert m.escalator.calls == []
    near = [t for t in m.transitions if t.kind == db.NEAR_FALL]
    assert near and near[-1].reason == "checkin_answered"


def test_machine_escalates_help_immediately():
    m = machine_with(make_provider(vf.HELP))
    start = drive_to_checkin(m)
    m.step(frame(1, 21))  # one second into a 45s window
    assert m.state_of(1) is State.CONFIRMED_FALL
    assert len(m.escalator.calls) == 1
    confirm = next(t for t in m.transitions if t.kind == db.CONFIRMED_FALL)
    assert confirm.reason == "help_keyword"
    # Proves "immediate": confirmed far short of the 45s timeout.
    elapsed_s = confirm.reading_ts // 1000 - start
    assert elapsed_s < m.config.checkin_timeout_secs
    assert elapsed_s == 1


@pytest.mark.parametrize("pcm", [vf.SILENCE, vf.TV_NOISE])
def test_machine_times_out_on_no_answer(pcm):
    provider = make_provider(pcm)
    m = machine_with(provider)
    start = drive_to_checkin(m)
    # Walk the whole window; confirmation must come only at the timeout.
    for t in range(21, 65):
        m.step(frame(1, t))
        assert m.state_of(1) is State.CHECK_IN_ACTIVE  # nothing premature
    m.step(frame(1, start + 45))
    assert m.state_of(1) is State.CONFIRMED_FALL
    assert len(m.escalator.calls) == 1
    confirm = next(t for t in m.transitions if t.kind == db.CONFIRMED_FALL)
    assert confirm.reason == "checkin_timeout"
    # Mic was released no later than the window — capture stayed bounded.
    closes = [e for e in provider.audit_events if e.event == "mic_close"]
    opens = [e for e in provider.audit_events if e.event == "mic_open"]
    assert closes and closes[0].at_ms - opens[0].at_ms <= 45 * 1000
    assert not provider.is_capturing(1)


# -- config-only provider swap (AC#3) -----------------------------------------


def test_factory_builds_stub_by_default():
    assert isinstance(build_checkin_provider(DetectionConfig()), StubCheckInProvider)


def test_factory_builds_local_audio_without_importing_heavy_deps():
    cfg = load_config_from_text(
        "checkin:\n  provider: local_audio\n  model_path: /models/vosk-en\n"
    )
    provider = build_checkin_provider(cfg)
    assert isinstance(provider, LocalAudioCheckInProvider)
    # Constructed lazily: a Vosk spotter exists but no model/lib was loaded.
    assert isinstance(provider._spotter, VoskKeywordSpotter)


def test_factory_requires_model_path_for_local_audio():
    cfg = load_config_from_text("checkin:\n  provider: local_audio\n")
    with pytest.raises(ValueError, match="model_path"):
        build_checkin_provider(cfg)


def test_factory_rejects_unknown_provider():
    cfg = load_config_from_text("checkin:\n  provider: nope\n")
    with pytest.raises(ValueError, match="unknown checkin provider"):
        build_checkin_provider(cfg)


def test_provider_swap_is_config_only():
    """Same machine code, only the config string changes stub <-> local_audio."""
    stub_cfg = DetectionConfig()
    local_cfg = load_config_from_text(
        "checkin:\n  provider: local_audio\n  model_path: /m\n"
    )
    # The machine accepts whichever the factory yields — no code branch on type.
    for cfg in (stub_cfg, local_cfg):
        m = FallMachine(cfg, escalator=NullEscalator(),
                        checkin=build_checkin_provider(cfg))
        assert hasattr(m.checkin, "poll")


def test_detection_yaml_parses_checkin_block():
    cfg = load_config_from_text(
        "checkin:\n"
        "  provider: local_audio\n"
        "  device: PowerConf\n"
        "  listen_window_secs: 30\n"
        "  confidence_threshold: 0.7\n"
        "  vad_energy_threshold: 500.0\n"
        "  samplerate: 16000\n"
    )
    assert cfg.checkin.provider == "local_audio"
    assert cfg.checkin.device == "PowerConf"
    assert cfg.checkin.listen_window_secs == 30
    assert cfg.checkin.confidence_threshold == 0.7


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
