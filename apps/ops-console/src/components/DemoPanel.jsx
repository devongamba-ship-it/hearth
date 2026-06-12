// Demo control panel (BAT-43): a dev/demo-only strip that plays deterministic,
// seeded scenarios against the REAL ingest pipeline so the whole console lights
// up off real data. Shown only when the page is opened with `?demo`.
//
// It is loudly labelled DEMO and the header's data-source badge reads
// "simulated" while it runs, so a demo is never confusable with a live floor.

import { useCallback, useState } from "react";
import { playDemo, resetDemo } from "../api.js";
import { DEMO_SCENARIOS } from "../demo.js";

export default function DemoPanel() {
  const [busy, setBusy] = useState(null); // scenario key (or "reset") in flight
  const [active, setActive] = useState(null); // last scenario played
  const [error, setError] = useState(null);

  const play = useCallback(async (key) => {
    setBusy(key);
    setError(null);
    try {
      await playDemo(key);
      setActive(key);
    } catch (e) {
      setError(e.message || "play failed");
    } finally {
      setBusy(null);
    }
  }, []);

  const reset = useCallback(async () => {
    setBusy("reset");
    setError(null);
    try {
      await resetDemo();
      setActive(null);
    } catch (e) {
      setError(e.message || "reset failed");
    } finally {
      setBusy(null);
    }
  }, []);

  return (
    <section className="demo-panel" aria-label="Demo scenario player">
      <span className="demo-tag">DEMO</span>
      <span className="demo-sub">simulated scenarios — not a live floor</span>
      <div className="demo-buttons" role="group" aria-label="Play a scenario">
        {DEMO_SCENARIOS.map((s) => (
          <button
            key={s.key}
            type="button"
            className={active === s.key ? "demo-btn active" : "demo-btn"}
            disabled={busy !== null}
            aria-pressed={active === s.key}
            title={s.hint}
            onClick={() => play(s.key)}
          >
            {s.label}
          </button>
        ))}
        <button
          type="button"
          className="demo-btn reset"
          disabled={busy !== null}
          onClick={reset}
        >
          Reset
        </button>
      </div>
      {error && (
        <span className="demo-error" role="status">
          demo error — {error}
        </span>
      )}
    </section>
  );
}
