export async function fetchInitialEvents(limit = 100) {
  const res = await fetch(`/api/events?limit=${limit}`);
  if (!res.ok) throw new Error("Failed to load events");
  return res.json();
}

export async function fetchHealth() {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error("health check failed");
  return res.json();
}

/**
 * Switch a chain's data source.
 * @param {string} chain - chain name, e.g. "ethereum"
 * @param {string} source - "ws" or "polling"
 */
export async function setSource(chain, source) {
  const res = await fetch("/api/source", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chain, source }),
  });
  if (!res.ok) throw new Error("Failed to switch source");
  return res.json();
}

export function subscribeEvents(onEvent, onState) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${location.host}/ws/events`);
  ws.onopen = () => onState?.("connected");
  ws.onclose = () => onState?.("disconnected");
  ws.onerror = () => onState?.("error");
  ws.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch {
      // ignore invalid message
    }
  };

  const ping = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) ws.send("ping");
  }, 15000);

  return () => {
    clearInterval(ping);
    ws.close();
  };
}
