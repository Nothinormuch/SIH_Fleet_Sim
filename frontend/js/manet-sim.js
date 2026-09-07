/* MANET Simulation Engine — frontend packet synthesis from existing telemetry.
 *
 * This module does NOT replace the real transport.py simulation. It reads the
 * telemetry the backend already produces (fleet[], peers, auction_events,
 * decisions) and synthesises a visual representation of the packet journey
 * through the full OSI-mapped protocol stack.
 *
 * Every constant here (port 26123, multicast group 239.26.1.23, MTU 2048,
 * MAC OUI b8:27:eb) matches the real values in src/transport.py and
 * src/messages.py, so the visualization is technically credible rather than
 * decorative.
 */

const ManetSim = (() => {

  /* ── Constants from the real backend ────────────────────────────────── */

  const UDP_PORT          = 26123;
  const MULTICAST_GROUP   = '239.26.1.23';
  const MULTICAST_MAC     = '01:00:5e:1a:01:17';
  const PROTOCOL_VERSION  = 1;
  const MAX_DATAGRAM      = 2048;
  const WIFI_FREQ_GHZ     = 2.437;        // channel 6
  const WIFI_CHANNEL      = 6;
  const WIFI_BW_MHZ       = 20;
  const REFERENCE_RSSI    = -30;          // dBm at 1 m
  const PATH_LOSS_EXP     = 3.0;          // indoor warehouse
  const NOISE_FLOOR_DBM   = -90;
  const TX_POWER_DBM      = 20;           // typical Pi / ESP32

  /* Raspberry Pi 3B+ Wi-Fi MAC OUI */
  const MAC_OUI = 'b8:27:eb';

  /* Message type → approximate payload bytes (from messages.py encode()) */
  const MSG_SIZES = {
    HB: 92,   IN: 186,  CL: 74,   RL: 58,   YD: 64,
    BD: 128,  AW: 112,  TD: 96,   TN: 108,  MB: 68,
    PQ: 82,   PS: 320,  EX: 156,
  };

  /* Overhead per layer */
  const UDP_HEADER   = 8;
  const IP_HEADER    = 20;
  const MAC_HEADER   = 34;    // 802.11 data frame header
  const PHY_PREAMBLE = 20;    // PLCP preamble

  /* Message type → display category */
  const MSG_CATEGORIES = {
    HB: 'telemetry', IN: 'coordination', CL: 'coordination', RL: 'coordination',
    YD: 'coordination', BD: 'auction', AW: 'auction', TD: 'auction',
    TN: 'auction', MB: 'management', PQ: 'management', PS: 'management',
    EX: 'intelligence',
  };

  /* ── State ──────────────────────────────────────────────────────────── */

  let _packets   = [];   // all synthesised packets, sorted by t
  let _meta      = null;
  let _robotIPs  = {};   // robotId → '10.0.1.xx'
  let _robotMACs = {};   // robotId → 'b8:27:eb:xx:xx:xx'
  let _deadZones = [];
  let _ready     = false;

  /* ── Deterministic MAC / IP generation ─────────────────────────────── */

  function robotIndex(id) {
    return Math.max(0, (parseInt(id.replace(/\D/g, ''), 10) || 1) - 1);
  }

  function generateIP(id) {
    const idx = robotIndex(id);
    return `10.0.1.${11 + idx}`;
  }

  function generateMAC(id) {
    const idx = robotIndex(id);
    const b4 = ((idx * 17 + 0xa1) & 0xff).toString(16).padStart(2, '0');
    const b5 = ((idx * 13 + 0x01) & 0xff).toString(16).padStart(2, '0');
    const b6 = ((idx * 7 + 0x0b) & 0xff).toString(16).padStart(2, '0');
    return `${MAC_OUI}:${b4}:${b5}:${b6}`;
  }

  /* ── Radio model (matches transport.py) ────────────────────────────── */

  function distanceMetres(r1, r2, cellM) {
    return Math.hypot((r1.x - r2.x), (r1.y - r2.y));
  }

  function rssiAtDistance(distM) {
    if (distM < 0.1) distM = 0.1;
    return REFERENCE_RSSI - 10 * PATH_LOSS_EXP * Math.log10(distM);
  }

  function snrFromRSSI(rssi) {
    return Math.max(0, rssi - NOISE_FLOOR_DBM);
  }

  function mcsFromSNR(snr) {
    if (snr > 30) return 7;
    if (snr > 25) return 6;
    if (snr > 20) return 5;
    if (snr > 15) return 4;
    if (snr > 12) return 3;
    if (snr > 9)  return 2;
    if (snr > 6)  return 1;
    return 0;
  }

  function throughputMbps(mcs) {
    // 802.11n HT20, 1 spatial stream
    const rates = [6.5, 13, 19.5, 26, 39, 52, 58.5, 65];
    return rates[Math.min(mcs, 7)] || 6.5;
  }

  function inDeadZone(x, y, cellM) {
    for (const [cx, cy, r] of _deadZones) {
      const dx = x / cellM - cx;
      const dy = y / cellM - cy;
      if (Math.hypot(dx, dy) <= r) return true;
    }
    return false;
  }

  /* ── Packet synthesis ──────────────────────────────────────────────── */

  function buildStack(type, srcId, dstId, distM, srcX, srcY, cellM) {
    const payloadBytes = MSG_SIZES[type] || 100;
    const udpLen       = payloadBytes + UDP_HEADER;
    const ipLen        = udpLen + IP_HEADER;
    const frameBytes   = ipLen + MAC_HEADER;
    const rssi         = rssiAtDistance(distM);
    const snr          = snrFromRSSI(rssi);
    const mcs          = mcsFromSNR(snr);

    return {
      app: {
        type,
        protocol_version: PROTOCOL_VERSION,
        payload_bytes: payloadBytes,
        category: MSG_CATEGORIES[type] || 'other',
      },
      udp: {
        src_port: UDP_PORT,
        dst_port: UDP_PORT,
        length: udpLen,
        checksum: '0x' + ((payloadBytes * 257 + 0xbeef) & 0xffff).toString(16),
      },
      ip: {
        version: 4,
        src: _robotIPs[srcId] || generateIP(srcId),
        dst: dstId === 'BROADCAST' ? MULTICAST_GROUP : (_robotIPs[dstId] || generateIP(dstId)),
        protocol: 17,  // UDP
        ttl: 1,
        total_length: ipLen,
        dscp: 46,      // EF (Expedited Forwarding) for real-time
      },
      mac: {
        src: _robotMACs[srcId] || generateMAC(srcId),
        dst: dstId === 'BROADCAST' ? MULTICAST_MAC : (_robotMACs[dstId] || generateMAC(dstId)),
        type: '802.11',
        frame_bytes: frameBytes,
        qos: true,
      },
      phy: {
        freq_ghz: WIFI_FREQ_GHZ,
        channel: WIFI_CHANNEL,
        bandwidth_mhz: WIFI_BW_MHZ,
        rssi_dbm: Math.round(rssi * 10) / 10,
        snr_db: Math.round(snr * 10) / 10,
        mcs,
        rate_mbps: throughputMbps(mcs),
        preamble_bytes: PHY_PREAMBLE,
      },
    };
  }

  /* Synthesise packets from telemetry state changes between frames.
   *
   * The real simulation sends messages at every tick (50 Hz) and the telemetry
   * samples at 10 Hz, so we can't know the exact transmission schedule. What
   * we CAN know:
   *   - If a robot's peers changed → it sent/received INTENT
   *   - If an auction event fired → BID/AWARD/TN/TD was sent
   *   - If a decision was made → related coordination messages were sent
   *   - Every robot sends HEARTBEAT periodically
   *
   * We synthesise a curated selection, not every heartbeat. */

  function synthesisePackets(frames, meta) {
    const packets = [];
    let pktId = 0;
    const cellM = meta.cell_m || 0.4;

    const posAt = (frame, id) => {
      const r = (frame.robots || []).find(r => r.id === id);
      return r || null;
    };

    for (let i = 1; i < frames.length; i++) {
      const prev = frames[i - 1];
      const curr = frames[i];
      const t = curr.t;
      const prevFleet = new Map((prev.fleet || []).map(f => [f.id, f]));
      const currFleet = new Map((curr.fleet || []).map(f => [f.id, f]));

      for (const [id, info] of currFleet) {
        const prevInfo = prevFleet.get(id) || {};
        const robot = posAt(curr, id);
        if (!robot) continue;

        /* INTENT — path changed */
        const prevPath = JSON.stringify(prevInfo.path || []);
        const currPath = JSON.stringify(info.path || []);
        if (currPath !== prevPath && (info.path || []).length > 0) {
          for (const peerId of (info.peers || [])) {
            const peer = posAt(curr, peerId);
            if (!peer) continue;
            const dist = distanceMetres(robot, peer, cellM);
            const dead = inDeadZone(robot.x, robot.y, cellM);
            const peerDead = inDeadZone(peer.x, peer.y, cellM);
            packets.push({
              id: `pkt_${pktId++}`,
              t,
              src: id,
              dst: peerId,
              type: 'IN',
              delivered: !dead && !peerDead,
              latency_ms: 1.5 + Math.random() * 3,
              in_dead_zone: dead || peerDead,
              ...buildStack('IN', id, peerId, dist, robot.x, robot.y, cellM),
            });
          }
        }

        /* CLAIM — blocked_on changed */
        if (info.blocked_on && info.blocked_on !== prevInfo.blocked_on && info.blocked_on !== 'gate') {
          const peer = posAt(curr, info.blocked_on);
          if (peer) {
            const dist = distanceMetres(robot, peer, cellM);
            packets.push({
              id: `pkt_${pktId++}`,
              t,
              src: id,
              dst: info.blocked_on,
              type: 'CL',
              delivered: true,
              latency_ms: 1.0 + Math.random() * 2,
              in_dead_zone: false,
              ...buildStack('CL', id, info.blocked_on, dist, robot.x, robot.y, cellM),
            });
          }
        }

        /* YIELD — was blocked, now not */
        if (prevInfo.blocked_on && !info.blocked_on && prevInfo.blocked_on !== 'gate') {
          const peer = posAt(curr, prevInfo.blocked_on);
          if (peer) {
            const dist = distanceMetres(robot, peer, cellM);
            packets.push({
              id: `pkt_${pktId++}`,
              t,
              src: id,
              dst: prevInfo.blocked_on,
              type: 'YD',
              delivered: true,
              latency_ms: 1.0 + Math.random() * 2,
              in_dead_zone: false,
              ...buildStack('YD', id, prevInfo.blocked_on, dist, robot.x, robot.y, cellM),
            });
          }
        }

        /* HEARTBEAT — sample one per robot every ~1s of sim time */
        if (Math.floor(t) !== Math.floor(prev.t)) {
          for (const peerId of (info.peers || []).slice(0, 2)) {
            const peer = posAt(curr, peerId);
            if (!peer) continue;
            const dist = distanceMetres(robot, peer, cellM);
            packets.push({
              id: `pkt_${pktId++}`,
              t,
              src: id,
              dst: peerId,
              type: 'HB',
              delivered: true,
              latency_ms: 0.8 + Math.random() * 1.5,
              in_dead_zone: false,
              ...buildStack('HB', id, peerId, dist, robot.x, robot.y, cellM),
            });
          }
        }
      }

      /* Auction events — the telemetry already carries them */
      for (const evt of (curr.auction_events || [])) {
        if (Math.abs(evt.t - t) > 0.2) continue;
        const src = evt.src || evt.winner || evt.dst || 'WMS';
        const srcRobot = posAt(curr, src);
        if (!srcRobot) continue;

        const msgType = evt.type; // TN, BD, AW, TD
        if (!MSG_SIZES[msgType]) continue;

        // Broadcast to all peers
        const info = currFleet.get(src) || {};
        for (const peerId of (info.peers || [])) {
          const peer = posAt(curr, peerId);
          if (!peer) continue;
          const dist = distanceMetres(srcRobot, peer, cellM);
          packets.push({
            id: `pkt_${pktId++}`,
            t: evt.t,
            src,
            dst: peerId,
            type: msgType,
            delivered: true,
            latency_ms: 1.2 + Math.random() * 2.5,
            in_dead_zone: false,
            ...buildStack(msgType, src, peerId, dist, srcRobot.x, srcRobot.y, cellM),
          });
        }
      }
    }

    packets.sort((a, b) => a.t - b.t);
    return packets;
  }

  /* ── Public API ─────────────────────────────────────────────────────── */

  function init(payload) {
    _meta = payload.meta;
    _deadZones = _meta.dead_zones || [];
    _robotIPs = {};
    _robotMACs = {};

    const firstFrame = payload.frames[0];
    if (firstFrame) {
      for (const r of firstFrame.robots) {
        _robotIPs[r.id] = generateIP(r.id);
        _robotMACs[r.id] = generateMAC(r.id);
      }
    }

    _packets = synthesisePackets(payload.frames, _meta);
    _ready = true;
  }

  /** Packets active (in-flight or recently delivered) at simulation time t */
  function packetsAt(t, windowS = 0.5) {
    if (!_ready) return [];
    const lo = t - windowS;
    // Binary search for start
    let start = 0, end = _packets.length;
    while (start < end) {
      const mid = (start + end) >> 1;
      if (_packets[mid].t < lo) start = mid + 1;
      else end = mid;
    }
    const result = [];
    for (let i = start; i < _packets.length && _packets[i].t <= t; i++) {
      result.push(_packets[i]);
    }
    return result;
  }

  /** Aggregate network statistics up to time t */
  function statsAt(t) {
    if (!_ready) return null;
    let sent = 0, delivered = 0, dropped = 0, totalBytes = 0, totalLatency = 0;
    const typeCounts = {};
    for (const pkt of _packets) {
      if (pkt.t > t) break;
      sent++;
      if (pkt.delivered) {
        delivered++;
        totalLatency += pkt.latency_ms;
      } else {
        dropped++;
      }
      totalBytes += pkt.mac?.frame_bytes || 0;
      typeCounts[pkt.type] = (typeCounts[pkt.type] || 0) + 1;
    }
    return {
      sent, delivered, dropped,
      loss_pct: sent > 0 ? (dropped / sent * 100) : 0,
      total_bytes: totalBytes,
      avg_latency_ms: delivered > 0 ? totalLatency / delivered : 0,
      throughput_bps: t > 0 ? (totalBytes * 8 / t) : 0,
      type_counts: typeCounts,
    };
  }

  /** Full protocol stack breakdown for a packet */
  function stackForPacket(pkt) {
    return {
      layers: [
        { name: 'APPLICATION', label: 'BIOS Coordination', detail: pkt.app },
        { name: 'TRANSPORT',   label: 'UDP',               detail: pkt.udp },
        { name: 'NETWORK',     label: 'IPv4 Multicast',    detail: pkt.ip },
        { name: 'DATA LINK',   label: 'IEEE 802.11',       detail: pkt.mac },
        { name: 'PHYSICAL',    label: 'Wi-Fi Radio',       detail: pkt.phy },
      ],
    };
  }

  /** Link metrics between two robots in a given frame */
  function linkMetrics(srcId, dstId, frame) {
    const src = (frame.robots || []).find(r => r.id === srcId);
    const dst = (frame.robots || []).find(r => r.id === dstId);
    if (!src || !dst) return null;
    const dist = distanceMetres(src, dst, _meta?.cell_m || 0.4);
    const rssi = rssiAtDistance(dist);
    const snr  = snrFromRSSI(rssi);
    const mcs  = mcsFromSNR(snr);
    return {
      distance_m: Math.round(dist * 100) / 100,
      rssi_dbm: Math.round(rssi * 10) / 10,
      snr_db: Math.round(snr * 10) / 10,
      mcs,
      rate_mbps: throughputMbps(mcs),
      quality: rssi > -50 ? 'excellent' : rssi > -65 ? 'good' : rssi > -75 ? 'fair' : 'poor',
      src_in_dead_zone: inDeadZone(src.x, src.y, _meta?.cell_m || 0.4),
      dst_in_dead_zone: inDeadZone(dst.x, dst.y, _meta?.cell_m || 0.4),
    };
  }

  /** IP and MAC addresses for a robot */
  function nodeInfo(robotId) {
    return {
      ip: _robotIPs[robotId] || generateIP(robotId),
      mac: _robotMACs[robotId] || generateMAC(robotId),
      hostname: `amr-${robotId.toLowerCase().replace(/[^a-z0-9]/g, '')}`,
      os: 'Raspberry Pi OS (Debian 12)',
      kernel: 'Linux 6.1.21-v8+ aarch64',
      wifi_driver: 'brcmfmac (BCM43455)',
      wifi_interface: 'wlan0',
    };
  }

  function isReady() { return _ready; }
  function allPackets() { return _packets; }

  function dispose() {
    _packets = [];
    _meta = null;
    _robotIPs = {};
    _robotMACs = {};
    _deadZones = [];
    _ready = false;
  }

  return {
    init, dispose, isReady,
    packetsAt, statsAt, stackForPacket,
    linkMetrics, nodeInfo, allPackets,
    // Expose for the overlay
    rssiAtDistance, snrFromRSSI, mcsFromSNR, throughputMbps,
    MULTICAST_GROUP, UDP_PORT, MULTICAST_MAC,
  };
})();

window.ManetSim = ManetSim;
