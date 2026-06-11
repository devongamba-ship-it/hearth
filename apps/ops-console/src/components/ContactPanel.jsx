// Contact panel (BAT-28 incident view): the home's care circle in the exact
// order they are contacted during a fall. Read-only here — the ops operator sees
// who is being reached and how, and who (if anyone) has claimed.

export default function ContactPanel({ contacts = [], claimedBy }) {
  const ordered = [...contacts].sort((a, b) => (a.priority ?? 99) - (b.priority ?? 99));
  return (
    <section aria-label="Contact panel">
      <h3>Care circle</h3>
      <ol className="contacts">
        {ordered.map((c) => (
          <li key={c.id} className={claimedBy && claimedBy === c.name ? "contact claimed" : "contact"}>
            <span className="contact-rank">{c.priority}</span>
            <span className="contact-name">{c.name}</span>
            {c.relation && <span className="contact-relation">{c.relation}</span>}
            <span className="contact-channels">{(c.channels || []).join(" · ")}</span>
            {claimedBy && claimedBy === c.name && <span className="badge ok">claimed</span>}
          </li>
        ))}
      </ol>
      <p className="note">Contacted in priority order during a fall.</p>
    </section>
  );
}
