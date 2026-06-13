// View bar (BAT-29): the camera-view switch and the "Figures" style switch.
//
// Views: bird's-eye / ground / orbit. Figures: capsule / column / marker
// (canonical update §2). Both are pure UI over the parent's state — selecting a
// figure style is config-only at runtime, exactly the switchable control the
// design calls for.

import { VIEWS } from "../lib/views.js";
import { FIGURE_STYLES } from "../lib/figures.js";

const VIEW_LABELS = { birdseye: "Bird's eye", ground: "Ground", orbit: "Orbit", field: "Field" };
const STYLE_LABELS = { capsule: "Capsule", column: "Column", marker: "Marker" };

export default function ViewBar({ view, onView, style, onStyle, fallWatch }) {
  return (
    <div className="view-bar">
      <div className="brand">
        Hearth <span className="brand-sub">Command Center</span>
      </div>

      <div className="control" role="group" aria-label="Camera view">
        <span className="control-label">View</span>
        {VIEWS.map((v) => (
          <button
            key={v}
            type="button"
            className={v === view ? "seg active" : "seg"}
            aria-pressed={v === view}
            onClick={() => onView(v)}
          >
            {VIEW_LABELS[v] ?? v}
          </button>
        ))}
      </div>

      <div className="control" role="group" aria-label="Figures">
        <span className="control-label">Figures</span>
        {FIGURE_STYLES.map((s) => (
          <button
            key={s}
            type="button"
            className={s === style ? "seg active" : "seg"}
            aria-pressed={s === style}
            onClick={() => onStyle(s)}
          >
            {STYLE_LABELS[s] ?? s}
          </button>
        ))}
      </div>

      {fallWatch && (
        <div className="fall-watch-badge" role="status">
          ● FALL WATCH
        </div>
      )}
    </div>
  );
}
