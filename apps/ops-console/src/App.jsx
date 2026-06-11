// Hearth ops console (BAT-28).
//
// Monitoring-center surface: desktop, high-density, queue-driven, clinical tone,
// multi-monitor friendly. Four surfaces — Queue, Incident view, Home detail,
// History — over the ingest + escalation services and the 1 Hz live mesh feed.
//
// Privacy is the brand: the console renders coarse status, aggregated room
// activity, and anonymous tracks only — never names, cameras, audio, or raw
// readings. Identity is shown as track labels with the heuristic disclaimer.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  claimIncident,
  getActiveIncidents,
  getCareCircle,
  getDailyActiveMinutes,
  getFallIncidentDetail,
  getIncidentDetail,
  getIncidents,
  getLayout,
  getNodes,
  getThresholds,
  getTrends,
  resolveIncident,
} from "./api.js";
import { useLiveFeed } from "./live.js";
import Queue from "./components/Queue.jsx";
import IncidentView from "./components/IncidentView.jsx";
import HomeDetail from "./components/HomeDetail.jsx";
import History from "./components/History.jsx";

const POLL_MS = 4000;
const HOME_ID = import.meta.env.VITE_HOME_ID || "home";
const OPERATOR = import.meta.env.VITE_OPERATOR || "ops";

const TABS = [
  { key: "queue", label: "Queue" },
  { key: "incident", label: "Incident" },
  { key: "home", label: "Home detail" },
  { key: "history", label: "History" },
];

export default function App() {
  const [tab, setTab] = useState("queue");
  const [incidents, setIncidents] = useState([]);
  const [history, setHistory] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null); // escalation incident + cascade
  const [fallDetail, setFallDetail] = useState(null); // ingest state-machine replay
  const [layout, setLayout] = useState(null);
  const [nodes, setNodes] = useState(null);
  const [trends, setTrends] = useState(null);
  const [thresholds, setThresholds] = useState(null);
  const [contacts, setContacts] = useState([]);
  const [activeMinutes, setActiveMinutes] = useState(null);
  const [error, setError] = useState(null);

  const { frame, ticker } = useLiveFeed(HOME_ID);

  // One-time reference data (layout, sensing health, trends, thresholds, circle).
  useEffect(() => {
    getLayout(HOME_ID).then(setLayout).catch(() => {});
    getNodes().then(setNodes).catch(() => {});
    getTrends(HOME_ID).then(setTrends).catch(() => {});
    getThresholds().then(setThresholds).catch(() => {});
    getCareCircle().then(setContacts).catch(() => {});
    getDailyActiveMinutes().then(setActiveMinutes).catch(() => {});
  }, []);

  // Poll the live queue (active) + full history.
  const refresh = useCallback(async () => {
    try {
      const [active, all] = await Promise.all([getActiveIncidents(), getIncidents()]);
      setIncidents(active.incidents || []);
      setHistory(all.incidents || []);
      setError(null);
    } catch (e) {
      setError(e.message || "offline");
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  // Load an incident's detail (cascade + state-machine replay) when opened.
  const loadDetail = useCallback(async (incidentId) => {
    const inc = await getIncidentDetail(incidentId);
    setDetail(inc);
    if (inc.source_incident_id) {
      getFallIncidentDetail(inc.source_incident_id).then(setFallDetail).catch(() => setFallDetail(null));
    } else {
      setFallDetail(null);
    }
  }, []);

  const openIncident = useCallback(
    (incidentId) => {
      setSelectedId(incidentId);
      setTab("incident");
      loadDetail(incidentId).catch((e) => setError(e.message));
    },
    [loadDetail],
  );

  const handleClaim = useCallback(
    async (incidentId) => {
      try {
        await claimIncident(incidentId, OPERATOR);
        await refresh();
        if (detail?.id === incidentId) await loadDetail(incidentId);
      } catch (e) {
        setError(e.message);
      }
    },
    [refresh, loadDetail, detail],
  );

  const handleResolve = useCallback(
    async (incidentId, payload) => {
      await resolveIncident(incidentId, payload);
      await refresh();
      await loadDetail(incidentId);
    },
    [refresh, loadDetail],
  );

  // Track-by-room (from the live frame) lets the queue show match confidence.
  const trackByRoom = useMemo(() => {
    const map = {};
    for (const t of frame?.tracks || []) {
      if (!map[t.room]) map[t.room] = t;
    }
    return map;
  }, [frame]);

  // The track label for the selected incident's room (identity-honest).
  const selectedTrackLabel = detail ? trackByRoom[detail.room]?.label || "A" : "A";

  return (
    <div className="app">
      <header className="topbar">
        <h1>Hearth Ops</h1>
        <nav className="tabs" aria-label="Sections">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              className={tab === t.key ? "tab active" : "tab"}
              aria-current={tab === t.key ? "page" : undefined}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </nav>
        {error && <span className="topbar-error" role="status">offline — {error}</span>}
      </header>

      <main className="content">
        {tab === "queue" && (
          <Queue
            incidents={incidents}
            trackByRoom={trackByRoom}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onClaim={handleClaim}
            onOpen={openIncident}
          />
        )}
        {tab === "incident" && (
          <IncidentView
            incident={detail}
            fallDetail={fallDetail}
            contacts={contacts}
            layout={layout}
            frame={frame}
            ticker={ticker}
            trackLabel={selectedTrackLabel}
            onClaim={handleClaim}
            onResolve={handleResolve}
          />
        )}
        {tab === "home" && (
          <HomeDetail
            layout={layout}
            frame={frame}
            ticker={ticker}
            nodes={nodes}
            trends={trends}
            thresholds={thresholds}
          />
        )}
        {tab === "history" && (
          <History incidents={history} activeMinutes={activeMinutes} />
        )}
      </main>
    </div>
  );
}
