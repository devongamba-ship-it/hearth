// Thin React wrapper around the imperative Three.js renderer (BAT-29).
//
// Mounts the scene once, then pushes scene-state + UI selections (view, figure
// style, geometry) into it on every change. All WebGL lives in renderer.js;
// this component just owns the container <div> and the renderer lifecycle. In
// tests this module is mocked (jsdom has no WebGL), so the rest of the app
// stays unit-testable.

import { useEffect, useRef } from "react";
import { createScene } from "./renderer.js";

export default function SceneView({ scene, state, view, style }) {
  const containerRef = useRef(null);
  const sceneApiRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const api = createScene(containerRef.current, { view, style });
    sceneApiRef.current = api;
    return () => api.dispose();
    // Mount once; updates flow through the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (sceneApiRef.current && scene) {
      sceneApiRef.current.update(state, { scene, view, style });
    }
  }, [scene, state, view, style]);

  return <div className="scene-view" ref={containerRef} data-testid="scene-view" />;
}
