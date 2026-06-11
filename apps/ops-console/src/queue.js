// Queue model (BAT-28 §Queue). Pure ordering logic, kept out of the component
// so it can be unit-tested directly.
//
// "Alerts across homes, P1 falls on top with age timers." v1 runs a single home
// but the model must not assume it — every queue row carries its home_name, and
// ordering is multi-home from the start.

// Escalation status strings (mirror escalation/db.py).
export const ACTIVE = "active";
export const CLAIMED = "claimed";
export const RESOLVED = "resolved";

// Every Hearth incident today originates from a confirmed fall, which is a P1
// life-safety alert. The model keeps an explicit priority so future, lower-acuity
// alert types (e.g. wellness rules) can rank beneath falls without code changes.
export function incidentPriority(incident) {
  return incident.priority || "P1";
}

const PRIORITY_RANK = { P1: 0, P2: 1, P3: 2 };
// Unclaimed alerts demand attention first; resolved drop to the bottom.
const STATUS_RANK = { [ACTIVE]: 0, [CLAIMED]: 1, [RESOLVED]: 2 };

// Order the queue: P1 first, then unclaimed-before-claimed-before-resolved,
// then oldest-first so the longest-waiting alert sits on top with the highest
// age timer. Stable and pure — does not mutate the input.
export function queueOrder(incidents) {
  return [...(incidents || [])].sort((a, b) => {
    const pr = (PRIORITY_RANK[incidentPriority(a)] ?? 9) -
      (PRIORITY_RANK[incidentPriority(b)] ?? 9);
    if (pr !== 0) return pr;
    const sr = (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9);
    if (sr !== 0) return sr;
    return (a.created_at ?? 0) - (b.created_at ?? 0);
  });
}

// The unresolved slice — what an operator is actively working.
export function openIncidents(incidents) {
  return (incidents || []).filter((i) => i.status !== RESOLVED);
}
