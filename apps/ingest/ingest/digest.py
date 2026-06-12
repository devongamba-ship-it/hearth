"""Caregiver weekly wellness digest (BAT-42).

The reassurance product that lives *between* alerts: a plain-language weekly note
a family reads to feel the value — the concept doc's "Mom was active this
morning, slept normally." Pure software, no hardware.

What this module does, end to end:

* **Aggregate** a week of the local ``readings`` table into coarse per-day trend
  figures (:class:`DayMetrics`) — active minutes, the sleep window (first/last
  motion of the day), bathroom frequency, room patterns. Never raw readings: a
  reading becomes a one-bit "real motion this minute" mark and is then thrown
  away. Aggregation reads ``received_at`` (the BAT-37 wall-clock stamp); rows
  without it can't be placed on a calendar day and are skipped.
* **Narrate** those figures in plain, *non-medical* language, reusing the BAT-28
  §4.1 copy principles: plain words a stressed family member understands, no
  jargon, numbers only when they help. We say "a few more bathroom visits than
  usual", never "possible UTI" — this module describes, it never diagnoses.
* **Highlight deviations** at ±2σ. The baseline is the resident's own week (a
  longer trailing baseline is a follow-up): a day whose figure sits ≥2σ from the
  week mean *and* clears a human floor is called out; when nothing clears the bar
  the digest says everything looks about the same as usual.
* **Deliver** via an email notifier that is **dry-run by default** — it logs the
  digest and makes no external call and needs no credentials (CLAUDE.md #6/#7),
  exactly like the escalation :class:`DryRunNotifier`. Live SMTP is human-set
  only (``DIGEST_MODE=live``) with creds from env, never code.

Privacy (CLAUDE.md): the digest is coarse states + summarized figures only — no
per-second data, no raw motion energies or vitals, no resident name (the
caregiver app supplies the "Mom" framing client-side).
"""

from __future__ import annotations

import logging
import os
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from .detection import DetectionConfig

log = logging.getLogger("hearth.ingest.digest")

# Deviations beyond this many standard deviations from the week's mean are
# "notable". With seven days a lone outlier reaches |z| ≈ 2.45, so a single
# unusual day crosses the bar while ordinary scatter does not.
SIGMA = 2.0

# Minimum number of days with data before we trust a week enough to narrate it.
MIN_DAYS_WITH_DATA = 3

PRIVACY_NOTICE = "Coarse weekly summary only. No cameras, no audio, no raw readings."

# Medical / alarming words that must never appear in family-facing digest copy
# (lower-cased; matched case-insensitively). The digest describes, never
# diagnoses — "a few more bathroom visits", never "possible UTI". Tests guard on
# this list, mirroring the BAT-28 jargon guard.
MEDICAL_WORDS = (
    "uti", "infection", "diagnos", "symptom", "dementia", "decline",
    "disease", "syndrome", "dehydrat", "fever", "medical", "patient",
    "condition", "deteriorat", "abnormal",
)


# -- aggregated trend data (the digest's input) ------------------------------


@dataclass(frozen=True)
class DayMetrics:
    """One day's coarse trend figures, derived from (not containing) readings.

    Times are minutes-from-local-midnight. ``first_motion_min`` doubles as the
    morning *wake* boundary and ``last_motion_min`` as the evening *settle*
    boundary of the sleep window. A field is ``None`` when the day had no real
    motion at all (no data / away), which excludes it from that metric's stats.
    """

    date: str  # ISO YYYY-MM-DD (local)
    active_minutes: int = 0
    bathroom_visits: int = 0
    first_motion_min: int | None = None
    last_motion_min: int | None = None
    room_minutes: dict[str, int] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return self.first_motion_min is not None


@dataclass(frozen=True)
class WeekAggregate:
    """A week of :class:`DayMetrics` plus the home label — the digest's input."""

    week_start: str  # ISO date of the first day
    days: list[DayMetrics]
    home_name: str | None = None

    @property
    def week_end(self) -> str:
        d = date.fromisoformat(self.week_start) + timedelta(days=max(0, len(self.days) - 1))
        return d.isoformat()

    @property
    def days_with_data(self) -> list[DayMetrics]:
        return [d for d in self.days if d.has_data]


