// Live mesh feed hook (BAT-27 consumer). Opens the 1 Hz WebSocket and keeps the
// latest room_activity frame, a bounded event ticker (newest first), and the
// incident state-machine transitions seen this session. In mock mode it seeds a
// single representative frame + ticker so the mesh view renders with no backend.
//
// Aggregated frames only — raw CSI/readings never leave the home. The console is
// purely a consumer here; it never sends on the socket.

import { useEffect, useState } from "react";
import { USE_MOCK, liveFeedUrl } from "./api.js";
import { mockLiveEvents, mockLiveFrame } from "./mockData.js";

const MAX_TICKER = 20;

export function useLiveFeed(homeId, { enabled = true } = {}) {
  const [frame, setFrame] = useState(null);
  const [ticker, setTicker] = useState([]);
  const [transitions, setTransitions] = useState([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!enabled) return undefined;
    if (USE_MOCK) {
      setFrame(mockLiveFrame);
      setTicker([...mockLiveEvents].reverse());
      setConnected(true);
      return undefined;
    }
    let ws;
    try {
      ws = new WebSocket(liveFeedUrl(homeId));
    } catch {
      return undefined;
    }
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === "room_activity") {
        setFrame(msg);
      } else if (msg.type === "event") {
        setTicker((t) => [msg, ...t].slice(0, MAX_TICKER));
      } else if (msg.type === "incident") {
        setTransitions((t) => [...t, msg]);
      }
    };
    return () => {
      try {
        ws.close();
      } catch {
        // already closing
      }
    };
  }, [homeId, enabled]);

  return { frame, ticker, transitions, connected };
}
