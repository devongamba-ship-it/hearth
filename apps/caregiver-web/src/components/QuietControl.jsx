// Resident "I'm OK / pause" quiet window control (BAT-39).
//
// One-tap "I'm fine for 2 hours" suppresses NON-fall wellness nudges for the
// window — the antidote to alert fatigue. While active it shows a live countdown
// and a cancel. A real fall ALWAYS pages, window or not: the copy says so, so a
// resident is never misled into thinking they've silenced a fall alert.

import { useEffect, useRef, useState } from "react";
import { remaining as fmtRemaining } from "../format.js";

const PAUSE_HOURS = 2; // the one-tap "I'm fine for 2 hours" duration

export default function QuietControl({ quiet, onStart, onCancel }) {
  const active = !!quiet?.active;
  const [secs, setSecs] = useState(quiet?.remaining_seconds || 0);
  const [busy, setBusy] = useState(false);
  const tick = useRef(null);

  // Re-sync the local countdown to each poll, and tick it down every second so
  // the timer is smooth between the (slower) status polls.
  useEffect(() => {
    setSecs(quiet?.remaining_seconds || 0);
    clearInterval(tick.current);
    if (active) {
      tick.current = setInterval(() => {
        setSecs((s) => (s > 0 ? s - 1 : 0));
      }, 1000);
    }
    return () => clearInterval(tick.current);
  }, [active, quiet?.remaining_seconds]);

  async function run(action) {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  }

  if (active) {
    return (
      <section className="quiet quiet--on" aria-labelledby="quiet-headline">
        <h2 id="quiet-headline" className="quiet__headline">Wellness alerts paused</h2>
        <p className="quiet__countdown" role="timer" aria-live="polite">
          {fmtRemaining(secs)} left
        </p>
        <p className="quiet__note">A fall will still alert your care circle.</p>
        <button
          type="button"
          className="btn btn--quiet-cancel"
          onClick={() => run(() => onCancel())}
          disabled={busy}
        >
          {busy ? "Resuming…" : "Resume alerts"}
        </button>
      </section>
    );
  }

  return (
    <section className="quiet" aria-labelledby="quiet-headline">
      <h2 id="quiet-headline" className="quiet__headline visually-hidden">Quiet window</h2>
      <button
        type="button"
        className="btn btn--quiet"
        onClick={() => run(() => onStart(PAUSE_HOURS))}
        disabled={busy}
      >
        {busy ? "Pausing…" : `I'm OK — pause wellness alerts for ${PAUSE_HOURS} hours`}
      </button>
      <p className="quiet__note">Falls always alert. This only pauses wellness reminders.</p>
    </section>
  );
}
