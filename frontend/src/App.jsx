import { useEffect, useMemo, useState } from "react";
import { fetchInitialEvents, subscribeEvents } from "./api";

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

export default function App() {
  const [events, setEvents] = useState([]);
  const [status, setStatus] = useState("connecting");
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;

    fetchInitialEvents(120)
      .then((data) => {
        if (mounted) setEvents(data);
      })
      .catch((e) => {
        if (mounted) setError(e.message || "初始化失败");
      });

    const unsubscribe = subscribeEvents(
      (event) => {
        setEvents((prev) => {
          if (prev.some((x) => x.chain === event.chain && x.tx_hash === event.tx_hash)) {
            return prev;
          }
          return [event, ...prev].slice(0, 200);
        });
      },
      setStatus
    );

    return () => {
      mounted = false;
      unsubscribe();
    };
  }, []);

  const rows = useMemo(() => events, [events]);

  return (
    <div className="page">
      <header className="header">
        <h1>多链大额转账监控</h1>
        <div className={`badge badge-${status}`}>WS: {status}</div>
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
