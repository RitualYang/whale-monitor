export async function fetchInitialEvents(limit = 100) {
  const res = await fetch(`/api/events?limit=${limit}`);
  if (!res.ok) {
    throw new Error("加载事件失败");
  }
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
