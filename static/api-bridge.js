(function () {
  async function snapshot() {
    const res = await fetch('/api/snapshot', { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('snapshot failed');
    return res.json();
  }

  async function pnl() {
    const res = await fetch('/api/pnl', { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('pnl failed');
    return res.json();
  }

  async function events(params = {}) {
    const qs = new URLSearchParams();
    if (params.asset) qs.set('asset', params.asset);
    if (params.search) qs.set('search', params.search);
    if (params.limit) qs.set('limit', params.limit);
    if (params.offset) qs.set('offset', params.offset);
    const res = await fetch('/api/events?' + qs.toString(), { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('events failed');
    return res.json();
  }

  async function getSettings() {
    const res = await fetch('/api/settings', { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('settings failed');
    return res.json();
  }

  async function saveSettings(payload) {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error('save settings failed');
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

  window.QuantumAPI = { snapshot, connect, pnl, events, getSettings, saveSettings };
})();
