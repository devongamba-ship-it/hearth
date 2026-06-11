// Disposition form (BAT-28 incident view): records the resolution. Mirrors the
// escalation engine's contract exactly — one of the four canonical dispositions
// plus a required, non-empty reason code (which feeds detection-threshold
// tuning). Submit is disabled until both are present, so we never POST a resolve
// the engine would reject with 422.
//
// There is deliberately no "called EMS / dispatched" disposition — Hearth has no
// EMS integration. The four codes are the whole vocabulary.

import { useState } from "react";

import { dispositionLabel } from "../format.js";

export const DISPOSITIONS = [
  { code: "recovered", label: "Recovered" },
  { code: "caregiver_responding", label: "Caregiver responding" },
  { code: "advised_call_ems", label: "Advised to call EMS" },
  { code: "false_positive", label: "False positive" },
];

export default function DispositionForm({ incident, operator = "ops", onResolve }) {
  const [disposition, setDisposition] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const resolved = incident?.status === "resolved";
  const canSubmit = !resolved && !busy && disposition && reason.trim().length > 0;

  if (resolved) {
    return (
      <section aria-label="Disposition">
        <h3>Disposition</h3>
        <p className="resolved-line">
          Resolved — <strong>{dispositionLabel(incident.disposition)}</strong>
          {incident.disposition_reason ? ` (${incident.disposition_reason})` : ""}
        </p>
      </section>
    );
  }

  async function submit(e) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      await onResolve({ disposition, reason_code: reason.trim(), by: operator });
    } catch (err) {
      setError(err.message || "Failed to resolve");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Disposition">
      <h3>Disposition</h3>
      <form onSubmit={submit} className="disposition-form">
        <fieldset>
          <legend>Outcome</legend>
          {DISPOSITIONS.map((d) => (
            <label key={d.code} className="disposition-option">
              <input
                type="radio"
                name="disposition"
                value={d.code}
                checked={disposition === d.code}
                onChange={() => setDisposition(d.code)}
              />
              {d.label}
            </label>
          ))}
        </fieldset>
        <label className="disposition-reason">
          Reason code / note
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. tripped on rug, got up fine"
            required
          />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit" disabled={!canSubmit}>
          Resolve incident
        </button>
      </form>
    </section>
  );
}