# -- ±2σ deviation detection -------------------------------------------------


@dataclass(frozen=True)
class Baseline:
    """Mean + population std of a metric across the week's days-with-data."""

    mean: float
    std: float

    def z(self, value: float) -> float:
        if self.std == 0:
            return 0.0
        return (value - self.mean) / self.std


def _baseline(values: list[float]) -> Baseline | None:
    if len(values) < MIN_DAYS_WITH_DATA:
        return None
    return Baseline(statistics.fmean(values), statistics.pstdev(values))


@dataclass(frozen=True)
class MetricSpec:
    """How one tracked figure is read off a day and phrased when it deviates."""

    key: str
    value_of: Callable[[DayMetrics], int | None]
    floor: float  # minimum absolute delta from the mean that counts as notable
    more: str  # plain-language clause when the day is above usual
    less: str  # plain-language clause when the day is below usual


# The four figures we call out. first/last motion *are* the sleep-window
# boundaries (wake / settle), so deviations on them describe sleep changes too.
METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "active_minutes", lambda d: d.active_minutes, floor=30,
        more="was more active than usual", less="was less active than usual",
    ),
    MetricSpec(
        "bathroom_visits", lambda d: d.bathroom_visits, floor=2,
        more="made a few more trips to the bathroom than usual",
        less="made fewer trips to the bathroom than usual",
    ),
    MetricSpec(
        "first_motion_min", lambda d: d.first_motion_min, floor=45,
        more="got up later in the morning than usual",
        less="was up and about earlier than usual",
    ),
    MetricSpec(
        "last_motion_min", lambda d: d.last_motion_min, floor=45,
        more="stayed up later in the evening than usual",
        less="settled in for the night earlier than usual",
    ),
)


@dataclass(frozen=True)
class Deviation:
    """A single notable day×metric, already phrased for a human."""

    day: str  # ISO date
    metric: str
    direction: str  # "more" | "less"
    text: str


def _deviations(week: WeekAggregate) -> list[Deviation]:
    """Every day whose figure is ≥SIGMA from the week mean and clears the floor."""
    found: list[Deviation] = []
    for spec in METRICS:
        pairs = [(d, spec.value_of(d)) for d in week.days if d.has_data]
        pairs = [(d, v) for d, v in pairs if v is not None]
        base = _baseline([float(v) for _, v in pairs])
        if base is None:
            continue
        for day, value in pairs:
            delta = value - base.mean
            if abs(base.z(value)) < SIGMA or abs(delta) < spec.floor:
                continue
            direction = "more" if delta > 0 else "less"
            clause = spec.more if delta > 0 else spec.less
            found.append(Deviation(
                day=day.date, metric=spec.key, direction=direction,
                text=f"{_weekday(day.date)} {clause}.",
            ))
    found.sort(key=lambda d: (d.day, d.metric))
    return found


# -- the digest --------------------------------------------------------------


