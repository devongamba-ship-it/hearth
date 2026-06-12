// Hearth caregiver dashboard (BAT-25).
//
// The "Mom is okay" app for an adult child on her phone — warm, high-contrast,
// large type, mobile-first. Privacy is the brand: coarse status and activity
// blocks only, never cameras, microphones, or per-second sensor data.
//
// Five screens: Home (status card), Activity, Alerts (incident replay), Care
// circle, Settings. The Home card polls the ingest /api/status and the
// escalation /api/incidents/active every few seconds and composes them.

import { useCallback, useEffect, useState } from "react";
import {
  cancelQuiet,
  claimIncident,
  getActiveIncidents,
  getQuietStatus,
  getStatus,
  startQuiet,
} from "./api.js";
import StatusCard from "./components/StatusCard.jsx";
import QuietControl from "./components/QuietControl.jsx";
import ActivityScreen from "./components/ActivityScreen.jsx";
import AlertsScreen from "./components/AlertsScreen.jsx";
import CareCircle from "./components/CareCircle.jsx";
import Settings from "./components/Settings.jsx";

const POLL_MS = 4000;
const HOME_ID = import.meta.env.VITE_HOME_ID || "home";

const TABS = [
  { key: "home", label: "Home" },
  { key: "activity", label: "Activity" },
  { key: "alerts", label: "Alerts" },
  { key: "circle", label: "Care circle" },
  { key: "settings", label: "Settings" },
];

function useStatusPolling() {
  const [status, setStatus] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [quiet, setQuiet] = useState({ active: false, remaining_seconds: 0 });
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [s, inc, q] = await Promise.all([
        getStatus(),
        getActiveIncidents(),
        getQuietStatus(),
      ]);
      setStatus(s);
      setIncidents(inc.incidents || []);
      setQuiet(q);
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

  return { status, incidents, quiet, error, refresh };
}

export default function App() {
  const [tab, setTab] = useState("home");
  const { status, incidents, quiet, error, refresh } = useStatusPolling();

  const handleClaim = useCallback(
    async (incidentId) => {
      await claimIncident(incidentId, "You");
      await refresh();
    },
    [refresh],
  );

  const handleStartQuiet = useCallback(
    async (hours) => {
      await startQuiet({ hours, actor: "You" });
      await refresh();
    },
    [refresh],
  );

  const handleCancelQuiet = useCallback(async () => {
    await cancelQuiet("You");
    await refresh();
  }, [refresh]);

  return (
    <div className="app">
      <header className="app__header">
        <h1 className="app__brand">Hearth</h1>
        {status?.home_id && <p className="app__home">{status.home_id}</p>}
      </header>

      <main className="app__main">
        {error && tab === "home" && (
          <p className="banner" role="status">Reconnecting to the home… ({error})</p>
        )}
        {tab === "home" && (
          <>
            <StatusCard status={status} activeIncidents={incidents} onClaim={handleClaim} />
            <QuietControl
              quiet={quiet}
              onStart={handleStartQuiet}
              onCancel={handleCancelQuiet}
            />
          </>
        )}
        {tab === "activity" && <ActivityScreen homeId={HOME_ID} />}
        {tab === "alerts" && <AlertsScreen />}
        {tab === "circle" && <CareCircle />}
        {tab === "settings" && <Settings />}
      </main>

      <nav className="app__tabs" aria-label="Sections">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className="app__tab"
            aria-current={tab === t.key ? "page" : undefined}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>
    </div>
  );
}
