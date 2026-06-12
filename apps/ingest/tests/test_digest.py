"""Caregiver weekly wellness digest (BAT-42).

Maps every acceptance criterion to a test:

* Digest generated from a week of packet_sim / aggregated data; plain-language,
  non-medical, no raw numbers dumped
    -> test_summary_from_fixture_week_all_normal
    -> test_digest_is_non_medical_and_dumps_no_raw_numbers
    -> test_aggregate_week_from_packet_sim_data
* Highlights deviations (±2σ) in human terms; "all normal" when nothing notable
    -> test_highlights_bathroom_deviation_in_human_terms
    -> test_highlights_low_activity_and_late_morning
    -> test_minor_scatter_under_floor_is_all_normal
* Dry-run by default (logged, not emailed); email send gated behind creds
    -> test_dry_run_is_the_default
    -> test_deliver_digest_logs_and_records_in_dry_run
    -> test_live_email_gated_behind_creds
* Test on the summary generation from a fixture week
    -> test_summary_from_fixture_week_all_normal (the fixture-week unit)
"""

from __future__ import annotations

import importlib.util
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ingest import digest as dg
from ingest.app import app
from ingest.detection import DetectionConfig
from ingest.listener import persist

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_sim():
    spec = importlib.util.spec_from_file_location(
        "packet_sim", REPO_ROOT / "tools" / "packet_sim.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SIM = _load_sim()

WEEK_START = date(2026, 6, 1)  # a Monday
NO_DIGITS = "highlights are human terms, never raw numbers"


# -- fixture-week builders ---------------------------------------------------


def _day(offset: int, **kw) -> dg.DayMetrics:
    """One DayMetrics with sensible 'normal' defaults, overridable per field."""
    base = dict(
        active_minutes=150,
        bathroom_visits=5,
        first_motion_min=410,   # ~6:50am wake
        last_motion_min=1380,   # ~11:00pm settle
        room_minutes={"Kitchen": 90, "Living Room": 60},
    )
    base.update(kw)
    return dg.DayMetrics(date=(WEEK_START + timedelta(days=offset)).isoformat(), **base)


def _week(days: list[dg.DayMetrics], home_name: str | None = "Maple St") -> dg.WeekAggregate:
    return dg.WeekAggregate(
        week_start=WEEK_START.isoformat(), days=days, home_name=home_name
    )


def _steady_week() -> dg.WeekAggregate:
    """Seven days with only sub-threshold jitter — nothing should be notable."""
    jitter_active = [0, 10, -8, 5, -12, 7, -3]
    jitter_bath = [0, 1, -1, 0, 1, -1, 0]
    jitter_wake = [0, 8, -10, 5, -7, 12, -4]
    days = [
        _day(i, active_minutes=150 + jitter_active[i],
             bathroom_visits=5 + jitter_bath[i],
             first_motion_min=410 + jitter_wake[i])
        for i in range(7)
    ]
    return _week(days)


# -- acceptance: summary generation from a fixture week ----------------------


def test_summary_from_fixture_week_all_normal():
    digest = dg.generate_digest(_steady_week())

    assert digest.enough_data is True
    assert digest.all_normal is True
    assert digest.highlights == []
    assert "about the same as usual" in digest.summary
    # Coarse, human overview — a sleep window, not a dump of readings.
    assert "to" in digest.overview["sleep_window"]
    assert digest.overview["busiest_rooms"][0] == "Kitchen"
    assert digest.week_start == "2026-06-01"
    assert digest.week_end == "2026-06-07"


# -- acceptance: ±2σ deviations in human terms -------------------------------


def test_highlights_bathroom_deviation_in_human_terms():
    days = [_day(i) for i in range(7)]
    days[5] = _day(5, bathroom_visits=12)  # Saturday: a spike
    digest = dg.generate_digest(_week(days))

    assert digest.all_normal is False
    bathroom = [h for h in digest.highlights if "bathroom" in h.lower()]
    assert len(bathroom) == 1
    assert bathroom[0].startswith("Saturday")
    assert "more" in bathroom[0]
    # "in human terms" — no raw numbers in the highlight line.
    assert not any(ch.isdigit() for ch in bathroom[0]), NO_DIGITS


def test_highlights_low_activity_and_late_morning():
    days = [_day(i) for i in range(7)]
    days[2] = _day(2, active_minutes=55, first_motion_min=620)  # quiet, slept in
    digest = dg.generate_digest(_week(days))

    metrics = {d.metric for d in dg._deviations(_week(days))}
    assert "active_minutes" in metrics
    assert "first_motion_min" in metrics
    wednesday = [h for h in digest.highlights if h.startswith("Wednesday")]
    assert any("less active" in h for h in wednesday)
    assert any("later in the morning" in h for h in wednesday)
    assert all(not any(ch.isdigit() for ch in h) for h in digest.highlights), NO_DIGITS


def test_minor_scatter_under_floor_is_all_normal():
    # One extra bathroom visit (delta 1 < floor 2) must not trip a highlight even
    # though a lone point in an otherwise-flat week reaches |z| ~ 2.45.
    days = [_day(i, bathroom_visits=5) for i in range(7)]
    days[3] = _day(3, bathroom_visits=6)
    digest = dg.generate_digest(_week(days))
    assert digest.all_normal is True
    assert digest.highlights == []


# -- acceptance: non-medical, no raw numbers dumped --------------------------


def test_digest_is_non_medical_and_dumps_no_raw_numbers():
    days = [_day(i) for i in range(7)]
    days[5] = _day(5, bathroom_visits=13)  # the kind of thing a naive system calls a UTI
    digest = dg.generate_digest(_week(days))

    blob = (digest.headline + " " + digest.summary + " " + digest.text()).lower()
    for word in dg.MEDICAL_WORDS:
        assert word not in blob, f"medical word leaked into digest copy: {word!r}"
    # No raw reading fields anywhere in the SERIALIZED payload (scan values, not
    # just top-level keys — a raw field nested in overview/highlights would slip
    # a key-only check). Tokens are unambiguous raw column names, not prose
    # substrings like "ts"/"motion" that legitimately appear in words.
    # ("readings" is excluded — the PRIVACY_NOTICE legitimately says "no raw
    # readings"; these are the raw Reading column names that must never appear.)
    blob = json.dumps(digest.to_json()).lower()
    for raw in ("hr_bpm", "breathing_bpm", "motion_energy", "confidence",
                "received_at", "node_id", "fall_flag"):
        assert raw not in blob, f"raw reading field leaked into digest payload: {raw!r}"
    # Highlights carry no raw numbers — deviations are phrased, not dumped.
    assert all(not any(ch.isdigit() for ch in h) for h in digest.highlights), NO_DIGITS


def test_not_enough_data_week():
    days = [_day(0), dg.DayMetrics(date="2026-06-02"), dg.DayMetrics(date="2026-06-03")]
    digest = dg.generate_digest(_week(days))
    assert digest.enough_data is False
    assert digest.all_normal is True
    assert digest.headline == "Not much to report yet"


# -- acceptance: dry-run default, email gated behind creds -------------------


def test_dry_run_is_the_default(monkeypatch):
    monkeypatch.delenv("DIGEST_MODE", raising=False)
    assert dg.is_dry_run() is True
    assert isinstance(dg.make_digest_notifier(), dg.DryRunDigestNotifier)


def test_deliver_digest_logs_and_records_in_dry_run(monkeypatch, caplog):
    monkeypatch.delenv("DIGEST_MODE", raising=False)
    monkeypatch.setenv("HEARTH_DIGEST_TO", "family@example.com")
    notifier = dg.DryRunDigestNotifier()
    digest = dg.generate_digest(_steady_week())

    with caplog.at_level(logging.INFO, logger="hearth.ingest.digest"):
        email = dg.deliver_digest(digest, notifier=notifier)

    assert notifier.sent == [email]          # recorded in memory, never sent out
    assert email.to == "family@example.com"  # recipient from env
    assert "Weekly wellness summary" in email.subject
    assert any(r.__dict__.get("status") == "dry_run" for r in caplog.records)


def test_deliver_digest_recipient_placeholder_when_unconfigured(monkeypatch):
    monkeypatch.delenv("HEARTH_DIGEST_TO", raising=False)
    notifier = dg.DryRunDigestNotifier()
    dg.deliver_digest(dg.generate_digest(_steady_week()), notifier=notifier)
    assert "no recipient configured" in notifier.sent[0].to


def test_live_email_gated_behind_creds(monkeypatch):
    monkeypatch.setenv("DIGEST_MODE", "live")
    monkeypatch.delenv("HEARTH_SMTP_HOST", raising=False)
    monkeypatch.delenv("HEARTH_DIGEST_FROM", raising=False)
    with pytest.raises(RuntimeError, match="HEARTH_SMTP_HOST"):
        dg.make_digest_notifier()

    # With creds present, the live notifier constructs (we do not actually send).
    monkeypatch.setenv("HEARTH_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("HEARTH_DIGEST_FROM", "hearth@example.com")
    assert isinstance(dg.make_digest_notifier(), dg.SmtpDigestNotifier)


# -- aggregation from a week of packet_sim data ------------------------------


def _config() -> DetectionConfig:
    return DetectionConfig(rooms={1: "Kitchen", 2: "Living Room", 3: "Bathroom"})


def _active_vitals(node_id: int):
    """A 'normal' packet_sim frame mapped onto the real wire fields (active)."""
    frame = dict(presence=1, fall=0, motion=2000, breath=15, hr=72, conf_p=90, occ=1)
    return SIM.frame_to_vitals(node_id, 0, frame)


def _epoch(day: date, hour: int, minute: int) -> float:
    return datetime(day.year, day.month, day.day, hour, minute).timestamp()


def _persist_week(session_factory, *, busy_bathroom_day: int | None = None):
    """Persist a week of active readings stamped with real wall-clock times."""
    for offset in range(7):
        day = WEEK_START + timedelta(days=offset)
        # Morning activity 7:00–7:40 in the kitchen, plus an evening block.
        for minute in range(0, 45, 5):
            persist(session_factory, _active_vitals(1),
                    received_at=_epoch(day, 7, minute))
        persist(session_factory, _active_vitals(2), received_at=_epoch(day, 21, 30))
        # Two bathroom visits/day (clusters split by a >10min gap).
        visits = [(8, 5), (13, 10)]
        if busy_bathroom_day == offset:
            visits += [(15, 0), (17, 30), (19, 15)]  # an unusually busy day
        for hh, mm in visits:
            persist(session_factory, _active_vitals(3), received_at=_epoch(day, hh, mm))


def test_aggregate_week_from_packet_sim_data(session_factory):
    _persist_week(session_factory)
    with session_factory() as session:
        week = dg.aggregate_week(session, WEEK_START, _config())

    assert len(week.days) == 7
    monday = week.days[0]
    assert monday.has_data
    assert monday.active_minutes >= 9          # nine 5-min marks + evening
    assert monday.first_motion_min == 7 * 60   # 07:00
    assert monday.last_motion_min == 21 * 60 + 30
    assert monday.bathroom_visits == 2         # two gap-split visits
    assert monday.room_minutes["Kitchen"] >= 9

    digest = dg.generate_digest(week)
    assert digest.enough_data is True
    assert digest.all_normal is True           # a flat week reads as steady


def test_aggregate_week_flags_a_real_anomalous_day(session_factory):
    _persist_week(session_factory, busy_bathroom_day=5)  # Saturday spikes to 5 visits
    with session_factory() as session:
        week = dg.aggregate_week(session, WEEK_START, _config())

    digest = dg.generate_digest(week)
    assert digest.all_normal is False
    assert any(h.startswith("Saturday") and "bathroom" in h.lower()
               for h in digest.highlights)


def test_aggregate_skips_rows_without_received_at(session_factory):
    # A GENUINELY null-stamped row (persist() would stamp now instead) can't be
    # placed on a day -> skipped, and must not crash aggregation on the None
    # timestamp. Insert directly so received_at is really NULL; a valid in-window
    # row proves aggregation still counts real data alongside it.
    from ingest.db import Reading
    active = datetime.combine(WEEK_START + timedelta(days=1),
                              datetime.min.time()).timestamp() + 12 * 3600
    with session_factory() as s:
        s.add(Reading(node_id=1, ts=0, presence=True, motion=2000.0,
                      breathing_bpm=15.0, hr_bpm=72.0, confidence=0.9,
                      fall_flag=False, occupancy=1, received_at=None))   # NULL stamp
        s.add(Reading(node_id=1, ts=1000, presence=True, motion=2000.0,
                      breathing_bpm=15.0, hr_bpm=72.0, confidence=0.9,
                      fall_flag=False, occupancy=1, received_at=active))  # in-window
        s.commit()
    with session_factory() as session:
        week = dg.aggregate_week(session, WEEK_START, _config())
    active_days = [d for d in week.days if d.has_data]
    assert [d.date for d in active_days] == [
        (WEEK_START + timedelta(days=1)).isoformat()
    ]  # only the stamped row contributed; the null row was skipped


# -- the /api/digest endpoint ------------------------------------------------


def _client(session_factory, *, config=None, now=None) -> TestClient:
    app.state.session_factory = session_factory
    app.state.detection_config = config
    app.state.clock = (lambda: now) if now is not None else None
    return TestClient(app)


def test_digest_endpoint_returns_plain_language_summary(session_factory):
    _persist_week(session_factory, busy_bathroom_day=5)
    client = _client(session_factory, config=_config())

    resp = client.get(f"/api/digest?week={WEEK_START.isoformat()}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enough_data"] is True
    assert body["all_normal"] is False
    assert any("Saturday" in h for h in body["highlights"])
    assert body["privacy"] == dg.PRIVACY_NOTICE
    # Coarse only — no raw reading fields anywhere in the serialized response.
    blob = json.dumps(body).lower()
    for raw in ("hr_bpm", "breathing_bpm", "motion_energy", "received_at",
                "node_id", "fall_flag"):
        assert raw not in blob, f"raw reading field leaked into /api/digest: {raw!r}"


def test_digest_endpoint_defaults_to_recent_week(session_factory):
    client = _client(session_factory, config=_config())
    resp = client.get("/api/digest")
    assert resp.status_code == 200
    assert "week_start" in resp.json()


def test_digest_endpoint_rejects_bad_week(session_factory):
    client = _client(session_factory, config=_config())
    assert client.get("/api/digest?week=last-tuesday").status_code == 422