@dataclass(frozen=True)
class Digest:
    """A rendered weekly digest: a card for the app and a body for email."""

    home_name: str | None
    week_start: str
    week_end: str
    headline: str
    summary: str
    highlights: list[str]
    all_normal: bool
    enough_data: bool
    overview: dict
    privacy: str = PRIVACY_NOTICE

    def to_json(self) -> dict:
        return {
            "home_name": self.home_name,
            "week_start": self.week_start,
            "week_end": self.week_end,
            "headline": self.headline,
            "summary": self.summary,
            "highlights": self.highlights,
            "all_normal": self.all_normal,
            "enough_data": self.enough_data,
            "overview": self.overview,
            "privacy": self.privacy,
        }

    def subject(self) -> str:
        where = f" — {self.home_name}" if self.home_name else ""
        return f"Weekly wellness summary{where} ({self.week_start} to {self.week_end})"

    def text(self) -> str:
        """Plain-text email body. Plain language only — safe to log in dry-run."""
        lines = [self.headline, "", self.summary, ""]
        if self.highlights:
            lines.append("Worth a glance this week:")
            lines.extend(f"  • {h}" for h in self.highlights)
            lines.append("")
        ov = self.overview
        if self.enough_data:
            lines.append("The week at a glance:")
            lines.append(f"  • Sleep: usually {ov['sleep_window']}")
            lines.append(f"  • Activity: {ov['active_minutes']}")
            lines.append(f"  • Bathroom: {ov['bathroom_visits']}")
            if ov.get("busiest_rooms"):
                lines.append(f"  • Most time in: {', '.join(ov['busiest_rooms'])}")
            lines.append("")
        lines.append(self.privacy)
        return "\n".join(lines)


def generate_digest(week: WeekAggregate) -> Digest:
    """Turn a week of aggregated trend figures into a plain-language digest.

    This is the heart of the feature and the unit the acceptance test drives
    against a fixture week. Pure: same week in, same digest out.
    """
    days = week.days_with_data
    if len(days) < MIN_DAYS_WITH_DATA:
        return Digest(
            home_name=week.home_name, week_start=week.week_start,
            week_end=week.week_end,
            headline="Not much to report yet",
            summary=("There wasn't enough activity recorded this week to "
                     "summarize. This is normal for a new setup or a quiet week "
                     "away."),
            highlights=[], all_normal=True, enough_data=False,
            overview=_overview(days),
        )

    deviations = _deviations(week)
    highlights = [d.text for d in deviations]
    all_normal = not highlights
    overview = _overview(days)

    if all_normal:
        headline = "A steady week"
        summary = (
            "Everything looked about the same as usual this week — up and about "
            f"most days, with a regular night's sleep around {overview['sleep_window']}."
        )
    else:
        headline = "A mostly steady week"
        summary = (
            "Most of the week looked about the same as usual. A few things stood "
            "out and are worth a glance below — none are cause for alarm, just "
            "the kind of thing it's nice to know."
        )

    return Digest(
        home_name=week.home_name, week_start=week.week_start,
        week_end=week.week_end, headline=headline, summary=summary,
        highlights=highlights, all_normal=all_normal, enough_data=True,
        overview=overview,
    )


def _overview(days: list[DayMetrics]) -> dict:
    """Coarse, rounded typical figures for the card — never raw readings."""
    if not days:
        return {
            "sleep_window": "not enough data",
            "active_minutes": "not enough data",
            "bathroom_visits": "not enough data",
            "busiest_rooms": [],
        }
    wakes = [d.first_motion_min for d in days if d.first_motion_min is not None]
    settles = [d.last_motion_min for d in days if d.last_motion_min is not None]
    active = round(statistics.fmean(d.active_minutes for d in days))
    bathroom = round(statistics.fmean(d.bathroom_visits for d in days))

    room_totals: dict[str, int] = {}
    for d in days:
        for room, minutes in d.room_minutes.items():
            room_totals[room] = room_totals.get(room, 0) + minutes
    busiest = [r for r, _ in sorted(room_totals.items(), key=lambda kv: kv[1], reverse=True)[:2]]

    sleep_window = "not enough data"
    if settles and wakes:
        sleep_window = (
            f"{_fmt_clock(round(statistics.median(settles)))} to "
            f"{_fmt_clock(round(statistics.median(wakes)))}"
        )
    return {
        "sleep_window": sleep_window,
        "active_minutes": f"about {active} active minutes a day",
        "bathroom_visits": f"about {bathroom} visits a day",
        "busiest_rooms": busiest,
    }


# -- aggregation: readings -> WeekAggregate ----------------------------------


def _local_dt(epoch: float, tz: ZoneInfo | None) -> datetime:
    return datetime.fromtimestamp(epoch, tz)


