/* Comms Panel — protocol stack inspector, link metrics, packet feed.
 *
 * Follows the same IIFE-with-init/render/dispose pattern as hud.js so main.js
 * treats it identically: init on run, render every frame, dispose on teardown.
 *
 * This panel shows:
 *   1. The 5-layer OSI-mapped protocol stack for the most recent packet
 *   2. Per-link radio metrics (RSSI, SNR, MCS, throughput)
 *   3. Hardware architecture mapping (software → physical component)
 *   4. A scrolling feed of recent packets through the selected link
 *   5. Aggregate network statistics
 */

const CommsPanel = (() => {
  const el = id => document.getElementById(id);

  const state = {
    ready: false,
    meta: null,
    selectedLink: null,    // { src, dst } or null
    selectedPacket: null,
    lastRenderKey: '',
  };

  const nodes = {};

  function cache() {
    for (const key of [
      'commsStack', 'commsLinkMetrics', 'commsPacketFeed',
      'commsNetStats', 'commsHwDiagram', 'commsNodeInfo',
    ]) {
      nodes[key] = el(key);
    }
  }

  /* ── Protocol Stack Rendering ──────────────────────────────────────── */

  const LAYER_COLOURS = {
    APPLICATION: '#35c6f4',
    TRANSPORT:   '#46d39a',
    NETWORK:     '#f5b843',
    'DATA LINK': '#b78cff',
    PHYSICAL:    '#ff6577',
  };

  const LAYER_ICONS = {
    APPLICATION: '⚙',
    TRANSPORT:   '📦',
    NETWORK:     '🌐',
    'DATA LINK': '🔗',
    PHYSICAL:    '📡',
  };

  const TYPE_LABELS = {
    HB: 'HEARTBEAT',   IN: 'INTENT',      CL: 'CLAIM',
    RL: 'RELEASE',     YD: 'YIELD',       BD: 'BID',
    AW: 'AWARD',       TD: 'TASK DONE',   TN: 'TASK NEW',
    MB: 'MGR BEACON',  PQ: 'PLAN REQ',    PS: 'PLAN RSP',
    EX: 'EXPERIENCE',
  };

  const TYPE_COLOURS = {
    HB: '#35c6f4', IN: '#46d39a', CL: '#b78cff', RL: '#6f8498',
    YD: '#ff6577', BD: '#f5b843', AW: '#f5b843', TD: '#46d39a',
    TN: '#67e8f9', MB: '#60a5fa', PQ: '#60a5fa', PS: '#60a5fa',
    EX: '#a3e635',
  };

  function renderStack(pkt) {
    const host = nodes.commsStack;
    if (!host) return;
    if (!pkt) {
      host.innerHTML = '<p class="comms-muted">Select a robot or wait for a packet to inspect the protocol stack.</p>';
      return;
    }

    const stack = ManetSim.stackForPacket(pkt);
    const layers = stack.layers;

    host.innerHTML = `
      <div class="stack-header">
        <span class="stack-type" style="background:${TYPE_COLOURS[pkt.type] || '#35c6f4'}">${TYPE_LABELS[pkt.type] || pkt.type}</span>
        <span class="stack-route">${esc(pkt.src)} → ${esc(pkt.dst)}</span>
        <span class="stack-time">${pkt.t.toFixed(2)}s</span>
      </div>
      <div class="stack-layers">
        ${layers.map(layer => `
          <div class="stack-layer" style="--layer-color:${LAYER_COLOURS[layer.name] || '#35c6f4'}">
            <div class="stack-layer-head">
              <span class="stack-layer-icon">${LAYER_ICONS[layer.name] || '•'}</span>
              <b>${layer.name}</b>
              <small>${layer.label}</small>
            </div>
            <div class="stack-layer-body">
              ${renderLayerDetail(layer)}
            </div>
          </div>
        `).join('<div class="stack-arrow">▼</div>')}
      </div>
      <div class="stack-outcome ${pkt.delivered ? 'delivered' : 'dropped'}">
        <b>${pkt.delivered ? '✓ DELIVERED' : '✗ DROPPED'}</b>
        ${pkt.delivered ? `<span>Latency: ${pkt.latency_ms.toFixed(1)} ms</span>` : '<span>Dead zone absorption</span>'}
        ${pkt.in_dead_zone ? '<span class="dead-zone-flag">⚠ DEAD ZONE</span>' : ''}
      </div>
    `;
  }

  function renderLayerDetail(layer) {
    const d = layer.detail;
    if (!d) return '';

    if (layer.name === 'APPLICATION') {
      return `<span>Type: <b>${TYPE_LABELS[d.type] || d.type}</b></span>
              <span>Payload: <b>${d.payload_bytes} bytes</b></span>
              <span>Protocol v${d.protocol_version}</span>
              <span>Category: ${d.category}</span>`;
    }
    if (layer.name === 'TRANSPORT') {
      return `<span>Port: <b>${d.src_port} → ${d.dst_port}</b></span>
              <span>Length: <b>${d.length} bytes</b></span>
              <span>Checksum: ${d.checksum}</span>`;
    }
    if (layer.name === 'NETWORK') {
      return `<span>Src: <b>${d.src}</b></span>
              <span>Dst: <b>${d.dst}</b></span>
              <span>Protocol: UDP (${d.protocol})</span>
              <span>TTL: ${d.ttl} · DSCP: ${d.dscp} (EF)</span>
              <span>Total: ${d.total_length} bytes</span>`;
    }
    if (layer.name === 'DATA LINK') {
      return `<span>Src MAC: <b>${d.src}</b></span>
              <span>Dst MAC: <b>${d.dst}</b></span>
              <span>Frame: <b>${d.frame_bytes} bytes</b></span>
              <span>Type: ${d.type}${d.qos ? ' · QoS' : ''}</span>`;
    }
    if (layer.name === 'PHYSICAL') {
      return `<span>Freq: <b>${d.freq_ghz} GHz</b> · Ch ${d.channel}</span>
              <span>RSSI: <b>${d.rssi_dbm} dBm</b> · SNR: ${d.snr_db} dB</span>
              <span>MCS: ${d.mcs} · Rate: ${d.rate_mbps} Mbps</span>
              <span>BW: ${d.bandwidth_mhz} MHz</span>`;
    }
    return '';
  }

  /* ── Link Metrics ──────────────────────────────────────────────────── */

  function renderLinkMetrics(frame, selectedId) {
    const host = nodes.commsLinkMetrics;
    if (!host || !selectedId) return;

    const fleet = frame.fleet || [];
    const info = fleet.find(f => f.id === selectedId);
    if (!info) return;

    const peers = info.peers || [];
    if (!peers.length) {
      host.innerHTML = '<p class="comms-muted">No active peer links.</p>';
      return;
    }

    const rows = peers.map(peerId => {
      const metrics = ManetSim.linkMetrics(selectedId, peerId, frame);
      if (!metrics) return '';
      const qualityCls = metrics.quality;
      const rssiPct = Math.max(0, Math.min(100, ((metrics.rssi_dbm + 90) / 60) * 100));
      return `
        <div class="link-row quality-${qualityCls}" data-peer="${esc(peerId)}">
          <div class="link-peer"><b>${esc(peerId)}</b><small>${metrics.distance_m} m</small></div>
          <div class="link-rssi">
            <div class="rssi-bar"><i style="width:${rssiPct}%"></i></div>
            <span>${metrics.rssi_dbm} dBm</span>
          </div>
          <div class="link-detail">
            <span>SNR ${metrics.snr_db} dB</span>
            <span>MCS ${metrics.mcs}</span>
            <span>${metrics.rate_mbps} Mbps</span>
          </div>
          ${metrics.src_in_dead_zone || metrics.dst_in_dead_zone ? '<span class="dead-zone-flag">⚠ DEAD ZONE</span>' : ''}
        </div>`;
    }).filter(Boolean);

    host.innerHTML = rows.length
      ? `<div class="link-grid">${rows.join('')}</div>`
      : '<p class="comms-muted">No link metrics available.</p>';
  }

  /* ── Packet Feed ───────────────────────────────────────────────────── */

  function renderPacketFeed(t, selectedId) {
    const host = nodes.commsPacketFeed;
    if (!host) return;

    const packets = ManetSim.packetsAt(t, 2.0);
    const relevant = selectedId
      ? packets.filter(p => p.src === selectedId || p.dst === selectedId)
      : packets;

    const recent = relevant.slice(-10).reverse();
    const key = recent.map(p => p.id).join('|');
    if (key === state.lastRenderKey) return;
    state.lastRenderKey = key;

    if (!recent.length) {
      host.innerHTML = '<p class="comms-muted">No recent packets.</p>';
      return;
    }

    host.innerHTML = recent.map(pkt => `
      <div class="pkt-row ${pkt.delivered ? '' : 'dropped'}">
        <span class="pkt-time">${pkt.t.toFixed(1)}s</span>
        <span class="pkt-type" style="background:${TYPE_COLOURS[pkt.type] || '#35c6f4'}">${pkt.type}</span>
        <span class="pkt-route">${esc(pkt.src)} → ${esc(pkt.dst)}</span>
        <span class="pkt-size">${pkt.mac?.frame_bytes || '?'} B</span>
        <span class="pkt-latency">${pkt.delivered ? pkt.latency_ms.toFixed(1) + ' ms' : 'LOST'}</span>
      </div>
    `).join('');
  }

  /* ── Network Stats ─────────────────────────────────────────────────── */

  function renderNetStats(t) {
    const host = nodes.commsNetStats;
    if (!host) return;

    const stats = ManetSim.statsAt(t);
    if (!stats) return;

    const typeBars = Object.entries(stats.type_counts || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([type, count]) => `
        <div class="stat-type-row">
          <span class="pkt-type" style="background:${TYPE_COLOURS[type] || '#35c6f4'}">${type}</span>
          <span>${count}</span>
        </div>`).join('');

    host.innerHTML = `
      <div class="net-stats-grid">
        <div class="net-stat"><span>Packets sent</span><b>${stats.sent}</b></div>
        <div class="net-stat"><span>Delivered</span><b class="good">${stats.delivered}</b></div>
        <div class="net-stat"><span>Dropped</span><b class="${stats.dropped > 0 ? 'bad' : ''}">${stats.dropped}</b></div>
        <div class="net-stat"><span>Loss rate</span><b class="${stats.loss_pct > 5 ? 'bad' : ''}">${stats.loss_pct.toFixed(1)}%</b></div>
        <div class="net-stat"><span>Avg latency</span><b>${stats.avg_latency_ms.toFixed(1)} ms</b></div>
        <div class="net-stat"><span>Total bytes</span><b>${formatBytes(stats.total_bytes)}</b></div>
        <div class="net-stat"><span>Throughput</span><b>${formatBps(stats.throughput_bps)}</b></div>
      </div>
      ${typeBars ? `<div class="net-stats-types"><small>By type</small>${typeBars}</div>` : ''}
    `;
  }

  /* ── Node Info ──────────────────────────────────────────────────────── */

  function renderNodeInfo(selectedId) {
    const host = nodes.commsNodeInfo;
    if (!host || !selectedId) {
      if (host) host.innerHTML = '<p class="comms-muted">Select a robot to see node information.</p>';
      return;
    }

    const info = ManetSim.nodeInfo(selectedId);
    host.innerHTML = `
      <div class="node-info-grid">
        <div class="node-field"><span>Hostname</span><b>${esc(info.hostname)}</b></div>
        <div class="node-field"><span>IP Address</span><b>${esc(info.ip)}</b></div>
        <div class="node-field"><span>MAC Address</span><b>${esc(info.mac)}</b></div>
        <div class="node-field"><span>OS</span><b>${esc(info.os)}</b></div>
        <div class="node-field"><span>Kernel</span><b>${esc(info.kernel)}</b></div>
        <div class="node-field"><span>Wi-Fi Driver</span><b>${esc(info.wifi_driver)}</b></div>
        <div class="node-field"><span>Interface</span><b>${esc(info.wifi_interface)}</b></div>
        <div class="node-field"><span>Multicast Group</span><b>${ManetSim.MULTICAST_GROUP}:${ManetSim.UDP_PORT}</b></div>
      </div>
    `;
  }

  /* ── Helpers ────────────────────────────────────────────────────────── */

  function esc(v) {
    return String(v ?? '').replace(/[&<>"']/g, c =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function formatBytes(b) {
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
    return (b / 1048576).toFixed(1) + ' MB';
  }

  function formatBps(bps) {
    if (bps < 1000) return bps.toFixed(0) + ' bps';
    if (bps < 1000000) return (bps / 1000).toFixed(1) + ' Kbps';
    return (bps / 1000000).toFixed(1) + ' Mbps';
  }

  /* ── Public API ─────────────────────────────────────────────────────── */

  function init() {
    cache();
    state.ready = true;
    state.lastRenderKey = '';
    state.selectedPacket = null;
  }

  function render(frame, meta, simTime, selectedId) {
    if (!state.ready || !ManetSim.isReady()) return;

    // Find the most recent packet involving the selected robot
    const packets = ManetSim.packetsAt(simTime, 1.0);
    const relevant = selectedId
      ? packets.filter(p => p.src === selectedId || p.dst === selectedId)
      : packets;
    const latest = relevant[relevant.length - 1] || null;

    renderStack(latest);
    renderLinkMetrics(frame, selectedId);
    renderPacketFeed(simTime, selectedId);
    renderNetStats(simTime);
    renderNodeInfo(selectedId);
  }

  function dispose() {
    state.ready = false;
    state.lastRenderKey = '';
    state.selectedPacket = null;
    for (const key in nodes) {
      if (nodes[key]) nodes[key].innerHTML = '';
    }
  }

  return { init, render, dispose };
})();

window.CommsPanel = CommsPanel;
