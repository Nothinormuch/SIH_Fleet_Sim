/* Hardware Architecture Diagram — maps software layers to physical AMR components.
 *
 * Rendered as styled HTML/CSS blocks (not canvas) so it is crisp at any zoom.
 * Each layer is clickable and highlights the corresponding step in the
 * protocol stack inspector.
 *
 * This is a static diagram that describes what a real deployment looks like.
 * It does not change per-frame; it is rebuilt once per robot selection.
 */

const HwDiagram = (() => {
  const el = id => document.getElementById(id);

  const LAYERS = [
    {
      id: 'hw-app',
      name: 'Application Software',
      component: 'BIOS 6.0 Runtime',
      hardware: 'Raspberry Pi 4B / Jetson Nano',
      detail: 'Python 3.11 · PIBT coordinator · Auction engine · Deadlock detector · Path planner',
      icon: '⚙',
      colour: '#35c6f4',
      stackLayer: 'APPLICATION',
    },
    {
      id: 'hw-socket',
      name: 'Socket Interface',
      component: 'UDP Datagram Socket',
      hardware: 'Linux kernel socket API (AF_INET, SOCK_DGRAM)',
      detail: `bind(0.0.0.0:26123) · sendto(239.26.1.23:26123) · SO_REUSEADDR · IP_ADD_MEMBERSHIP`,
      icon: '📦',
      colour: '#46d39a',
      stackLayer: 'TRANSPORT',
    },
    {
      id: 'hw-network',
      name: 'IP Networking Stack',
      component: 'IPv4 Multicast',
      hardware: 'Linux kernel: ip_forward · netfilter · igmp · route table',
      detail: 'TTL=1 · DSCP=46 (EF) · IGMP v3 membership · ip route via wlan0',
      icon: '🌐',
      colour: '#f5b843',
      stackLayer: 'NETWORK',
    },
    {
      id: 'hw-driver',
      name: 'Wi-Fi Driver',
      component: 'brcmfmac (BCM43455)',
      hardware: 'Linux kernel module: cfg80211 → mac80211 → brcmfmac',
      detail: 'nl80211 interface · WPA2-PSK · Channel 6 · HT20 · Power save OFF',
      icon: '🔗',
      colour: '#b78cff',
      stackLayer: 'DATA LINK',
    },
    {
      id: 'hw-radio',
      name: 'Wi-Fi Radio',
      component: 'BCM43455 (CYW43455)',
      hardware: 'Broadcom SoC via SDIO bus · 2.4 GHz + 5 GHz dual band',
      detail: '802.11b/g/n/ac · 1×1 SISO · 150 Mbps HT20 · Tx power: 20 dBm',
      icon: '📡',
      colour: '#ff6577',
      stackLayer: 'PHYSICAL',
    },
    {
      id: 'hw-antenna',
      name: 'Antenna',
      component: 'PCB Trace / External SMA',
      hardware: 'Omnidirectional · 3 dBi gain · Vertically polarised',
      detail: 'Integrated PCB trace antenna (Pi 4) or external 2.4 GHz whip antenna via U.FL → SMA pigtail',
      icon: '〰',
      colour: '#a3e635',
      stackLayer: 'PHYSICAL',
    },
  ];

  const BUS_LABELS = [
    'Python socket.sendto()',
    'syscall: sendmsg()',
    'ip_queue_xmit()',
    'nl80211 / SDIO',
    'SDIO / PCIe bus',
    'RF emission',
  ];

  function render(container) {
    if (!container) return;

    container.innerHTML = `
      <div class="hw-title">
        <b>AMR Edge Node Architecture</b>
        <small>Raspberry Pi 4B · BCM43455 Wi-Fi · Linux 6.1</small>
      </div>
      <div class="hw-stack">
        ${LAYERS.map((layer, i) => `
          <div class="hw-layer" id="${layer.id}" data-stack="${layer.stackLayer}"
               style="--hw-color:${layer.colour}">
            <div class="hw-layer-icon">${layer.icon}</div>
            <div class="hw-layer-content">
              <div class="hw-layer-head">
                <b>${layer.name}</b>
                <span class="hw-component">${layer.component}</span>
              </div>
              <div class="hw-layer-hw">${layer.hardware}</div>
              <div class="hw-layer-detail">${layer.detail}</div>
            </div>
          </div>
          ${i < LAYERS.length - 1 ? `
            <div class="hw-bus">
              <span class="hw-bus-arrow">↕</span>
              <small>${BUS_LABELS[i]}</small>
            </div>
          ` : ''}
        `).join('')}
      </div>
      <div class="hw-footer">
        <div class="hw-footer-row">
          <span class="hw-env-tag">WAREHOUSE</span>
          <span>Industrial Wi-Fi mesh · 2.4 GHz · Channel 6</span>
        </div>
        <div class="hw-footer-row">
          <span class="hw-env-tag">MULTICAST</span>
          <span>239.26.1.23:26123 · UDP · TTL 1</span>
        </div>
        <div class="hw-footer-row">
          <span class="hw-env-tag">PROTOCOL</span>
          <span>BIOS Wire Protocol v1 · HMAC-SHA256 · max 2048 bytes</span>
        </div>
      </div>
    `;

    // Interactivity: highlight corresponding protocol stack layer
    container.querySelectorAll('.hw-layer').forEach(layerEl => {
      layerEl.addEventListener('click', () => {
        container.querySelectorAll('.hw-layer').forEach(l => l.classList.remove('hw-active'));
        layerEl.classList.add('hw-active');
      });

      layerEl.addEventListener('mouseenter', () => {
        layerEl.classList.add('hw-hover');
      });
      layerEl.addEventListener('mouseleave', () => {
        layerEl.classList.remove('hw-hover');
      });
    });
  }

  return { render, LAYERS };
})();

window.HwDiagram = HwDiagram;