def _minute_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _is_bathroom(room: str) -> bool:
    return "bath" in room.lower()


def aggregate_week(
    session,
    week_start: date,
    config: DetectionConfig,
    *,
    num_days: int = 7,
    bathroom_gap_min: int = 10,
) -> WeekAggregate:
    """Build a :class:`WeekAggregate` from the local ``readings`` table.

    A reading counts as "real motion" when its energy is at/above the detection
    stillness threshold; each such reading marks the wall-clock minute it landed
    in active. Per day we keep only those one-bit minute marks — active-minute
    count, the first/last active minute (the sleep-window boundaries), per-room
    minute counts, and bathroom *visits* (active minute-runs on a bathroom node
    split on gaps ≥ ``bathroom_gap_min``). The readings themselves never leave
    this function. Rows without a ``received_at`` stamp can't be placed on a day
    and are skipped.
    """
    from sqlalchemy import select

    from .db import Reading

    tz = ZoneInfo(config.timezone) if config.timezone else None
    threshold = config.stillness_threshold
    start_epoch = datetime.combine(week_start, datetime.min.time(),
                                   tzinfo=tz).timestamp()
    end_epoch = (datetime.combine(week_start, datetime.min.time(), tzinfo=tz)
                 + timedelta(days=num_days)).timestamp()

    # day ISO -> set of active minute-of-day; per-room minutes; per-node bathroom minutes
    active_minutes: dict[str, set[int]] = {}
    room_minutes: dict[str, dict[str, set[int]]] = {}
    bath_minutes: dict[str, set[int]] = {}

    rows = session.execute(
        select(Reading.node_id, Reading.motion, Reading.received_at)
        .where(Reading.received_at.is_not(None))
        .where(Reading.received_at >= start_epoch)
        .where(Reading.received_at < end_epoch)
        .where(Reading.motion >= threshold)
    ).all()

    for node_id, _motion, received_at in rows:
        dt = _local_dt(received_at, tz)
        day = dt.date().isoformat()
        minute = _minute_of_day(dt)
        active_minutes.setdefault(day, set()).add(minute)
        room = config.room_for(node_id)
        room_minutes.setdefault(day, {}).setdefault(room, set()).add(minute)
        if _is_bathroom(room):
            bath_minutes.setdefault(day, set()).add(minute)

    days: list[DayMetrics] = []
    for offset in range(num_days):
        day = (week_start + timedelta(days=offset)).isoformat()
        minutes = active_minutes.get(day, set())
        first = min(minutes) if minutes else None
        last = max(minutes) if minutes else None
        rooms = {
            room: len(mins) for room, mins in room_minutes.get(day, {}).items()
        }
        visits = _count_visits(bath_minutes.get(day, set()), bathroom_gap_min)
        days.append(DayMetrics(
            date=day, active_minutes=len(minutes), bathroom_visits=visits,
            first_motion_min=first, last_motion_min=last, room_minutes=rooms,
        ))

    return WeekAggregate(
        week_start=week_start.isoformat(), days=days, home_name=config.home_name
    )


def _count_visits(minutes: set[int], gap: int) -> int:
    """Active minute-marks grouped into visits: a new visit starts after a gap."""
    if not minutes:
        return 0
    ordered = sorted(minutes)
    visits = 1
    for prev, cur in zip(ordered, ordered[1:]):
        if cur - prev >= gap:
            visits += 1
    return visits


# -- time formatting ---------------------------------------------------------


def _fmt_clock(minute_of_day: int) -> str:
    """``1370`` -> ``"10:50pm"`` (12-hour, lower-case am/pm)."""
    minute_of_day %= 24 * 60
    hour, minute = divmod(minute_of_day, 60)
    suffix = "am" if hour < 12 else "pm"
    h12 = hour % 12 or 12
    return f"{h12}:{minute:02d}{suffix}"


def _weekday(iso_date: str) -> str:
    return date.fromisoformat(iso_date).strftime("%A")


# -- delivery: email notifier (dry-run by default) ---------------------------

