// The small technical code chip shown beside primary copy (§4.1: the technical
// state lives in a chip, never in the family-facing line).
export default function Chip({ text, title }) {
  return (
    <span className="chip" title={title}>
      {text}
    </span>
  );
}
