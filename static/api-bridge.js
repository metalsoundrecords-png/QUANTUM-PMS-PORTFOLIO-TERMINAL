(function () {
  async function snapshot() {
    const res = await fetch('/api/snapshot', { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('snapshot failed');
    return res.json();
  }

  function connect(onMessage) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(protocol + '//' + window.location.host + '/ws/live');
    ws.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data));
      } catch (_) {
        // Ignore malformed live messages; the visual terminal keeps running.
      }
    };
    return ws;
  }

  window.QuantumAPI = { snapshot, connect };
})();
