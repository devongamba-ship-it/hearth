// Settings (BAT-25): wellness rule sensitivity, quiet hours, and the verbatim
// privacy statement. These are local UI controls in v1 — the per-home tuning
// store (detection.yaml / routines.yaml) is operator-set; this screen states the
// caregiver-facing posture and the brand promise.

import { useState } from "react";

const SENSITIVITIES = ["Low", "Standard", "High"];

// Verbatim per the issue — do not paraphrase.
export const PRIVACY_STATEMENT = "No cameras, no audio recording.";

export default function Settings() {
  const [sensitivity, setSensitivity] = useState("Standard");
  const [quietStart, setQuietStart] = useState("22:00");
  const [quietEnd, setQuietEnd] = useState("07:00");

  return (
    <div className="screen">
      <h2>Settings</h2>

      <fieldset className="fieldset">
        <legend>Wellness sensitivity</legend>
        <div className="segmented" role="group" aria-label="Wellness sensitivity">
          {SENSITIVITIES.map((s) => (
            <button
              key={s}
              type="button"
              className="segmented__btn"
              aria-pressed={sensitivity === s}
              onClick={() => setSensitivity(s)}
            >
              {s}
            </button>
          ))}
        </div>
        <p className="note">Higher sensitivity raises soft wellness concerns sooner.</p>
      </fieldset>

      <fieldset className="fieldset">
        <legend>Quiet hours</legend>
        <label className="field">
          <span className="field__label">From</span>
          <input type="time" value={quietStart} onChange={(e) => setQuietStart(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">To</span>
          <input type="time" value={quietEnd} onChange={(e) => setQuietEnd(e.target.value)} />
        </label>
      </fieldset>

      <section className="privacy" aria-labelledby="privacy-heading">
        <h3 id="privacy-heading">Privacy</h3>
        <p className="privacy__statement">{PRIVACY_STATEMENT}</p>
        <p className="note">
          Hearth senses motion and presence over WiFi. Sensing data stays on the
          home device; only alerts and coarse status ever leave.
        </p>
      </section>
    </div>
  );
}
