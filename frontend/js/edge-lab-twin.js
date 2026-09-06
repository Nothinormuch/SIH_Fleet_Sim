import { DigitalTwin } from './digital-twin.js';
import { frameFromSnapshot, sceneFromSnapshot, interpolateLiveFrame } from './live-twin-data.js';

export class LiveEdgeTwin {
  constructor(canvas, onSelect, onFailure) {
    this.canvas = canvas;
    this.twin = new DigitalTwin(canvas, onSelect);
    this.onFailure = onFailure;
    this.samples = [];
    this.selected = 'AMR01';
    this.camera = 'overview';
    this.visible = true;
    this.run = null;
    this.lastReceived = 0;
    this.lastPaint = 0;
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas.parentElement);
    canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      this.visible = false;
      onFailure('3D graphics context lost. Live 2D view is still available.');
    });
    this.animate = this.animate.bind(this);
    this.animation = requestAnimationFrame(this.animate);
  }

  receive(state) {
    const snapshot = state?.snapshot;
    if (!snapshot) return;
    // PIDs cover older servers without run_id and prevent cross-run interpolation.
    const run = state.run_id || snapshot.nodes.map(node => node.pid).join(':');
    if (run !== this.run) {
      this.twin.load(sceneFromSnapshot(snapshot));
      this.samples = [];
      this.run = run;
      this.twin.setCameraMode(this.camera);
    }
    const frame = frameFromSnapshot(snapshot, state.result);
    const previous = this.samples.at(-1);
    if (!previous || frame.t > previous.t) {
      this.samples.push(frame);
      this.samples = this.samples.slice(-5);
      this.lastReceived = performance.now();
    } else if (frame.t === previous.t) {
      // Final completion certificates update cargo even without another physics tick.
      this.samples[this.samples.length - 1] = frame;
    }
  }

  select(id) { this.selected = id; this.twin.setSelected(id); }
  setCamera(mode) { this.camera = mode; this.twin.setCameraMode(mode); }
  fit() {
    if (this.camera === 'tactical') this.twin.setCameraMode('tactical');
    else { this.camera = 'overview'; this.twin.setCameraMode('overview'); this.twin.frameFloor(); }
  }
  resize() {
    if (!this.visible) return;
    this.twin.resize();
    if (this.camera === 'tactical') this.twin.setCameraMode('tactical');
    else if (this.camera === 'overview') this.twin.frameFloor();
    // Resizing clears WebGL's drawing buffer. Paint immediately, even between RAFs.
    if (this.samples.length) this.twin.renderer.render(this.twin.scene, this.twin.camera);
  }
  setVisible(visible) {
    const changed = this.visible !== visible;
    this.visible = visible;
    if (changed && visible) this.resize();
  }

  animate(now) {
    this.animation = requestAnimationFrame(this.animate);
    if (!this.visible || document.hidden || !this.samples.length || now - this.lastPaint < 30) return;
    this.lastPaint = now;
    try {
      const newest = this.samples.at(-1);
      // Smooth only between received positions. Never predict motion past live evidence.
      const target = Math.min(newest.t, newest.t + (now - this.lastReceived) / 1000 - .16);
      const before = this.samples.at(-2) || newest;
      const frame = interpolateLiveFrame(before, newest, target);
      this.twin.update(frame, this.selected, this.camera, frame.t);
    } catch (error) {
      this.visible = false;
      this.onFailure(`3D display unavailable: ${error.message}. Live 2D view remains available.`);
    }
  }
}
