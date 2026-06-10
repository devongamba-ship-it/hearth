# CLAUDE.md — Hearth Build Agent Rules

You are building **Hearth**: contactless WiFi-sensing fall detection for older
adults. RuView ESP32 nodes (Tier-2 firmware) broadcast vitals packets; this
repo is everything downstream — ingest, fall confirmation, escalation,
caregiver app. The full concept doc is private (off-repo); the invariants
below and the Linear issue specs are the authoritative extract.

## Binding rules (non-negotiable)

1. **One Linear issue per run.** Only issues labeled `agent-ready` whose
   blockers are Done. Anything else: comment why on the issue and stop.
2. **Draft PRs only. Never push to `main`. Never merge. Never mark your own
   PR ready-for-review.** A human review is the irreversible-action boundary.
3. **Tests are part of the work.** Every acceptance criterion in the issue
   maps to at least one test. The suite must be green before you open the PR.
4. **Stay in scope.** Don't refactor neighboring code, add infra, or "improve"
   things the issue didn't ask for. File a Linear comment suggesting follow-up
   issues instead.
5. **Ambiguity = stop.** If the spec underdetermines a decision that matters,
   comment your question on the Linear issue and exit. A vague ticket must
   produce a question, not a vague PR.
6. **No secrets in code, ever.** Twilio/API creds via env only. If a test
   needs creds, it runs in dry-run mode instead.
7. **No real-world side effects from CI.** `ESCALATION_MODE=dry_run` is the
   default everywhere; live Twilio mode is human-set only.

## Architecture invariants

- **Local-first.** All sensing data stays on the home device. Only alert
  events and coarse status leave. Do not add cloud storage of raw readings.
- **Privacy is the brand.** No cameras, no microphones (until the explicit
  voice-check-in issue), no per-second data exposed to the caregiver app —
  states and coarse activity blocks only.
- **SQLite + Python (FastAPI) services, React+Vite frontend.** Don't introduce
  new databases, queues, or frameworks without an issue that says to.
- **Shared packet codec** lives in one module (`hearth/packet.py`); the
  simulator (`tools/packet_sim.py`) and the ingest listener both import it.
  The RuView firmware docs (ADR-039/044) are the source of truth for layout.

## Definition of done (every PR)

- Acceptance criteria demonstrably met (paste test output in PR body)
- `ruff` clean, `pytest` green, frontend builds if touched
- PR body lists assumptions + anything the human should verify on hardware
- Linear issue moved to In Review with the PR linked
