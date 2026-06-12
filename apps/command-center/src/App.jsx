// Command Center app shell (BAT-29).
//
// Loads the home layout once, builds the static scene geometry, opens the 1 Hz
// live feed, and folds each WS message into scene state with the pure store
// reducer. Renders the 3D scene with the HUD, view/figure controls, and event
// ticker on top. Audience is admin + sales demos — this is not a caregiver
// surface (no "Mom is okay" framing, no PII, aggregates only).

import { useEffect, useRef, useState } from "react";
import SceneView from "./scene/SceneView.jsx";
import ViewBar from "./components/ViewBar.jsx";
import HUD from "./components/HUD.jsx";
import Ticker from "./components/Ticker.jsx";
import { getLayout, openLiveFeed, DEFAULT_HOME } from "./api.js";
import { layoutToScene } from "./lib/layout.js";
import { applyMessage, initialState } from "./lib/store.js";
import { deriveSourceBadge } from "./lib/source.js";
import { DEFAULT_VIEW } from "./lib/views.js";
import { DEFAULT_FIGURE_STYLE } from "./lib/figures.js";

export default function App({ homeId = DEFAULT_HOME }) {
  const [scene, setScene] = useState(null);
  const [state, setState] = useState(null);
  const [view, setView] = useState(DEFAULT_VIEW);
  const [style, setStyle] = useState(DEFAULT_FIGURE_STYLE);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);
  const sceneRef = useRef(null);

  useEffect(() => {
    let feed = null;
    let cancelled = false;
    (async () => {
      try {
        const layout = await getLayout(homeId);
        if (cancelled) return;
        const geo = layoutToScene(layout);
        sceneRef.current = geo;
        setScene(geo);
        setState(initialState(geo));
        feed = openLiveFeed(
          homeId,
          (msg) => {
            setState((prev) =>
              applyMessage(prev ?? initialState(sceneRef.current), msg, sceneRef.current),
            );
          },
          { onStatus: (up) => !cancelled && setConnected(up) },
        );
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      }
    })();
    return () => {
      cancelled = true;
      feed?.close();
    };
  }, [homeId]);

  // Data-source badge (BAT-34): the frame's source label + feed health, derived
  // purely from store state and the WS connection flag. Null frame until the
  // first room_activity → the badge reads "feed offline".
  const sourceBadge = deriveSourceBadge({
    connected,
    frame:
      state?.lastTs == null
        ? null
        : {
            source: state.source,
            home_id: state.homeId,
            tracking_degraded: state.tracking_degraded,
            nodeCount: state.figures.length,
          },
    offlineCount: state?.offlineNodes.length ?? 0,
  });

  if (error) {
    return (
      <div className="app error" role="alert">
        <h1>Command Center</h1>
        <p>Could not load the home: {error}</p>
        <p className="hint">
          Start the ingest service (or run with <code>VITE_USE_MOCK=1</code> for a
          backendless demo).
        </p>
      </div>
    );
  }

  return (
    <div className="app">
      <ViewBar
        view={view}
        onView={setView}
        style={style}
        onStyle={setStyle}
        fallWatch={!!state?.fallWatch}
      />
      <div className="stage">
        {scene && <SceneView scene={scene} state={state} view={view} style={style} />}
        <HUD hud={state?.hud} sourceBadge={sourceBadge} />
        {state?.tracking_degraded && (
          <div className="degraded-banner" role="status">
            Tracking degraded — showing room occupancy counts
            {(state.occupancyBadges ?? []).map((b) => (
              <span key={b.room} className="occ-badge">
                {b.room}: {b.count}
              </span>
            ))}
          </div>
        )}
        <Ticker events={state?.events} />
      </div>
    </div>
  );
}