EMAIL = "email"


@dataclass(frozen=True)
class DigestEmail:
    to: str
    subject: str
    body: str


class DigestNotifier(Protocol):
    """Delivers one digest email. The dry-run default never leaves the process."""

    def send(self, email: DigestEmail) -> DigestEmail: ...


@dataclass
class DryRunDigestNotifier:
    """Logs the digest instead of sending it. Default; no creds, no I/O.

    Mirrors the escalation :class:`DryRunNotifier`: every "send" is recorded in
    memory and written to the log so an operator can see exactly what *would*
    have gone out, with zero external side effects (CLAUDE.md #6/#7).
    """

    sent: list[DigestEmail] = field(default_factory=list)

    def send(self, email: DigestEmail) -> DigestEmail:
        self.sent.append(email)
        log.info("DRY-RUN digest email -> %s: %s", email.to, email.subject,
                 extra={"event": "digest_dry_run", "status": "dry_run"})
        log.info("digest body:\n%s", email.body)
        return email


class SmtpDigestNotifier:
    """Live SMTP delivery. Human-set only (``DIGEST_MODE=live``); never in CI.

    Credentials come from the environment, never code:
    ``HEARTH_SMTP_HOST`` (required), ``HEARTH_SMTP_PORT`` (default 587),
    ``HEARTH_SMTP_USER`` / ``HEARTH_SMTP_PASSWORD`` (optional auth),
    ``HEARTH_DIGEST_FROM`` (required sender).
    """

    def __init__(self) -> None:
        self._host = os.environ.get("HEARTH_SMTP_HOST")
        self._from = os.environ.get("HEARTH_DIGEST_FROM")
        if not self._host or not self._from:
            raise RuntimeError(
                "live digest email requires HEARTH_SMTP_HOST and HEARTH_DIGEST_FROM "
                "in the environment"
            )
        self._port = int(os.environ.get("HEARTH_SMTP_PORT", "587"))
        self._user = os.environ.get("HEARTH_SMTP_USER")
        self._password = os.environ.get("HEARTH_SMTP_PASSWORD")

    def send(self, email: DigestEmail) -> DigestEmail:  # pragma: no cover - live path
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = self._from
        msg["To"] = email.to
        msg["Subject"] = email.subject
        msg.set_content(email.body)
        with smtplib.SMTP(self._host, self._port) as smtp:
            smtp.starttls()
            if self._user and self._password:
                smtp.login(self._user, self._password)
            smtp.send_message(msg)
        return email


def is_dry_run() -> bool:
    """Dry-run unless a human explicitly sets ``DIGEST_MODE=live``."""
    return os.environ.get("DIGEST_MODE", "dry_run").strip().lower() != "live"


def make_digest_notifier() -> DigestNotifier:
    """Pick the notifier for the current mode. Dry-run is the safe default."""
    if is_dry_run():
        return DryRunDigestNotifier()
    return SmtpDigestNotifier()  # pragma: no cover - requires human-set live mode


def deliver_digest(
    digest: Digest,
    *,
    notifier: DigestNotifier | None = None,
    to: str | None = None,
) -> DigestEmail:
    """Render ``digest`` to an email and hand it to the notifier.

    Recipient resolves from ``to`` then ``$HEARTH_DIGEST_TO``; with neither set
    the dry-run path still logs the digest against a clearly-unconfigured
    placeholder, so a misconfigured deploy fails loud (in the log) not silent.
    """
    notifier = notifier or make_digest_notifier()
    recipient = to or os.environ.get("HEARTH_DIGEST_TO") or "(no recipient configured)"
    email = DigestEmail(to=recipient, subject=digest.subject(), body=digest.text())
    return notifier.send(email)


def default_week_start(now: float, tz: ZoneInfo | None, *, num_days: int = 7) -> date:
    """The start of the most recent complete ``num_days`` window before today."""
    today = _local_dt(now, tz).date()
    return today - timedelta(days=num_days)
