import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchHealth, fetchInitialEvents, setSource, subscribeEvents } from "./api";

function formatUsd(value) {
  return new Intl.NumberFormat("zh-CN", {
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
  return (
    <div className="chain-toggle">
      <span className="chain-label">{chain}</span>
      <div className="toggle-track">
        <button
          className={`toggle-option${!isWs ? " active" : ""}`}
          onClick={() => !disabled && isWs && onSwitch(chain, "polling")}
          disabled={disabled || !isWs}
        >
          轮询
        </button>
        <button
          className={`toggle-option${isWs ? " active" : ""}`}
          onClick={() => !disabled && !isWs && onSwitch(chain, "ws")}
          disabled={disabled || isWs}
        >
          WS
        </button>
      </div>
      {isWs && (
        <span
          className={`dot ${wsConnected ? "dot-on" : "dot-off"}`}
          title={wsConnected ? "WS 已连接" : "WS 连接中…"}
        />
      )}
    </div>
  );
}

export default function App() {
  const [events, setEvents] = useState([]);
  const [pushStatus, setPushStatus] = useState("connecting");
  const [error, setError] = useState("");
  const [health, setHealth] = useState({
    eth_source: "ws",
    eth_ws_connected: false,
    sol_source: "ws",
    sol_ws_connected: false,
  });
  const [switching, setSwitching] = useState(false);
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
      .catch((e) => { if (mounted) setError(e.message || "初始化失败"); });

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

  const handleSwitch = useCallback(async (chain, target) => {
    setSwitching(true);
    setError("");
    try {
      const patch = chain === "ETH"
        ? { eth_source: target }
        : { sol_source: target };
      await setSource(patch);
      setHealth((prev) => ({
        ...prev,
        ...(chain === "ETH"
          ? { eth_source: target, eth_ws_connected: false }
          : { sol_source: target, sol_ws_connected: false }),
      }));
      setTimeout(refreshHealth, 1500);
    } catch (e) {
      setError(e.message);
    } finally {
      setSwitching(false);
    }
  }, [refreshHealth]);

  const rows = useMemo(() => events, [events]);

  return (
    <div className="page">
      <header className="header">
        <h1>多链大额转账监控</h1>
        <div className="header-right">
          <div className="source-panel">
            <ChainToggle
              chain="ETH"
              source={health.eth_source}
              wsConnected={health.eth_ws_connected}
              onSwitch={handleSwitch}
              disabled={switching}
            />
            <div className="divider" />
            <ChainToggle
              chain="SOL"
              source={health.sol_source}
              wsConnected={health.sol_ws_connected}
              onSwitch={handleSwitch}
              disabled={switching}
            />
          </div>
          <div className={`badge badge-${pushStatus}`}>推送: {pushStatus}</div>
        </div>
      </header>
      {error && <div className="error">{error}</div>}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>时间(UTC)</th>
              <th>链</th>
              <th>交易哈希</th>
              <th>From</th>
              <th>To</th>
              <th>资产/数量</th>
              <th>估值(USD)</th>
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
                <td className="usd">{formatUsd(item.usd_value)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="empty">
                  暂无满足阈值的大额转账事件
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
