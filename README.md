# Hearth — WiFi Fall Detection for Aging in Place

Contactless fall detection and wellness monitoring built on RuView WiFi
sensing (ESP32 CSI nodes). No wearables, no cameras, no buttons. The house is
the alert system. (Business concept doc is private, kept off-repo.)

Built autonomously via the **Watchtower loop**: Linear issues labeled
`agent-ready` → Claude Code GitHub Action → draft PRs → human review.

## Setup

`scripts/bootstrap.ps1` does everything: creates the repo, pushes, applies
branch protection on `main` (require PR + 1 review), sets the
`ANTHROPIC_API_KEY` and `LINEAR_API_KEY` Actions secrets, and dispatches the
first agent run (BAT-20). It is idempotent — re-run it any time and it only
does what's missing. The 6-hour cron in
`.github/workflows/watchtower-agent.yml` is live: the agent works the
highest-priority unblocked `agent-ready` issue, opens a draft PR, and your
review/merge is what unblocks the next issue in the chain.

**Test without hardware**
Once BAT-21's PR lands, the full pipeline runs on simulated data:

    python tools/packet_sim.py --scenario fall_confirmed --speed 10

Scenarios: `normal`, `empty`, `fall_confirmed`, `fall_recovered`,
`quiet_morning`. The fall machine (BAT-22) should escalate exactly once on
`fall_confirmed` and never on `fall_recovered` — that asymmetry is the
product.

**Hardware (BAT-26 checklist in Linear)**
4× ESP32 boards (~$40), flash RuView Tier-2 firmware, point nodes at the
ingest listener, then a calibration day with ground-truth notes.

## Kill switches
Pull the `agent-ready` label from an issue, or disable the workflow in the
Actions tab. Branch protection means nothing reaches `main` without you.

## Layout
    apps/ingest/         UDP listener, codec, fall state machine, rules engine
    apps/escalation/     SMS/voice cascade (Twilio, dry-run by default)
    apps/caregiver-web/  "Mom is okay" status app
    tools/packet_sim.py  hardware-free test driver (works today)
    scripts/bootstrap.ps1  idempotent GitHub setup
    CLAUDE.md            binding agent rules
