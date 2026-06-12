# Data retention & privacy posture (BAT-41)

Hearth's long game is fall **prediction** from longitudinal data. That only
works if the raw material is still around to learn from when there are pilot
homes to learn from — so the default posture is **keep everything, forever**,
and **nothing in the codebase deletes that data silently.** There is exactly one
deletion path (`ingest/retention.py`); it runs only when an operator explicitly
asks, only within a configured window, and logs every row it removes.

## What is kept

| Table              | One row per…                                  | Real timestamp        | Purgeable |
| ------------------ | --------------------------------------------- | --------------------- | --------- |
| `readings`         | decoded vitals packet                         | `received_at` (epoch) | yes       |
| `fall_transitions` | fall-machine decision, with feature snapshot  | `at` (epoch)          | yes       |
| `events`           | fall-flag toggle (BAT-21)                     | — (ms-since-boot only)| no        |

Every `fall_transitions` row — including `transition`, `near_fall`, and
`suppressed` rows — persists a **full feature snapshot** (the
motion/breathing/confidence/window state at decision time, keyed by
`fall_machine.FEATURE_KEYS`), not just the state name. That snapshot is the
training-set foundation; a row without it would be useless to learn from later.

`events` carries only `ts` (ms-since-boot, which resets on every node reboot),
so it has no real wall-clock and cannot be safely aged — it is append-only and
kept indefinitely. Rows whose timestamp column is `NULL` (legacy / undated) are
likewise never purged.

## How long it is kept — the `HEARTH_RETENTION` knob

Retention is config-driven via the `HEARTH_RETENTION` environment variable. The
default keeps everything; a value only ever *widens* what an explicit purge is
allowed to remove — setting it never deletes anything on its own.

| `HEARTH_RETENTION`              | Meaning                                            |
| ------------------------------- | -------------------------------------------------- |
| *(unset)* / `all` / `keep-all`  | keep everything, forever (**default**)             |
| `180`                           | keep every purgeable table at most 180 days        |
| `readings=180,transitions=730`  | per-table windows (days)                           |
| `readings=90,transitions=all`   | `all` per table = keep that table forever          |

An unknown table name or a malformed value is a hard error — a misconfigured
policy must never silently fall back to deleting (or keeping) the wrong thing.

## Archival path — the explicit, opt-in purge tool

```
python -m ingest.retention            # DRY RUN: report what would be purged, delete nothing
python -m ingest.retention --confirm  # actually delete eligible rows
```

- **Opt-in:** without `--confirm` the tool only reports counts; it deletes
  nothing. Deletion requires both a configured window *and* `--confirm`.
- **Logged:** every table is logged on every run (`retention_purge` /
  `retention_dry_run` / `retention_keep_all` event codes), so a purge is always
  reconstructable from the logs alone.
- **Archive first if you need the history.** Purging is destructive. Before a
  windowed purge, copy the SQLite file (or export the table) off to cold storage
  — keeping the local DB lean while preserving the longitudinal record. The tool
  deletes; it does not archive for you.

There is **no scheduled or automatic purge.** Cleanup happens only when a human
runs the command above.

## Privacy posture

- **Local-first.** All of this lives in one SQLite file on the home device. No
  raw readings (and no raw CSI) ever leave the home — only alert events and
  coarse status do. Do not point `HEARTH_DB_PATH` at cloud storage.
- **No cameras, no microphones** except during an active voice check-in window
  (BAT-30): the mic opens only while a check-in is live, recognition is
  yes/no-class only, and no audio is recorded, persisted, or sent off-device.
- **Coarse to caregivers.** The caregiver app sees states and coarse activity
  blocks — never per-second data.
- Retaining data longer is a *local* decision with *local* blast radius: the
  data never crosses the home boundary, so "keep everything" does not widen the
  off-device exposure surface.
