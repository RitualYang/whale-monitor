import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchHealth, fetchInitialEvents, setSource, subscribeEvents } from "./api";

function formatUsd(value) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value || 0);
}

function formatAmount(amount, asset) {
  return `${Number(amount || 0).toFixed(4)} ${asset}`;
}

/** Single chain source toggle pill */
function ChainToggle({ chain, source, wsConnected, onSwitch, disabled }) {
  const isWs = source === "ws";
  const wsError = isWs && !wsConnected;
  return (
    <div className={`chain-toggle${wsError ? " chain-error" : ""}`}>
      <span className="chain-label">{chain}</span>
      <div className="toggle-track">
        <button
          className={`toggle-option${!isWs ? " active" : ""}`}
          onClick={() => !disabled && isWs && onSwitch(chain, "polling")}
          disabled={disabled || !isWs}
        >
          Poll
        </button>
        <button
          className={`toggle-option${isWs ? " active" : ""}${wsError ? " ws-error" : ""}`}
          onClick={() => !disabled && !isWs && onSwitch(chain, "ws")}
          disabled={disabled || isWs}
        >
          WS
        </button>
      </div>
      {isWs && (
        <span
          className={`dot ${wsConnected ? "dot-on" : "dot-err"}`}
          title={wsConnected ? "WS connected" : "WS disconnected, consider switching to Poll"}
        />
      )}
    </div>
  );
}

export default function App() {
  const [events, setEvents] = useState([]);
  const [pushStatus, setPushStatus] = useState("connecting");
  const [error, setError] = useState("");
  const [health, setHealth] = useState({ chains: [] });
  const [switching, setSwitching] = useState(false);
  const [chainFilter, setChainFilter] = useState("all");
  const healthTimer = useRef(null);

  const refreshHealth = useCallback(() => {
    fetchHealth()
      .then((h) => setHealth(h))
      .catch(() => {});
  }, []);

  useEffect(() => {
    let mounted = true;

    fetchInitialEvents(120)
      .then((data) => { if (mounted) setEvents(data); })
      .catch((e) => { if (mounted) setError(e.message || "Init failed"); });

    const unsubscribe = subscribeEvents(
      (event) => {
        setEvents((prev) => {
          if (prev.some((x) => x.chain === event.chain && x.tx_hash === event.tx_hash)) return prev;
          return [event, ...prev].slice(0, 200);
        });
      },
      setPushStatus
    );

    refreshHealth();
    healthTimer.current = setInterval(refreshHealth, 5000);

    return () => {
      mounted = false;
      unsubscribe();
      clearInterval(healthTimer.current);
    };
  }, [refreshHealth]);

  const handleSwitch = useCallback(async (chainName, target) => {
    setSwitching(true);
    setError("");
    try {
      await setSource(chainName, target);
      setTimeout(refreshHealth, 1500);
    } catch (e) {
      setError(e.message);
    } finally {
      setSwitching(false);
    }
  }, [refreshHealth]);

  const rows = useMemo(
    () => chainFilter === "all" ? events : events.filter((e) => e.chain === chainFilter),
    [events, chainFilter],
  );

  const chainNames = useMemo(
    () => (health.chains || []).map((c) => c.name),
    [health.chains],
  );

  return (
    <div className="page">
      <header className="header">
        <h1>Whale Monitor</h1>
        <div className={`badge badge-${pushStatus}`}>Push: {pushStatus}</div>
      </header>

      <div className="chain-grid">
        {(health.chains || []).map((c) => (
          <ChainToggle
            key={c.name}
            chain={c.name}
            source={c.source}
            wsConnected={c.connected}
            onSwitch={handleSwitch}
            disabled={switching}
          />
        ))}
      </div>

      {error && <div className="error">{error}</div>}

      <div className="filter-bar">
        <button
          className={`filter-btn${chainFilter === "all" ? " active" : ""}`}
          onClick={() => setChainFilter("all")}
        >
          All
        </button>
        {chainNames.map((name) => (
          <button
            key={name}
            className={`filter-btn${chainFilter === name ? " active" : ""}`}
            onClick={() => setChainFilter(name)}
          >
            {name}
          </button>
        ))}
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time (UTC)</th>
              <th>Chain</th>
              <th>Tx Hash</th>
              <th>From</th>
              <th>To</th>
              <th>Asset / Amount</th>
              <th>Price</th>
              <th>Value (USD)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <tr key={`${item.chain}-${item.tx_hash}`}>
                <td>{new Date(item.timestamp).toISOString().replace("T", " ").slice(0, 19)}</td>
                <td>{item.chain}</td>
                <td>
                  <a href={item.explorer_url} target="_blank" rel="noreferrer">
                    {item.tx_hash.slice(0, 10)}...{item.tx_hash.slice(-8)}
                  </a>
                </td>
                <td title={item.from_address}>{item.from_address.slice(0, 10)}...{item.from_address.slice(-8)}</td>
                <td title={item.to_address}>{item.to_address.slice(0, 10)}...{item.to_address.slice(-8)}</td>
                <td>{formatAmount(item.amount, item.asset)}</td>
                <td className="price">{formatUsd(item.unit_price)}</td>
                <td className="usd">{formatUsd(item.usd_value)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="empty">
                  No whale transfers detected yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
