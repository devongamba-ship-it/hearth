"""Live mesh-view feed (BAT-27): the 1 Hz aggregated stream behind the floor plan.

The live mesh view (platform design v0.1 §1, §8) draws a schematic floor plan
with per-room activity heat, occupancy/track markers, and an event ticker. This
module produces the data behind it: once per second it reads the latest readings
out of SQLite and turns them into **aggregated** frames — never raw CSI or raw
readings (CLAUDE.md: privacy is the brand; "Never ship raw readings to clients").

Message types produced by :meth:`LiveFeed.tick` (each goes out as one WS JSON
message):

* ``room_activity`` — once per tick. Per-room ``{room, activity_index (0-100,
  rolling 30s), occupancy_count}`` plus a ``tracks`` array. Per the v0.2 design
  update, multi-occupancy is first-class: tracks are *anonymous motion targets*,
  not identities. v1 emits a single known resident track (label from the layout,
  ``identity_confidence`` from the layout) wherever presence is strongest, and
  anonymous ``guest-N`` tracks for extra detected targets. The contract is
  array-shaped so real per-target heuristics land later without a client change.
  If track-separation confidence is low the frame carries ``tracking_degraded:
  true`` and an empty ``tracks`` array — clients degrade to count badges.

  The frame also carries a ``source`` label (BAT-34): ``"simulated"`` when the
  floor is driven by packet_sim, ``"live"`` on real silicon. It lets the ops
  console / command center badge the data source at a glance (the calibration-day
  flip from sim to ESP32 nodes visibly confirms itself). Additive and coarse — a
  configured label, not a raw reading; older clients ignore the unknown field.

  The known **resident** track may also carry two **optional** coarse vitals
  estimates (BAT-33), derived from the same persisted breathing/motion
  aggregates — never raw readings. Anonymous ``guest-N`` tracks never carry
  them: the estimates are room-level, so attaching them to a guest would
  attribute one person's vitals to another (BAT-35; see ``_tracks``). They are
  withheld, not misattributed, until per-target sensing genuinely lands:

    * ``respiration_bpm_estimate`` — integer breaths-per-minute, smoothed over
      the rolling ``WINDOW_SECS`` window. Absent until the window has warmed up
      and while presence confidence is below ``VITALS_CONF_MIN``.
    * ``posture_estimate`` — one of ``upright | seated | floor | unknown``.
      ``floor`` whenever the track is under fall watch; otherwise ``upright``
      when there is sustained motion, ``seated`` when present but still. Absent
      below ``VITALS_CONF_MIN``.

  Both are *estimates*, optional by contract: a client that sees neither keeps
  rendering "—" (BAT-29's HUD already does). The privacy bar is unchanged —
  smoothed 1 Hz estimates only, nothing per-second, nothing raw, no waveform.
* ``event`` — ticker items: ``room_transition`` (the resident moved rooms),
  ``dwell_start`` (the resident has stayed put past the dwell threshold), and
  ``sensing_dropout`` (a node went silent > 10s). Carry ``track_id`` where it
  applies.
* ``incident`` — fall state-machine transitions (NORMAL→SUSPECTED→…), streamed
  from the ``fall_transitions`` table as they are written.

The feed is a stateful poller, deliberately deterministic: all elapsed-time
logic goes through an injected ``clock`` and all data comes from the session
factory, so :meth:`tick` is unit-testable with no sleeps. The async WS driver in
``app.py`` just calls :meth:`tick` once per second.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from sqlalchemy import func, select

from .db import FallTransition, Reading
from .layout import HomeLayout

Clock = Callable[[], float]

# Motion energy that maps to activity_index 100. The simulator's "normal moving
# around" scenario sits at ~800-4000 motion energy; 3000 puts ordinary activity
# near the top of the scale while leaving headroom. Tunable, documented constant
# rather than a magic number buried in the math.
ACTIVITY_REF = 3000.0
# Rolling window (seconds / ticks) the activity index averages over.
WINDOW_SECS = 30
# A node silent longer than this is reported as a sensing dropout.
DROPOUT_SECS = 10
# The resident staying in one room this long emits a single dwell_start.
DWELL_SECS = 30
# Below this room presence confidence, track separation is untrustworthy: emit
# occupancy counts with no positional tracks (tracking_degraded).
TRACK_CONF_MIN = 0.4
# Below this presence confidence the coarse vitals estimates (respiration,
# posture) are withheld — a track can still be placed (>= TRACK_CONF_MIN) while
# the signal is too weak to estimate vitals from. Then the fields are absent and
# the client keeps rendering "—" (BAT-33; spec: "absent when confidence too low").
VITALS_CONF_MIN = 0.5
# Respiration is only emitted once the breathing window holds at least this many
# samples, so the published estimate really is "smoothed over >= 30s" rather than
# a barely-averaged first reading.
RESP_MIN_SAMPLES = WINDOW_SECS
# Rolling motion-energy mean at/above which a present (non-fall) track reads as
# "upright" (moving/standing); below it the person is present but still ->
# "seated". 10% of ACTIVITY_REF: ordinary moving-around (~800-4000) is well
# above, quiet stillness (tens) well below. Fall watch overrides both -> floor.
POSTURE_UPRIGHT_MOTION = ACTIVITY_REF * 0.1

# Fall-machine states (db `to_state`) that put a track into "fall_watch".
_WATCH_STATES = {"SUSPECTED", "PROBABLE", "CHECK_IN_ACTIVE", "CONFIRMED_FALL"}

# Data-source labels advertised on each room_activity frame (BAT-34) so the ops
# console / command center can badge whether the floor is driven by packet_sim
# or real silicon. The env values (HEARTH_SOURCE) the operator sets that mean
# "this is the simulator"; anything else is treated as live.
SOURCE_SIMULATED = "simulated"
SOURCE_LIVE = "live"
_SIM_SOURCE_ALIASES = {"sim", "simulated", "packet_sim", "packet-sim"}


def resolve_source(value: str | None) -> str:
    """Normalise a configured source hint to ``simulated`` or ``live`` (BAT-34).

    The operator flips this on calibration day (env ``HEARTH_SOURCE``) when they
    swap packet_sim for live ESP32 nodes. Any of the sim aliases -> ``simulated``;
    anything else, including unset, defaults to ``live`` — real silicon is the
    production case, so an unconfigured feed advertises itself as live.
    """
    if value and value.strip().lower() in _SIM_SOURCE_ALIASES:
        return SOURCE_SIMULATED
    return SOURCE_LIVE


class _NodeSnapshot:
    """The latest reading for one node, plus its rolling motion/breathing windows."""

    __slots__ = (
        "reading_id", "motion", "presence", "occupancy", "confidence",
        "window", "breath_window", "breath_stale",
    )

    def __init__(self) -> None:
        self.reading_id = 0
        self.motion = 0.0
        self.presence = False
        self.occupancy = 0
        self.confidence = 0.0
        self.window: deque[float] = deque(maxlen=WINDOW_SECS)
        # Breathing-rate samples, same rolling window. Only samples taken while
        # the node reported presence with a valid (> 0) breathing lock land here.
        self.breath_window: deque[float] = deque(maxlen=WINDOW_SECS)
        # Fresh packets seen since the last valid breath sample. The deque only
        # evicts on append, so without this a node that keeps sending packets
        # with the breath lock lost (presence=1, breathing=0 — plausible during
        # vigorous motion) would republish its last good mean forever. Once the
        # lock has been gone a full window, the estimate must age out to absent.
        self.breath_stale = 0

    def rolling_motion(self) -> float:
        return sum(self.window) / len(self.window) if self.window else 0.0

    def rolling_breathing(self) -> float | None:
        # Stale: the breath lock has been gone for a full window — the window
        # still holds old samples (deque evicts only on append), so report
        # absent rather than a number sourced from a dead signal.
        if self.breath_stale >= WINDOW_SECS or not self.breath_window:
            return None
        return sum(self.breath_window) / len(self.breath_window)


def activity_index(motion: float) -> int:
    """Map a rolling motion-energy mean to the 0-100 activity index."""
    return max(0, min(100, round(100.0 * motion / ACTIVITY_REF)))


def posture_estimate(motion: float, *, present: bool, watch: bool) -> str:
    """Coarse posture from rolling motion + fall-watch state.

    A person under fall watch is treated as on the ``floor`` regardless of
    residual motion. Otherwise sustained motion reads as ``upright`` and a
    present-but-still person as ``seated``. ``unknown`` when there is no presence
    to classify. This is a derived *estimate*, never a raw reading.
    """
    if watch:
        return "floor"
    if not present:
        return "unknown"
    if motion >= POSTURE_UPRIGHT_MOTION:
        return "upright"
    return "seated"


class LiveFeed:
    """Stateful 1 Hz aggregator. Call :meth:`tick` once per second.

    Dependencies are injected (the pattern the fall machine uses): a session
    factory for the readings/transitions, the :class:`HomeLayout` for the
    node->room map and resident-track config, and a wall ``clock`` so elapsed
    time (dropout, dwell) is deterministic in tests.
    """

    def __init__(
        self,
        session_factory,
        layout: HomeLayout,
        *,
        home_id: str | None = None,
        source: str = SOURCE_LIVE,
        clock: Clock | None = None,
    ) -> None:
        self._sessions = session_factory
        self.layout = layout
        self.home_id = home_id or layout.home_id
        # Data-source label stamped on every room_activity frame (BAT-34):
        # "simulated" when fed by packet_sim, "live" on real silicon. Normalised
        # so an operator's raw HEARTH_SOURCE value can't leak an odd string.
        self.source = resolve_source(source)
        self._clock = clock or time.time

        self._nodes: dict[int, _NodeSnapshot] = {}
        self._last_seen_at: dict[int, float] = {}
        self._dropped: set[int] = set()
        self._resident_room: str | None = None
        self._resident_room_since: float = 0.0
        self._dwell_emitted = False
        self._last_txn_id = 0
        self._primed = False

    # -- public driver -------------------------------------------------------

    def tick(self) -> list[dict]:
        """Advance one second. Returns the WS messages to send this tick."""
        now = self._clock()
        with self._sessions() as session:
            latest = self._latest_readings(session)
            node_states = self._node_states(session)
            # Prime BEFORE reading transitions: otherwise the first tick reads
            # with _last_txn_id == 0 and replays the entire incident history
            # into a feed labelled "live" (reviewer concern #1 on PR #7).
            if not self._primed:
                self._prime(session, latest, now)
            new_txns = self._new_transitions(session)

        self._update_snapshots(latest, now)
        messages: list[dict] = []
        messages.extend(self._dropout_events(now))

        rooms, tracks, degraded = self._aggregate(node_states)
        messages.extend(self._resident_events(tracks, now))
        # Authoritative node-health on every frame (BAT-34 review): the badge
        # must reflect a sustained outage for as long as it lasts, not be
        # reconstructed client-side from the one-shot sensing_dropout event.
        # nodes_reporting = the spec's "N nodes" (sensors seen and not dropped),
        # NOT the track/person count. nodes_offline clears exactly when the
        # backend clears _dropped on a fresh packet — the real recovery signal.
        offline = sorted(self._dropped)
        reporting = sum(1 for n in self._last_seen_at if n not in self._dropped)
        messages.append({
            "type": "room_activity",
            "home_id": self.home_id,
            "source": self.source,
            "ts": now,
            "rooms": rooms,
            "tracks": tracks,
            "tracking_degraded": degraded,
            "nodes_reporting": reporting,
            "nodes_offline": offline,
        })
        messages.extend(self._incident_events(new_txns))
        return messages

    # -- DB reads ------------------------------------------------------------

    def _latest_readings(self, session) -> dict[int, Reading]:
        latest_ids = (
            select(func.max(Reading.id)).group_by(Reading.node_id).scalar_subquery()
        )
        rows = session.execute(
            select(Reading).where(Reading.id.in_(latest_ids))
        ).scalars().all()
        return {r.node_id: r for r in rows}

    def _node_states(self, session) -> dict[int, str]:
        latest_ids = (
            select(func.max(FallTransition.id))
            .group_by(FallTransition.node_id)
            .scalar_subquery()
        )
        rows = session.execute(
            select(FallTransition).where(FallTransition.id.in_(latest_ids))
        ).scalars().all()
        return {r.node_id: r.to_state for r in rows}

    def _new_transitions(self, session) -> list[FallTransition]:
        rows = session.scalars(
            select(FallTransition)
            .where(FallTransition.id > self._last_txn_id)
            .order_by(FallTransition.id)
        ).all()
        for row in rows:
            session.expunge(row)
        return list(rows)

    # -- state updates -------------------------------------------------------

    def _prime(self, session, latest: dict[int, Reading], now: float) -> None:
        """First tick: seed high-water marks so we stream only NEW activity.

        Without priming the first tick would replay every historical fall
        transition and immediately flag long-silent nodes as fresh dropouts. We
        treat connect time as "just seen" for known nodes and start the incident
        stream from the current tail. Must run before _new_transitions reads
        with the high-water mark.
        """
        self._last_txn_id = (
            session.scalar(select(func.max(FallTransition.id))) or 0
        )
        for node_id in latest:
            self._last_seen_at[node_id] = now
        self._primed = True

    def _update_snapshots(self, latest: dict[int, Reading], now: float) -> None:
        for node_id, r in latest.items():
            snap = self._nodes.setdefault(node_id, _NodeSnapshot())
            advanced = r.id > snap.reading_id
            snap.reading_id = r.id
            snap.motion = r.motion
            snap.presence = r.presence
            snap.occupancy = r.occupancy
            snap.confidence = r.confidence
            if advanced:
                # A fresh packet: extend the rolling window and mark the node
                # alive. A node that stops sending stops extending its window,
                # so its activity index decays to zero over WINDOW_SECS.
                snap.window.append(r.motion)
                # Breathing feeds the respiration estimate. Only count a sample
                # when the node had presence and a valid (> 0) breathing lock —
                # an empty room or a dropped lock must not drag the mean down.
                # Track ticks since the last valid lock so a long-lost lock ages
                # the estimate out to absent instead of freezing the last mean.
                if r.presence and r.breathing_bpm > 0:
                    snap.breath_window.append(r.breathing_bpm)
                    snap.breath_stale = 0
                else:
                    snap.breath_stale += 1
                self._last_seen_at[node_id] = now
                self._dropped.discard(node_id)

    # -- aggregation ---------------------------------------------------------

    def _aggregate(self, node_states: dict[int, str]):
        """Build the per-room list, the track array, and the degraded flag."""
        rooms_out = []
        room_meta: dict[str, dict] = {}
        for room_id in self.layout.room_ids():
            node_ids = self.layout.nodes_in(room_id)
            snaps = [self._nodes[n] for n in node_ids if n in self._nodes]
            if snaps:
                motion = sum(s.rolling_motion() for s in snaps) / len(snaps)
                occupancy = max(
                    (s.occupancy if s.occupancy else (1 if s.presence else 0))
                    for s in snaps
                )
                confidence = max(
                    (s.confidence for s in snaps if s.presence), default=0.0
                )
                present = any(s.presence for s in snaps) or occupancy > 0
                breathing = self._room_breathing(snaps)
            else:
                motion, occupancy, confidence, present = 0.0, 0, 0.0, False
                breathing = None
            watch = any(node_states.get(n) in _WATCH_STATES for n in node_ids)
            ai = activity_index(motion)
            rooms_out.append({
                "room": room_id,
                "activity_index": ai,
                "occupancy_count": occupancy,
            })
            room_meta[room_id] = {
                "occupancy": occupancy, "confidence": confidence,
                "present": present, "activity_index": ai, "watch": watch,
                "motion": motion, "breathing": breathing,
            }

        tracks, degraded = self._tracks(room_meta)
        return rooms_out, tracks, degraded

    @staticmethod
    def _room_breathing(snaps: list[_NodeSnapshot]) -> float | None:
        """Mean rolling breathing rate across the room's *warmed-up* nodes.

        Only nodes whose breathing window holds >= RESP_MIN_SAMPLES contribute,
        so the published estimate is genuinely smoothed over the window rather
        than a barely-averaged first reading. None when no node has warmed up.
        """
        warm = [
            s.rolling_breathing()
            for s in snaps
            if len(s.breath_window) >= RESP_MIN_SAMPLES
        ]
        warm = [b for b in warm if b is not None]
        return sum(warm) / len(warm) if warm else None

    @staticmethod
    def _vitals_fields(meta: dict) -> dict:
        """Optional coarse vitals (respiration, posture) for a track in a room.

        Both are withheld below VITALS_CONF_MIN ("absent when confidence too
        low"); respiration is additionally withheld until the breathing window
        has warmed up (``breathing`` is None). Estimates only — no raw readings.
        """
        out: dict = {}
        if meta["confidence"] < VITALS_CONF_MIN:
            return out
        out["posture_estimate"] = posture_estimate(
            meta["motion"], present=meta["present"], watch=meta["watch"]
        )
        if meta["breathing"] is not None:
            out["respiration_bpm_estimate"] = round(meta["breathing"])
        return out

    def _tracks(self, room_meta: dict[str, dict]):
        """Resident track "A" in the strongest room, plus guest-N for extras."""
        occupied = [rid for rid, m in room_meta.items() if m["present"]]
        if not occupied:
            return [], False

        # Primary room = strongest activity among occupied rooms (stable on tie).
        primary = max(
            occupied,
            key=lambda rid: (room_meta[rid]["activity_index"], -self._room_order(rid)),
        )
        # Low separation confidence in the primary room: degrade to count badges.
        if room_meta[primary]["confidence"] < TRACK_CONF_MIN:
            return [], True

        tracks = [{
            "track_id": self.layout.track_label,
            "label": self.layout.track_label,
            "room": primary,
            "confidence": round(room_meta[primary]["confidence"], 2),
            "identity_confidence": self.layout.identity_confidence,
            "state": "fall_watch" if room_meta[primary]["watch"] else "normal",
            **self._vitals_fields(room_meta[primary]),
        }]

        # Unrecognised extra targets become anonymous guest tracks: every extra
        # occupant in the primary room, and every occupant of the other rooms.
        #
        # Identity-honesty invariant (BAT-35): guest tracks carry NO vitals
        # estimates. _vitals_fields is room-level — every track in a room would
        # be handed the *same* respiration/posture. For the lone resident track
        # that is a fair attribution; for a guest co-located with the resident it
        # is a lie: a guest standing next to a fallen resident would otherwise
        # read posture_estimate="floor" and inherit the resident's breathing.
        # RuView CSI does not separate vitals per target at room level today, so
        # we withhold rather than misattribute (option 1) — guests carry presence
        # + room + state only. Per-target attribution is deferred until/if the
        # sensing model can genuinely tell two people's vitals apart; only then
        # may a guest track carry its own (not the room's) estimates.
        guest_n = 0
        for rid in sorted(occupied, key=self._room_order):
            extra = room_meta[rid]["occupancy"] - (1 if rid == primary else 0)
            for _ in range(max(0, extra)):
                guest_n += 1
                tracks.append({
                    "track_id": f"guest-{guest_n}",
                    "label": f"guest-{guest_n}",
                    "room": rid,
                    "confidence": round(room_meta[rid]["confidence"], 2),
                    "identity_confidence": 0.0,
                    "state": "guest",
                })
        return tracks, False

    def _room_order(self, room_id: str) -> int:
        ids = self.layout.room_ids()
        return ids.index(room_id) if room_id in ids else len(ids)

    # -- events --------------------------------------------------------------

    def _dropout_events(self, now: float) -> list[dict]:
        events = []
        for node_id, last in self._last_seen_at.items():
            if node_id in self._dropped:
                continue
            if now - last > DROPOUT_SECS:
                self._dropped.add(node_id)
                room = self.layout.room_for_node(node_id)
                events.append({
                    "type": "event",
                    "event": "sensing_dropout",
                    "home_id": self.home_id,
                    "node_id": node_id,
                    "room": room,
                    "silent_secs": round(now - last, 1),
                    "ts": now,
                })
        return events

    def _resident_events(self, tracks: list[dict], now: float) -> list[dict]:
        resident = next(
            (t for t in tracks if t["track_id"] == self.layout.track_label), None
        )
        room = resident["room"] if resident else None
        events: list[dict] = []
        if room != self._resident_room:
            # from_room None means the resident was simply already present at
            # connect (or reappeared after a vacancy) — not a movement. Emitting
            # it pushes "moved to <room>" ticker noise on every WS connect
            # (reviewer concern #2 on PR #7).
            if room is not None and self._resident_room is not None:
                events.append({
                    "type": "event",
                    "event": "room_transition",
                    "home_id": self.home_id,
                    "track_id": self.layout.track_label,
                    "from_room": self._resident_room,
                    "room": room,
                    "ts": now,
                })
            self._resident_room = room
            self._resident_room_since = now
            self._dwell_emitted = False
        elif (
            room is not None
            and not self._dwell_emitted
            and now - self._resident_room_since >= DWELL_SECS
        ):
            self._dwell_emitted = True
            events.append({
                "type": "event",
                "event": "dwell_start",
                "home_id": self.home_id,
                "track_id": self.layout.track_label,
                "room": room,
                "dwell_secs": round(now - self._resident_room_since, 1),
                "ts": now,
            })
        return events

    def _incident_events(self, new_txns: list[FallTransition]) -> list[dict]:
        events = []
        for t in new_txns:
            self._last_txn_id = max(self._last_txn_id, t.id)
            room = self.layout.room_for_node(t.node_id)
            events.append({
                "type": "incident",
                "home_id": self.home_id,
                "incident_id": t.incident_id,
                "node_id": t.node_id,
                "room": room,
                "from_state": t.from_state,
                "to_state": t.to_state,
                "kind": t.kind,
                "ts": t.at,
            })
        return events
