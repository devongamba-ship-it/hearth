"""Data-retention policy + an explicit, opt-in purge tool (BAT-41).

The concept doc's long game is fall *prediction* from longitudinal data, so the
raw material — ``readings`` (one row per vitals packet) and ``fall_transitions``
(every fall-machine decision, with its feature snapshot) — is worth keeping. The
binding rule: **nothing in this codebase deletes that data silently.** There is
exactly one deletion path, and it lives here. It runs only when an operator
explicitly asks for it, only within a configured window, and logs every row it
removes.

Policy knob: ``HEARTH_RETENTION`` (default: keep everything, forever).

  * unset / ``""`` / ``all`` / ``keep-all`` — keep everything (the default).
  * ``180``                                  — keep every purgeable table 180 days.
  * ``readings=180,transitions=730``         — per-table windows; ``all`` per
    table means keep that table forever.

A "window" is the maximum age in days a row may reach before the purge tool is
*allowed* to remove it. Setting a window does **not** delete anything on its own
— it only widens what an explicit ``purge(..., confirm=True)`` may touch.

Only tables with a real wall-clock column can be aged: ``readings.received_at``
and ``fall_transitions.at`` (both epoch seconds). The BAT-21 ``events`` table
carries only ms-since-boot ``ts`` (reset on every node reboot), which is not a
real timestamp, so it cannot be safely aged and is never purged — it is
append-only and kept indefinitely. Rows whose timestamp column is NULL (legacy /
undated) are likewise never purged.

Run the tool: ``python -m ingest.retention`` (dry-run report) or
``python -m ingest.retention --confirm`` (actually delete). See
``docs/data-retention.md`` for the full policy + privacy posture.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select

from .db import Event, FallTransition, Reading, make_session_factory

log = logging.getLogger("hearth.ingest.retention")

# Friendly table name -> (model, wall-clock epoch column). Only these can be
# aged; anything not listed here is never a purge target. ``events`` is absent
# on purpose: it has no real timestamp (see module docstring).
PURGEABLE: dict[str, tuple[type, str]] = {
    "readings": (Reading, "received_at"),
    "transitions": (FallTransition, "at"),
}

_KEEP_ALL_TOKENS = {"", "all", "keep-all", "keep_all", "keepall", "none", "off"}
_PER_TABLE_KEEP = {"all", "keep", "forever", "none"}

SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class RetentionConfig:
    """Resolved retention policy. ``windows`` maps a purgeable table to its max
    age in days; a table absent from ``windows`` is kept forever."""

    windows: dict[str, int] = field(default_factory=dict)

    @property
    def keep_all(self) -> bool:
        """True when no table has a finite window — the default posture."""
        return not self.windows

    def max_age_days(self, table: str) -> int | None:
        """Configured window for ``table`` in days, or ``None`` to keep forever."""
        return self.windows.get(table)


def _parse_days(raw: str, *, where: str) -> int:
    try:
        days = int(raw)
    except ValueError as exc:
        raise ValueError(f"HEARTH_RETENTION: {where} must be an integer day count "
                         f"or 'all', got {raw!r}") from exc
    if days < 0:
        raise ValueError(f"HEARTH_RETENTION: {where} days must be >= 0, got {days}")
    return days


def parse_retention(spec: str | None) -> RetentionConfig:
    """Parse a ``HEARTH_RETENTION`` spec string into a :class:`RetentionConfig`.

    Fails loud on an unknown table or a malformed value — a misconfigured
    retention policy must never silently fall back to deleting (or to keeping)
    the wrong thing.
    """
    if spec is None:
        return RetentionConfig()
    spec = spec.strip()
    if spec.lower() in _KEEP_ALL_TOKENS:
        return RetentionConfig()

    # A bare integer applies one window to every purgeable table.
    if "=" not in spec:
        days = _parse_days(spec, where="value")
        return RetentionConfig(windows={t: days for t in PURGEABLE})

    windows: dict[str, int] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"HEARTH_RETENTION: expected 'table=value', got {part!r}")
        table, _, value = part.partition("=")
        table, value = table.strip(), value.strip()
        if table not in PURGEABLE:
            raise ValueError(
                f"HEARTH_RETENTION: unknown table {table!r}; "
                f"purgeable tables are {sorted(PURGEABLE)}")
        if value.lower() in _PER_TABLE_KEEP:
            continue  # keep this table forever; leave it out of windows
        windows[table] = _parse_days(value, where=f"{table}")
    return RetentionConfig(windows=windows)


def load_retention_config() -> RetentionConfig:
    """Resolve the policy from ``$HEARTH_RETENTION`` (default: keep everything)."""
    return parse_retention(os.environ.get("HEARTH_RETENTION"))


@dataclass(frozen=True)
class TablePurge:
    """What a purge did (or, in a dry run, would do) for one table."""

    table: str
    max_age_days: int | None
    cutoff_epoch: float | None
    matched: int  # rows older than the cutoff
    deleted: int  # rows actually removed (0 in a dry run / keep-all)


@dataclass(frozen=True)
class PurgeReport:
    dry_run: bool
    items: list[TablePurge]

    @property
    def total_matched(self) -> int:
        return sum(i.matched for i in self.items)

    @property
    def total_deleted(self) -> int:
        return sum(i.deleted for i in self.items)


def purge(
    session_factory,
    config: RetentionConfig | None = None,
    *,
    now: float | None = None,
    confirm: bool = False,
    logger: logging.Logger | None = None,
) -> PurgeReport:
    """Apply the retention policy. Deletes rows **only** when ``confirm`` is True.

    With ``confirm=False`` (the default) this is a dry run: it counts what each
    table's window matches and deletes nothing — the report tells the operator
    what an explicit run would remove. A table with no configured window, and any
    row with a NULL timestamp, is never touched. Every table is logged either
    way, so a purge is always reconstructable from the logs.
    """
    config = config or load_retention_config()
    logger = logger or log
    now = time.time() if now is None else now

    items: list[TablePurge] = []
    for table, (model, col_name) in PURGEABLE.items():
        max_age = config.max_age_days(table)
        if max_age is None:
            logger.info("retention: %s keep-all, nothing to purge", table,
                        extra={"event": "retention_keep_all", "count": 0})
            items.append(TablePurge(table, None, None, 0, 0))
            continue

        cutoff = now - max_age * SECONDS_PER_DAY
        col = getattr(model, col_name)
        # NULL timestamps are undatable -> never eligible. Only strictly-older.
        cond = (col.is_not(None)) & (col < cutoff)
        with session_factory() as session:
            matched = int(session.scalar(
                select(func.count()).select_from(model).where(cond)) or 0)
            deleted = 0
            if confirm and matched:
                deleted = session.execute(delete(model).where(cond)).rowcount or 0
                session.commit()
            items.append(TablePurge(table, max_age, cutoff, matched, deleted))
            if confirm:
                logger.info("retention: purged %d/%d %s rows older than %dd",
                            deleted, matched, table, max_age,
                            extra={"event": "retention_purge", "count": deleted})
            else:
                logger.info("retention: dry-run, %d %s rows older than %dd would purge",
                            matched, table, max_age,
                            extra={"event": "retention_dry_run", "count": matched})

    return PurgeReport(dry_run=not confirm, items=items)


# Pipeline tables the demo-mode reset (BAT-43) wipes wholesale, newest-dependent
# first. This is the only deletion in the codebase besides the retention purge,
# and it lives here on purpose: deletion stays in one auditable, logged module
# (see the no-silent-deletion guard in test_retention.py).
_DEMO_TABLES = (FallTransition, Event, Reading)


def purge_demo_data(session_factory, *, logger: logging.Logger | None = None) -> dict[str, int]:
    """Wipe every pipeline row for a demo reset (BAT-43). Returns rows cleared per table.

    Unlike :func:`purge`, this is unconditional and total — demo readings are
    disposable, so a reset clears everything rather than an aged window. It is
    reachable only from the ``HEARTH_DEMO``-gated demo endpoints and must never
    be pointed at a production database; like the retention purge, every reset is
    logged so the deletion is never silent.
    """
    logger = logger or log
    counts: dict[str, int] = {}
    with session_factory() as session:
        for model in _DEMO_TABLES:
            counts[model.__tablename__] = session.execute(delete(model)).rowcount or 0
        session.commit()
    logger.info("demo reset: cleared %s", counts, extra={"event": "demo_reset"})
    return counts


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI entrypoint
    import argparse

    from .db import init_db, make_engine
    from .logconfig import configure_logging

    parser = argparse.ArgumentParser(
        description="Hearth data-retention purge (BAT-41). Dry-run unless --confirm.")
    parser.add_argument(
        "--confirm", action="store_true",
        help="actually delete eligible rows (otherwise report only, delete nothing)")
    args = parser.parse_args(argv)

    configure_logging("retention")
    config = load_retention_config()
    engine = make_engine()
    init_db(engine)
    sessions = make_session_factory(engine)

    if config.keep_all:
        log.info("retention policy is keep-all (HEARTH_RETENTION unset); nothing to do",
                 extra={"event": "retention_keep_all", "count": 0})

    report = purge(sessions, config, confirm=args.confirm)
    verb = "deleted" if args.confirm else "would delete"
    log.info("retention run complete: %s %d rows across %d tables",
             verb, report.total_deleted if args.confirm else report.total_matched,
             len(report.items), extra={"event": "retention_done"})
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
