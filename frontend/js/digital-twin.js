import * as THREE from '../vendor/three/three.module.min.js';
import { OrbitControls } from '../vendor/three/addons/controls/OrbitControls.js';

const PALETTE = {
  cyan: 0x35c6f4,
  green: 0x46d39a,
  amber: 0xf5b843,
  violet: 0xb78cff,
  rose: 0xff6577,
  navy: 0x071019,
  floor: 0x111b26,
  rack: 0x33485b,
  steel: 0x6f8498,
};

const ROBOT_COLOURS = [PALETTE.cyan, PALETTE.green, PALETTE.amber, PALETTE.violet,
  0xff7f50, 0x67e8f9, 0xa3e635, 0xfb7185, 0x60a5fa, 0xf0abfc];

function disposeObject(root) {
  root.traverse(obj => {
    if (obj.geometry) obj.geometry.dispose();
    if (obj.material) {
      const materials = Array.isArray(obj.material) ? obj.material : [obj.material];
      for (const material of materials) {
        if (material.map) material.map.dispose();
        material.dispose();
      }
    }
  });
}

function makeLabel(text, colour = '#59cbf6', compact = false) {
  const canvas = document.createElement('canvas');
  canvas.width = compact ? 256 : 512;
  canvas.height = compact ? 72 : 96;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = 'rgba(7, 18, 27, .92)';
  ctx.strokeStyle = colour;
  ctx.lineWidth = 2;
  const radius = 0;
  ctx.beginPath();
  ctx.roundRect(3, 3, canvas.width - 6, canvas.height - 6, radius);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = '#eaf6ff';
  ctx.font = `${compact ? 28 : 32}px Georgia, 'Times New Roman', serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, canvas.width / 2, canvas.height / 2 + 1);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({map: texture, transparent: true, depthTest: false}));
  sprite.scale.set(compact ? 2.5 : 4.2, compact ? 0.7 : 0.8, 1);
  sprite.renderOrder = 100;
  return sprite;
}

function hexCss(value) {
  return `#${value.toString(16).padStart(6, '0')}`;
}

/* Textures for the twin.
 *
 * One loader so every map gets the same treatment: SRGB colour space (a texture
 * loaded as linear reads washed out under ACES tone mapping), and anisotropy so
 * the floor does not turn to mush at grazing angles - which is most of the frame
 * when the camera sits at 45 degrees.
 *
 * These are baked from the render drop, not the renders themselves: the sources
 * are 7 MB presentation shots with plinths and labels in them. See
 * tools/bake_twin_textures.py for how each one was cut.
 */
const TEXTURE_LOADER = new THREE.TextureLoader();
const TEXTURES = {};

function texture(name, {repeat = null, srgb = true} = {}) {
  const key = `${name}|${repeat ? repeat.join('x') : '1'}`;
  if (TEXTURES[key]) return TEXTURES[key];
  const map = TEXTURE_LOADER.load(`/assets/twin/${name}`);
  if (srgb) map.colorSpace = THREE.SRGBColorSpace;
  if (repeat) {
    map.wrapS = map.wrapT = THREE.RepeatWrapping;
    map.repeat.set(repeat[0], repeat[1]);
  }
  map.anisotropy = 8;
  TEXTURES[key] = map;
  return map;
}

function pedestrianEnvelope(map, meta) {
  const cell = meta.cell_m;
  const enabled = Boolean(map && map.pedestrian_apron);
  const offset = enabled
    ? Number(map.pedestrian_apron_offset_m || cell * 2.5) : 0;
  const laneWidth = enabled
    ? Number(map.pedestrian_apron_width_m || cell * .86) : 0;
  // Include the whole lane and a small visual breathing margin.  Camera fitting that
  // stops at the AMR map boundary makes an outside worker project directly over a
  // robot even though their physics positions are metres apart.
  const margin = enabled ? offset + laneWidth / 2 + cell * .34 : .6;
  return {enabled, offset, laneWidth, margin};
}

export class DigitalTwin {
  constructor(canvas, onSelect) {
    this.canvas = canvas;
    this.onSelect = onSelect;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x071019);
    this.scene.fog = new THREE.FogExp2(0x071019, 0.0085);
    this.camera = new THREE.PerspectiveCamera(43, 1, 0.1, 1000);
    this.camera.position.set(18, 25, 24);
    this.renderer = new THREE.WebGLRenderer({canvas, antialias: true, powerPreference: 'high-performance'});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    // A little hotter than the pre-rework 1.05: the shell lays a vignette over the
    // whole stage now, and at the old exposure the floor edges sank into it.
    this.renderer.toneMappingExposure = 1.12;

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.065;
    // Do not let Orbit flatten the warehouse into an almost-horizontal strip. At that
    // angle a worker on the protected perimeter can project on top of an AMR several
    // metres inside the barrier, which is physically safe but visually misleading.
    this.controls.maxPolarAngle = Math.PI * 0.35;
    this.controls.minDistance = 5;
    this.controls.maxDistance = 95;
    this.controls.target.set(0, 0, 0);

    this.world = new THREE.Group();
    this.dynamic = new THREE.Group();
    this.routes = new THREE.Group();
    this.scene.add(this.world, this.routes, this.dynamic);
    this.robots = new Map();
    this.humans = new Map();
    this.obstacles = new Map();
    this.taskMarkers = new Map();
    this.deliveredMarkers = new Map();
    this.frameTimes = [];
    this.taskTimeline = [];
    this.deadZones = [];
    this.rackCells = [];
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.selectedId = null;
    this.map = null;
    this.meta = null;
    this.cameraMode = 'overview';
    this.lastRouteRefresh = -Infinity;
    this._addLighting();
    canvas.addEventListener('pointerdown', event => this._selectAt(event));
  }

  _addLighting() {
    const hemi = new THREE.HemisphereLight(0xbfe8ff, 0x15202c, 2.2);
    this.scene.add(hemi);
    const key = new THREE.DirectionalLight(0xffffff, 3.4);
    key.position.set(-18, 34, 16);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.camera.left = -35;
    key.shadow.camera.right = 35;
    key.shadow.camera.top = 35;
    key.shadow.camera.bottom = -35;
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(0x35c6f4, 1.2);
    rim.position.set(22, 14, -22);
    this.scene.add(rim);
  }

  load(data) {
    disposeObject(this.world);
    disposeObject(this.routes);
    disposeObject(this.dynamic);
    this.scene.remove(this.world, this.routes, this.dynamic);
    this.world = new THREE.Group();
    this.routes = new THREE.Group();
    this.dynamic = new THREE.Group();
    this.scene.add(this.world, this.routes, this.dynamic);
    this.robots.clear();
    this.humans.clear();
    this.obstacles.clear();
    this.taskMarkers.clear();
    this.deliveredMarkers.clear();
    this.deadZones = [];
    this.rackCells = [];
    this.map = data.map;
    this.meta = data.meta;
    this.frameTimes = (data.frames || []).map(frame => frame.t);
    this.taskTimeline = [];
    this._buildWarehouse();
    this._buildTasks();
    this.taskTimeline = this._buildTaskTimeline(data.frames || []);
    const first = data.frames[0] || {robots: [], humans: []};
    for (const robot of first.robots) this._ensureRobot(robot.id);
    for (const human of first.humans || []) this._ensureHuman(human.id);
    this.resize();
    this.frameFloor();
  }

  /* Place the camera and make OrbitControls accept the placement.
   *
   * With damping on, OrbitControls keeps a decaying rotation delta and re-applies
   * it on every update. Assigning camera.position is therefore not enough: the
   * controls re-derive their spherical coordinates from the new position, add the
   * leftover delta, and spend the next second dragging the camera off toward
   * wherever the last gesture was heading - which looks exactly like the reframe
   * silently failing. Running a single update with damping switched off is what
   * clears the delta (that is the branch where OrbitControls zeroes it), after
   * which the placement sticks.
   */
  _commitCamera() {
    const damping = this.controls.enableDamping;
    this.controls.enableDamping = false;
    this.controls.update();
    this.controls.enableDamping = damping;
  }

  /* Put the whole warehouse in the frame, and most of the frame.
   *
   * The camera used to sit at a fixed multiple of the map span - numbers tuned
   * when the 3D view was a panel a third of the page wide. Now that it is the
   * whole screen those same numbers leave the warehouse sitting in the middle of
   * a black field like a postage stamp, which is exactly the impression a demo of
   * a physical system cannot afford to give.
   *
   * This measures the projection instead of modelling it. The version before it
   * solved the fit with trigonometry - rotate the floor's bounding box by the
   * azimuth, foreshorten the depth axis by sin(elevation), take the binding axis
   * - which is correct in principle and got the sign of every term right, and
   * still cropped, because a hand-derived model of a perspective projection is
   * one approximation away from the projection itself. It was tuned on a 22 x 15
   * floor; Chokepoint is 25 x 9, and its far corner landed 22 px off the right
   * edge and 28 px below the bottom.
   *
   * So: put the camera at a trial distance, project the eight corners of the
   * floor's bounding box, and scale the distance by however far outside the frame
   * the worst one landed. Three passes converge well inside a pixel, and the
   * result is exact for any map shape because the thing doing the foreshortening
   * is the projection matrix rather than a model of it.
   */
  frameFloor(fill = 0.94) {
    if (!this.map || !this.meta) return;
    const widthM = this.map.width * this.meta.cell_m;
    const depthM = this.map.height * this.meta.cell_m;
    const envelope = pedestrianEnvelope(this.map, this.meta);

    // Elevation and azimuth are chosen, not fitted. 45 degrees up is enough to
    // read the aisles without flattening the racks, and 20 degrees off the short
    // axis gives depth without turning the floor corner-on - which is the state
    // the old camera was stuck in, and the reason it needed to sit so far back:
    // seen from a corner, a 31 x 21 m floor presents its 37 m diagonal.
    const elevation = Math.PI * 0.25;
    const azimuth = Math.PI * 0.11;
    const direction = new THREE.Vector3(
      Math.cos(elevation) * Math.sin(azimuth),
      Math.sin(elevation),
      Math.cos(elevation) * Math.cos(azimuth));

    // Both floor corners and rack-height corners: what has to be in frame is the
    // complete operational volume, including the protected pedestrian perimeter.
    const corners = [];
    for (const x of [-envelope.margin, widthM + envelope.margin]) {
      for (const z of [-envelope.margin, depthM + envelope.margin]) {
        for (const height of [0, 3]) corners.push(this._toWorld(x, z, height));
      }
    }

    this.controls.target.set(0, 0, 0);
    let distance = Math.max(widthM, depthM);
    for (let pass = 0; pass < 3; pass++) {
      this.camera.position.copy(direction).multiplyScalar(distance);
      this.camera.lookAt(this.controls.target);
      this.camera.updateMatrixWorld();
      let extent = 0;
      for (const corner of corners) {
        const ndc = corner.clone().project(this.camera);
        extent = Math.max(extent, Math.abs(ndc.x), Math.abs(ndc.y));
      }
      if (!Number.isFinite(extent) || extent <= 0) break;
      // extent is where the worst corner landed in normalised device coords, so
      // 1 is the frame edge. Scaling the distance by extent/fill moves it to
      // exactly `fill` of the way out, leaving the rest as margin.
      distance *= extent / fill;
    }

    this.camera.position.copy(direction).multiplyScalar(distance);
    this.controls.minDistance = Math.max(3.5, distance * 0.1);
    this.controls.maxDistance = distance * 2.8;
    this._commitCamera();
  }

  resize() {
    const width = Math.max(1, this.canvas.clientWidth);
    const height = Math.max(1, this.canvas.clientHeight);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    // Re-apply the pixel ratio, not just the size. Moving the window to a display
    // with a different scale factor changes devicePixelRatio without changing the
    // CSS size, and a renderer still holding the old ratio draws a buffer smaller
    // than the canvas it is stretched across - which looks like a soft, badly
    // rendered 3D view and reads as a weak engine.
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    this.renderer.setSize(width, height, false);
  }

  setCameraMode(mode) {
    const previous = this.cameraMode;
    this.cameraMode = mode;
    this.controls.enabled = mode === 'overview' || mode === 'tactical';
    // Coming back to Orbit from a robot-locked camera re-frames the floor. Without
    // it the free camera reappears wherever the chase cam abandoned it, which
    // feels like the view broke rather than like it returned.
    if (mode === 'overview' && previous !== 'overview') this.frameFloor();
    if (mode === 'tactical' && this.map && this.meta) {
      // Straight down, fitted the same way as Orbit rather than at a fixed
      // multiple of the span - a tactical view that crops the aisles it exists to
      // show is worse than no tactical view.
      const widthM = this.map.width * this.meta.cell_m;
      const depthM = this.map.height * this.meta.cell_m;
      const envelope = pedestrianEnvelope(this.map, this.meta);
      const framedWidth = widthM + envelope.margin * 2;
      const framedDepth = depthM + envelope.margin * 2;
      const vFov = this.camera.fov * Math.PI / 180;
      const hFov = 2 * Math.atan(Math.tan(vFov / 2) * Math.max(0.5, this.camera.aspect));
      const height = Math.max((framedDepth / 2) / Math.tan(vFov / 2),
                              (framedWidth / 2) / Math.tan(hFov / 2)) * 1.02;
      // A few degrees off plumb. Dead vertical flattens the racks into their own
      // footprints and you lose every cue about which way a robot is facing.
      this.camera.position.set(0, height, depthM * .14);
      this.controls.target.set(0, 0, 0);
      this.controls.maxDistance = height * 2.4;
      this._commitCamera();
    }
  }

  setSelected(id) {
    this.selectedId = id || null;
  }

  zoom(delta) {
    if (!this.controls.enabled) return;
    const direction = this.camera.position.clone().sub(this.controls.target);
    const factor = delta > 0 ? .86 : 1.16;
    direction.multiplyScalar(factor);
    this.camera.position.copy(this.controls.target).add(direction);
    this._commitCamera();
  }

  _toWorld(xMetres, yMetres, height = 0) {
    const widthM = this.map.width * this.meta.cell_m;
    const heightM = this.map.height * this.meta.cell_m;
    return new THREE.Vector3(xMetres - widthM / 2, height, heightM / 2 - yMetres);
  }

  _cellToWorld(x, y, height = 0) {
    return this._toWorld((x + .5) * this.meta.cell_m, (y + .5) * this.meta.cell_m, height);
  }

  _buildWarehouse() {
    const cell = this.meta.cell_m;
    const widthM = this.map.width * cell;
    const heightM = this.map.height * cell;
    const pedestrian = pedestrianEnvelope(this.map, this.meta);
    const apronMargin = pedestrian.margin;
    // One texture tile per two cells: fine enough to read as panelling from the
    // orbit camera, coarse enough that it does not shimmer at the far edge.
    const floorMap = texture('floor_panel.jpg', [
      Math.max(1, Math.round(this.map.width / 2)),
      Math.max(1, Math.round(this.map.height / 2)),
    ]);
    const floor = new THREE.Mesh(
      new THREE.BoxGeometry(widthM + apronMargin * 2 + .8, .35,
                            heightM + apronMargin * 2 + .8),
      new THREE.MeshStandardMaterial({map: floorMap, color: 0xb9c4d2,
                                      roughness: .82, metalness: .12}),
    );
    floor.position.y = -.22;
    floor.receiveShadow = true;
    this.world.add(floor);

    const grid = new THREE.GridHelper(Math.max(widthM, heightM) * 1.05,
      Math.max(this.map.width, this.map.height), 0x29465e, 0x1a2c3b);
    grid.position.y = .012;
    grid.material.transparent = true;
    grid.material.opacity = .55;
    this.world.add(grid);

    if (pedestrian.enabled) {
      const laneMaterial = new THREE.MeshBasicMaterial({
        color: PALETTE.amber, transparent: true, opacity: .38, depthWrite: false,
      });
      const bufferMaterial = new THREE.MeshBasicMaterial({
        color: PALETTE.amber, transparent: true, opacity: .055, depthWrite: false,
      });
      const barrierMaterial = new THREE.MeshStandardMaterial({
        color: PALETTE.amber, roughness: .42, metalness: .58,
      });
      const apronOffset = pedestrian.offset;
      const apronWidth = pedestrian.laneWidth;
      const bufferDepth = Math.max(cell * .2, apronOffset - apronWidth / 2);
      const horizontal = new THREE.BoxGeometry(
        widthM + apronOffset * 2, .022, apronWidth,
      );
      const vertical = new THREE.BoxGeometry(
        apronWidth, .022, heightM + apronOffset * 2,
      );
      for (const z of [heightM / 2 + apronOffset, -heightM / 2 - apronOffset]) {
        const lane = new THREE.Mesh(horizontal, laneMaterial);
        lane.position.set(0, .018, z);
        lane.renderOrder = 2;
        this.world.add(lane);
      }
      for (const x of [-widthM / 2 - apronOffset, widthM / 2 + apronOffset]) {
        const lane = new THREE.Mesh(vertical, laneMaterial);
        lane.position.set(x, .018, 0);
        lane.renderOrder = 2;
        this.world.add(lane);
      }

      // A subdued exclusion buffer plus a physical guard rail makes the semantic
      // boundary readable from every camera angle. The rail is intentionally visual;
      // the physics route is already outside the AMR map and never relies on it.
      const horizontalBuffer = new THREE.BoxGeometry(
        widthM + apronOffset * 2, .014, bufferDepth,
      );
      const verticalBuffer = new THREE.BoxGeometry(
        bufferDepth, .014, heightM + apronOffset * 2,
      );
      for (const sign of [-1, 1]) {
        const z = sign * (heightM / 2 + bufferDepth / 2);
        const buffer = new THREE.Mesh(horizontalBuffer, bufferMaterial);
        buffer.position.set(0, .012, z);
        buffer.renderOrder = 1;
        this.world.add(buffer);
      }
      for (const sign of [-1, 1]) {
        const x = sign * (widthM / 2 + bufferDepth / 2);
        const buffer = new THREE.Mesh(verticalBuffer, bufferMaterial);
        buffer.position.set(x, .012, 0);
        buffer.renderOrder = 1;
        this.world.add(buffer);
      }

      const addRail = (length, horizontalRail, x, z) => {
        const railGeometry = horizontalRail
          ? new THREE.BoxGeometry(length, .07, .07)
          : new THREE.BoxGeometry(.07, .07, length);
        for (const y of [.18, .62]) {
          const rail = new THREE.Mesh(railGeometry, barrierMaterial);
          rail.position.set(x, y, z);
          rail.castShadow = true;
          this.world.add(rail);
        }
        const postCount = Math.max(2, Math.ceil(length / (cell * 2)));
        const postGeometry = new THREE.BoxGeometry(.085, .68, .085);
        for (let index = 0; index <= postCount; index++) {
          const along = -length / 2 + length * index / postCount;
          const post = new THREE.Mesh(postGeometry, barrierMaterial);
          post.position.set(horizontalRail ? along : x, .34,
                            horizontalRail ? z : along);
          post.castShadow = true;
          this.world.add(post);
        }
      };
      addRail(widthM + .16, true, 0, heightM / 2 + .10);
      addRail(widthM + .16, true, 0, -heightM / 2 - .10);
      addRail(heightM + .16, false, widthM / 2 + .10, 0);
      addRail(heightM + .16, false, -widthM / 2 - .10, 0);
    }

    const rackCells = [];
    for (let y = 0; y < this.map.height; y++) {
      for (let x = 0; x < this.map.width; x++) {
        if (this.map.grid[y][x] === 1) rackCells.push([x, y]);
      }
    }
    this.rackCells = rackCells;
    // Build recognisable industrial shelving instead of opaque rack-shaped blocks.
    // Instancing keeps the richer geometry inexpensive even on a large warehouse.
    const uprightGeometry = new THREE.BoxGeometry(cell * .055, cell * 1.18, cell * .055);
    const shelfGeometry = new THREE.BoxGeometry(cell * .9, cell * .045, cell * .9);
    const cartonGeometry = new THREE.BoxGeometry(cell * .62, cell * .24, cell * .64);
    const rackMaterial = new THREE.MeshStandardMaterial({color: PALETTE.steel, roughness: .32, metalness: .76});
    const shelfMaterial = new THREE.MeshStandardMaterial({color: PALETTE.rack, roughness: .38, metalness: .64});
    const cartonMaterial = new THREE.MeshStandardMaterial({
      map: texture('carton.jpg'), color: 0xd8cfc2, roughness: .88, metalness: .02,
    });
    const uprights = new THREE.InstancedMesh(uprightGeometry, rackMaterial, rackCells.length * 4);
    const shelves = new THREE.InstancedMesh(shelfGeometry, shelfMaterial, rackCells.length * 3);
    const cartons = new THREE.InstancedMesh(cartonGeometry, cartonMaterial, rackCells.length * 2);
    for (const mesh of [uprights, shelves, cartons]) {
      mesh.castShadow = true;
      mesh.receiveShadow = true;
    }
    const matrix = new THREE.Matrix4();
    let uprightIndex = 0, shelfIndex = 0, cartonIndex = 0;
    rackCells.forEach(([x, y], index) => {
      const centre = this._cellToWorld(x, y, 0);
      const edge = cell * .41;
      for (const [dx, dz] of [[-edge, -edge], [edge, -edge], [-edge, edge], [edge, edge]]) {
        matrix.setPosition(centre.x + dx, cell * .59, centre.z + dz);
        uprights.setMatrixAt(uprightIndex++, matrix);
      }
      for (const level of [cell * .1, cell * .56, cell * 1.02]) {
        matrix.setPosition(centre.x, level, centre.z);
        shelves.setMatrixAt(shelfIndex++, matrix);
      }
      for (const level of [cell * .3, cell * .76]) {
        const stagger = (index % 2 ? 1 : -1) * cell * .08;
        matrix.setPosition(centre.x + stagger, level, centre.z);
        cartons.setMatrixAt(cartonIndex++, matrix);
      }
    });
    this.world.add(uprights, shelves, cartons);

    for (const [x, y] of this.map.stations || []) {
      this.world.add(this._makePad(x, y, 0x3b82f6, 'PICK / DROP'));
    }
    for (const [x, y] of this.map.docks || []) {
      this.world.add(this._makePad(x, y, 0x22c55e, 'CHARGE'));
    }

    for (const zone of this.meta.dead_zones || []) {
      const [x, y, radiusCells] = zone;
      const radius = radiusCells * cell;
      // Make radio coverage a volume, not a subtle floor decal.  Judges can now see
      // exactly where connectivity degrades even when racks obscure the ground plane.
      const geometry = new THREE.CylinderGeometry(radius, radius, 1.6, 64, 1, true);
      const material = new THREE.MeshBasicMaterial({color: PALETTE.rose, transparent: true, opacity: .11,
        side: THREE.DoubleSide, depthWrite: false});
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.copy(this._toWorld(x * cell, y * cell, .82));
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(radius * .96, radius, 64),
        new THREE.MeshBasicMaterial({color: PALETTE.rose, transparent: true, opacity: .58,
          side: THREE.DoubleSide, depthWrite: false}),
      );
      ring.rotation.x = -Math.PI / 2;
      ring.position.y = -.79;
      mesh.add(ring);
      const upperRing = ring.clone();
      upperRing.position.y = .79;
      mesh.add(upperRing);
      const label = makeLabel('MESH DEAD ZONE', '#ff6577', true);
      label.position.set(0, 1.02, 0);
      mesh.add(label);
      this.world.add(mesh);
      this.deadZones.push(mesh);
    }

    const boundary = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(widthM + .8, .2, heightM + .8)),
      new THREE.LineBasicMaterial({color: 0x41647f, transparent: true, opacity: .8}),
    );
    boundary.position.y = .02;
    this.world.add(boundary);
  }

  _makePad(x, y, colour, labelText) {
    const cell = this.meta.cell_m;
    const group = new THREE.Group();
    group.position.copy(this._cellToWorld(x, y, .02));
    const pad = new THREE.Mesh(
      new THREE.CylinderGeometry(cell * .42, cell * .42, .08, 32),
      new THREE.MeshStandardMaterial({color: colour, emissive: colour, emissiveIntensity: .28,
        roughness: .45, metalness: .35}),
    );
    pad.receiveShadow = true;
    group.add(pad);
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(cell * .35, .025, 8, 40),
      new THREE.MeshBasicMaterial({color: 0xffffff, transparent: true, opacity: .72}),
    );
    ring.rotation.x = Math.PI / 2;
    ring.position.y = .07;
    group.add(ring);
    const label = makeLabel(labelText, hexCss(colour), true);
    label.position.y = 1.15;
    label.scale.multiplyScalar(.58);
    group.add(label);
    return group;
  }

  _buildTasks() {
    const catalog = this.meta.tasks_catalog || [];
    const rackUse = new Map();
    for (const task of catalog) {
      const pickMarker = this._makeCargoMarker(task, false, rackUse);
      const deliveredMarker = this._makeCargoMarker(task, true, rackUse);
      if (!pickMarker || !deliveredMarker) continue;
      deliveredMarker.visible = false;
      this.taskMarkers.set(task.id, pickMarker);
      this.deliveredMarkers.set(task.id, deliveredMarker);
      this.world.add(pickMarker, deliveredMarker);
    }
  }

  _cargoColour(task) {
    const colours = {normal: 0x5caeff, fragile: 0xc084fc, heavy: 0xf59e0b, hazardous: 0xfb7185};
    return colours[task?.cargo_type] || PALETTE.cyan;
  }

  _selectRackSlot(anchor, rackUse) {
    const rackKey = ([x, y]) => `${x},${y}`;
    let candidates = [
      [anchor[0] - 1, anchor[1]], [anchor[0] + 1, anchor[1]],
      [anchor[0], anchor[1] - 1], [anchor[0], anchor[1] + 1],
    ].filter(([x, y]) => this.map.grid?.[y]?.[x] === 1);
    if (!candidates.length) {
      candidates = [...this.rackCells].sort((a, b) => {
        const ad = Math.abs(a[0] - anchor[0]) + Math.abs(a[1] - anchor[1]);
        const bd = Math.abs(b[0] - anchor[0]) + Math.abs(b[1] - anchor[1]);
        return ad - bd || a[1] - b[1] || a[0] - b[0];
      }).slice(0, 8);
    }
    candidates.sort((a, b) =>
      (rackUse.get(rackKey(a)) || 0) - (rackUse.get(rackKey(b)) || 0)
      || a[1] - b[1] || a[0] - b[0]);
    const rack = candidates[0];
    if (!rack) return null;
    const key = rackKey(rack);
    const slot = rackUse.get(key) || 0;
    rackUse.set(key, slot + 1);
    return {rack, slot};
  }

  _makeCargoMarker(task, delivered, rackUse) {
    const colour = delivered ? PALETTE.green : this._cargoColour(task);
    const anchor = delivered ? task.drop : task.pick;
    const placement = this._selectRackSlot(anchor, rackUse);
    if (!placement) return null;
    const {rack, slot} = placement;
    const slotOffsets = [[-.19, -.19], [.19, -.19], [-.19, .19], [.19, .19]];
    const [slotX, slotZ] = slotOffsets[slot % slotOffsets.length];
    const cell = this.meta.cell_m;
    const marker = new THREE.Group();
    marker.position.copy(this._cellToWorld(rack[0], rack[1], 0));
    const box = new THREE.Mesh(
      new THREE.BoxGeometry(cell * .34, cell * .26, cell * .34),
      new THREE.MeshStandardMaterial({color: colour, roughness: .48, metalness: .12,
        emissive: colour, emissiveIntensity: delivered ? .16 : .08}),
    );
    box.position.set(slotX * cell, cell * 1.22, slotZ * cell);
    box.castShadow = true;
    marker.add(box);
    const strap = new THREE.Mesh(
      new THREE.BoxGeometry(cell * .055, cell * .272, cell * .35),
      new THREE.MeshStandardMaterial({color: 0xe8f2f8, roughness: .45, metalness: .18}),
    );
    strap.position.copy(box.position);
    marker.add(strap);
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(cell * .25, cell * .29, 32),
      new THREE.MeshBasicMaterial({color: colour, transparent: true, opacity: .75,
        side: THREE.DoubleSide, depthWrite: false}),
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(slotX * cell, cell * 1.055, slotZ * cell);
    marker.add(ring);
    marker.userData = {
      taskMarker: true, taskId: task.id, delivered, rackCell: rack,
    };
    return marker;
  }

  _buildTaskTimeline(frames) {
    const completed = new Set();
    const previousByRobot = new Map();
    return frames.map(frame => {
      const active = new Map();
      for (const info of frame.fleet || []) {
        const previous = previousByRobot.get(info.id);
        if (previous && Number(info.done || 0) > Number(previous.done || 0)
            && previous.task) {
          completed.add(previous.task);
        }
        const decision = info.decision;
        const decisionTask = decision?.code === 'TASK_COMPLETED'
          ? decision.details?.task : null;
        if (decisionTask) completed.add(decisionTask);
        if (info.task) active.set(info.task, info);
        previousByRobot.set(info.id, {task: info.task, done: info.done});
      }
      return {completed: new Set(completed), active};
    });
  }

  _timelineAt(simTime) {
    if (!this.taskTimeline.length) return {completed: new Set(), active: new Map()};
    let low = 0;
    let high = this.frameTimes.length - 1;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      if (this.frameTimes[middle] <= simTime) low = middle + 1;
      else high = middle - 1;
    }
    return this.taskTimeline[Math.max(0, high)] || this.taskTimeline[0];
  }

  _updateTaskCargo(frame, simTime) {
    const timeline = this._timelineAt(simTime);
    const activeByTask = new Map();
    for (const info of frame.fleet || []) {
      if (info.task) activeByTask.set(info.task, info);
    }
    for (const [taskId, marker] of this.taskMarkers) {
      const info = activeByTask.get(taskId) || timeline.active.get(taskId);
      marker.visible = !timeline.completed.has(taskId) && !(info && info.carry);
    }
    for (const [taskId, marker] of this.deliveredMarkers) {
      marker.visible = timeline.completed.has(taskId);
    }
  }

  _ensureRobot(id) {
    if (this.robots.has(id)) return this.robots.get(id);
    const index = Math.max(0, (parseInt(id.replace(/\D/g, ''), 10) || 1) - 1);
    const colour = ROBOT_COLOURS[index % ROBOT_COLOURS.length];
    const group = new THREE.Group();
    group.userData.robotId = id;
    const base = new THREE.Mesh(
      new THREE.CylinderGeometry(.48, .5, .24, 32),
      new THREE.MeshStandardMaterial({color: colour, roughness: .25, metalness: .64}),
    );
    base.position.y = .22;
    base.castShadow = true;
    base.userData.robotId = id;
    group.add(base);
    // Six materials, one per box face, so the baked top-down view lands on +Y and
    // nowhere else - it is the face the orbit and tactical cameras actually see.
    // The map is greyscale and the robot's identity colour is the tint, which is
    // how one texture serves all ten liveries. See tools/bake_twin_textures.py.
    const deckSide = new THREE.MeshStandardMaterial({color: 0x10202c, roughness: .28, metalness: .68});
    const deckTop = new THREE.MeshStandardMaterial({
      map: texture('amr_deck.png'), color: colour,
      roughness: .42, metalness: .34, transparent: true,
    });
    const top = new THREE.Mesh(
      new THREE.BoxGeometry(.68, .22, .66),
      [deckSide, deckSide, deckTop, deckSide, deckSide, deckSide],
    );
    top.position.y = .43;
    top.castShadow = true;
    top.userData.robotId = id;
    group.add(top);
    const wheelMaterial = new THREE.MeshStandardMaterial({color: 0x05090d, roughness: .76, metalness: .28});
    const wheels = [];
    for (const [x, z] of [[-.43, -.25], [.43, -.25], [-.43, .25], [.43, .25]]) {
      const wheel = new THREE.Mesh(new THREE.CylinderGeometry(.105, .105, .09, 18), wheelMaterial);
      wheel.rotation.z = Math.PI / 2;
      wheel.position.set(x, .16, z);
      wheel.castShadow = true;
      wheel.userData.robotId = id;
      wheels.push(wheel);
      group.add(wheel);
    }
    const bumper = new THREE.Mesh(
      new THREE.BoxGeometry(.66, .13, .08),
      new THREE.MeshStandardMaterial({color: 0x182d3b, roughness: .5, metalness: .54}),
    );
    bumper.position.set(0, .24, -.49);
    bumper.userData.robotId = id;
    group.add(bumper);
    const sensor = new THREE.Mesh(
      new THREE.BoxGeometry(.48, .08, .09),
      new THREE.MeshStandardMaterial({color: 0x081019, emissive: colour, emissiveIntensity: .95}),
    );
    sensor.position.set(0, .47, -.36);
    sensor.userData.robotId = id;
    group.add(sensor);
    const mast = new THREE.Mesh(
      new THREE.CylinderGeometry(.035, .045, .19, 16),
      new THREE.MeshStandardMaterial({color: 0x7d91a1, roughness: .3, metalness: .78}),
    );
    mast.position.set(0, .62, .08);
    const lidar = new THREE.Mesh(
      new THREE.CylinderGeometry(.13, .13, .085, 24),
      new THREE.MeshStandardMaterial({color: 0x071019, emissive: colour, emissiveIntensity: .38,
        roughness: .18, metalness: .64}),
    );
    lidar.position.set(0, .75, .08);
    lidar.userData.robotId = id;
    const beacon = new THREE.Mesh(
      new THREE.SphereGeometry(.055, 16, 10),
      new THREE.MeshBasicMaterial({color: PALETTE.green}),
    );
    beacon.position.set(.24, .6, .15);
    group.add(mast, lidar, beacon);
    const deckRailMaterial = new THREE.MeshStandardMaterial({color: 0x8da2b5, roughness: .34, metalness: .78});
    for (const x of [-.32, .32]) {
      const rail = new THREE.Mesh(new THREE.BoxGeometry(.035, .07, .56), deckRailMaterial);
      rail.position.set(x, .57, .02);
      group.add(rail);
    }
    const payload = new THREE.Group();
    // Textured, but the material colour is still the channel that says what the
    // cargo is - _cargoColour rewrites it every frame. A map multiplies the tint
    // rather than replacing it, so the carton reads as card AND stays colour-coded.
    const payloadBox = new THREE.Mesh(
      new THREE.BoxGeometry(.46, .34, .46),
      new THREE.MeshStandardMaterial({map: texture('carton.jpg'), color: PALETTE.cyan,
                                      roughness: .7, metalness: .06}),
    );
    payloadBox.position.set(0, .78, -.18);
    payloadBox.castShadow = true;
    payload.add(payloadBox);
    payload.userData = {box: payloadBox};
    payload.visible = false;
    group.add(payload);
    const arrow = new THREE.Mesh(
      new THREE.ConeGeometry(.13, .35, 3),
      new THREE.MeshBasicMaterial({color: 0xffffff}),
    );
    arrow.rotation.x = -Math.PI / 2;
    arrow.position.set(0, .58, -.18);
    group.add(arrow);
    const halo = new THREE.Mesh(
      new THREE.RingGeometry(.58, .68, 48),
      new THREE.MeshBasicMaterial({color: colour, transparent: true, opacity: .65,
        side: THREE.DoubleSide, depthWrite: false}),
    );
    halo.rotation.x = -Math.PI / 2;
    halo.position.y = .035;
    group.add(halo);
    const selection = new THREE.Mesh(
      new THREE.RingGeometry(.76, .80, 48),
      new THREE.MeshBasicMaterial({color: 0xffffff, transparent: true, opacity: 0,
        side: THREE.DoubleSide, depthWrite: false}),
    );
    selection.rotation.x = -Math.PI / 2;
    selection.position.y = .045;
    group.add(selection);
    const label = makeLabel(id, hexCss(colour), true);
    label.position.y = 1.34;
    label.scale.multiplyScalar(.84);
    group.add(label);
    group.userData = {robotId: id, colour, halo, selection, label, beacon, wheels, payload};
    this.dynamic.add(group);
    this.robots.set(id, group);
    return group;
  }

  _ensureHuman(id) {
    if (this.humans.has(id)) return this.humans.get(id);
    const group = new THREE.Group();
    const uniform = new THREE.MeshStandardMaterial({color: 0x24384a, roughness: .78});
    const vestMaterial = new THREE.MeshStandardMaterial({color: 0xf5b843, roughness: .64});
    const skin = new THREE.MeshStandardMaterial({color: 0xd9a276, roughness: .82});
    const limbs = [];
    const arms = [];
    for (const x of [-.1, .1]) {
      const leg = new THREE.Mesh(new THREE.CapsuleGeometry(.075, .47, 5, 10), uniform);
      leg.position.set(x, .34, 0);
      leg.castShadow = true;
      limbs.push(leg);
      group.add(leg);
    }
    const body = new THREE.Mesh(new THREE.CapsuleGeometry(.2, .47, 6, 14), vestMaterial);
    body.position.y = .97;
    body.castShadow = true;
    for (const x of [-.27, .27]) {
      const arm = new THREE.Mesh(new THREE.CapsuleGeometry(.055, .39, 5, 9), uniform);
      arm.position.set(x, .96, 0);
      arm.rotation.z = x < 0 ? -.12 : .12;
      arm.castShadow = true;
      limbs.push(arm);
      arms.push(arm);
      group.add(arm);
    }
    const head = new THREE.Mesh(
      new THREE.SphereGeometry(.2, 20, 14),
      skin,
    );
    head.position.y = 1.48;
    head.castShadow = true;
    const helmet = new THREE.Mesh(
      new THREE.SphereGeometry(.215, 20, 10, 0, Math.PI * 2, 0, Math.PI * .58),
      new THREE.MeshStandardMaterial({color: 0xf2cf45, roughness: .48}),
    );
    helmet.position.y = 1.56;
    const stripe = new THREE.Mesh(
      new THREE.TorusGeometry(.215, .026, 8, 28),
      new THREE.MeshBasicMaterial({color: 0xf8ffb5}),
    );
    stripe.rotation.x = Math.PI / 2;
    stripe.position.y = 1.02;
    const pauseRing = new THREE.Mesh(
      new THREE.RingGeometry(.42, .5, 36),
      new THREE.MeshBasicMaterial({color: PALETTE.amber, transparent: true, opacity: .72,
        side: THREE.DoubleSide, depthWrite: false}),
    );
    pauseRing.rotation.x = -Math.PI / 2;
    pauseRing.position.y = .025;
    pauseRing.visible = false;
    const workTool = new THREE.Group();
    const tablet = new THREE.Mesh(
      new THREE.BoxGeometry(.30, .38, .045),
      new THREE.MeshStandardMaterial({color: 0x172633, roughness: .46}),
    );
    const tabletScreen = new THREE.Mesh(
      new THREE.BoxGeometry(.24, .30, .012),
      new THREE.MeshBasicMaterial({color: 0x55dfff}),
    );
    tabletScreen.position.z = -.029;
    workTool.add(tablet, tabletScreen);
    workTool.position.set(0, 1.02, -.30);
    workTool.rotation.x = -.34;
    workTool.visible = false;
    group.add(body, head, helmet, stripe, pauseRing, workTool);
    // The yellow vest and helmet already communicate the role. A compact ID avoids
    // the long "WORKER" plaques covering AMR labels when both share a junction.
    const label = makeLabel(id, '#f5b843', true);
    label.position.y = 2.02;
    label.scale.multiplyScalar(.52);
    group.add(label);
    group.userData = {limbs, arms, pauseRing, workTool};
    this.dynamic.add(group);
    this.humans.set(id, group);
    return group;
  }

  _ensureObstacle(id) {
    if (this.obstacles.has(id)) return this.obstacles.get(id);
    const group = new THREE.Group();
    const pallet = new THREE.Mesh(
      new THREE.BoxGeometry(.92, .11, .72),
      new THREE.MeshStandardMaterial({color: 0x7b4d27, roughness: .9}),
    );
    pallet.position.y = .08;
    pallet.castShadow = true;
    group.add(pallet);
    for (const z of [-.25, 0, .25]) {
      const slat = new THREE.Mesh(
        new THREE.BoxGeometry(.86, .07, .12),
        new THREE.MeshStandardMaterial({color: 0xa86f38, roughness: .86}),
      );
      slat.position.set(0, .17, z);
      group.add(slat);
    }
    const warning = new THREE.Mesh(
      new THREE.RingGeometry(.56, .65, 36),
      new THREE.MeshBasicMaterial({color: PALETTE.rose, transparent: true, opacity: .8,
        side: THREE.DoubleSide, depthWrite: false}),
    );
    warning.rotation.x = -Math.PI / 2;
    warning.position.y = .025;
    group.add(warning);
    const label = makeLabel('BLOCKED AISLE', '#ff6577', true);
    label.position.y = 1.25;
    label.scale.multiplyScalar(.72);
    group.add(label);
    group.userData.warning = warning;
    this.dynamic.add(group);
    this.obstacles.set(id, group);
    return group;
  }

  update(frame, selectedId, cameraMode, simTime) {
    if (!this.map || !frame) return;
    this.selectedId = selectedId || null;
    if (cameraMode !== this.cameraMode) this.setCameraMode(cameraMode);
    const fleetById = new Map((frame.fleet || []).map(item => [item.id, item]));
    const denseFleet = fleetById.size >= 9;
    for (const robot of frame.robots || []) {
      const group = this._ensureRobot(robot.id);
      group.position.copy(this._toWorld(robot.x, robot.y, 0));
      group.rotation.y = -robot.th - Math.PI / 2;
      const info = fleetById.get(robot.id) || {};
      const stateColour = info.failed ? PALETTE.rose
        : info.state === 'charging' ? PALETTE.green
        : info.state === 'blocked' ? PALETTE.rose
        : info.state === 'retreat' ? PALETTE.amber
        : group.userData.colour;
      group.userData.halo.material.color.setHex(stateColour);
      group.userData.halo.material.opacity = .48 + .24 * (1 + Math.sin(simTime * 4)) / 2;
      group.userData.selection.material.opacity = robot.id === this.selectedId ? .95 : 0;
      group.userData.selection.rotation.z = simTime * 1.4;
      // At ten-plus AMRs, ten permanent billboards obscure the exact traffic that the
      // audience is trying to inspect. Keep labels for the selected robot and genuine
      // exceptions; the fleet panel and stable colour still identify every chassis.
      group.userData.label.visible = !denseFleet || robot.id === this.selectedId
        || info.failed || info.state === 'blocked' || info.state === 'retreat'
        || cameraMode === 'chase' || cameraMode === 'pov';
      group.userData.beacon.material.color.setHex(stateColour);
      group.userData.beacon.scale.setScalar(.82 + .25 * (1 + Math.sin(simTime * 5)) / 2);
      for (const wheel of group.userData.wheels) wheel.rotation.x = -simTime * 4;
      const carrying = Boolean(robot.carry || info.carry);
      group.userData.payload.visible = carrying;
      if (carrying) {
        group.userData.payload.userData.box.material.color.setHex(this._cargoColour(info));
      }
      group.visible = true;
    }
    this._updateTaskCargo(frame, simTime);
    for (const human of frame.humans || []) {
      const group = this._ensureHuman(human.id);
      group.position.copy(this._toWorld(human.x, human.y, 0));
      group.rotation.y = -human.th - Math.PI / 2;
      const stride = human.paused ? 0 : Math.sin(simTime * 7 + Number(human.id.replace(/\D/g, '') || 0)) * .32;
      group.userData.limbs.forEach((limb, index) => {
        limb.rotation.x = index % 2 ? -stride : stride;
      });
      const humanMode = human.mode || (human.paused ? 'yielding' : 'walking');
      group.userData.workTool.visible = humanMode === 'working';
      if (humanMode === 'working') {
        group.userData.arms.forEach((arm, index) => {
          arm.rotation.x = -.86;
          arm.rotation.z = index ? -.18 : .18;
        });
      }
      group.userData.pauseRing.visible = humanMode !== 'walking';
      group.userData.pauseRing.material.color.setHex(
        humanMode === 'working' ? PALETTE.green : PALETTE.amber,
      );
      group.userData.pauseRing.rotation.z = simTime * 1.5;
    }
    const activeObstacles = new Set();
    for (const obstacle of frame.obstacles || []) {
      const group = this._ensureObstacle(obstacle.id);
      group.position.copy(this._toWorld(obstacle.x, obstacle.y, 0));
      group.userData.warning.rotation.z = simTime * 1.1;
      group.visible = true;
      activeObstacles.add(obstacle.id);
    }
    for (const [id, group] of this.obstacles) {
      if (!activeObstacles.has(id)) group.visible = false;
    }
    for (const zone of this.deadZones) {
      zone.material.opacity = .08 + .045 * (1 + Math.sin(simTime * 1.8)) / 2;
    }
    if (simTime - this.lastRouteRefresh > .09 || simTime < this.lastRouteRefresh) {
      this._refreshRoutes(frame);
      this.lastRouteRefresh = simTime;
    }
    this._updateCamera(frame, simTime);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  _refreshRoutes(frame) {
    disposeObject(this.routes);
    this.scene.remove(this.routes);
    this.routes = new THREE.Group();
    this.scene.add(this.routes);
    const posById = new Map((frame.robots || []).map(robot => [robot.id, robot]));
    const drawn = new Set();
    for (const info of frame.fleet || []) {
      const robot = posById.get(info.id);
      if (!robot) continue;
      const group = this.robots.get(info.id);
      const colour = group ? group.userData.colour : PALETTE.cyan;
      const points = [this._toWorld(robot.x, robot.y, .08),
        ...(info.path || []).map(([x, y]) => this._cellToWorld(x, y, .08))];
      if (points.length > 1) {
        const line = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(points),
          new THREE.LineBasicMaterial({color: colour, transparent: true, opacity: .82}),
        );
        this.routes.add(line);
        for (const [x, y] of (info.path || []).slice(0, 5)) {
          const lease = new THREE.Mesh(
            new THREE.BoxGeometry(this.meta.cell_m * .68, .035, this.meta.cell_m * .68),
            new THREE.MeshBasicMaterial({color: colour, transparent: true, opacity: .14,
              depthWrite: false}),
          );
          lease.position.copy(this._cellToWorld(x, y, .035));
          this.routes.add(lease);
        }
      }
      for (const peerId of info.peers || []) {
        const key = info.id < peerId ? `${info.id}|${peerId}` : `${peerId}|${info.id}`;
        if (drawn.has(key)) continue;
        drawn.add(key);
        const peer = posById.get(peerId);
        if (!peer) continue;
        const link = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints([
            this._toWorld(robot.x, robot.y, .72),
            this._toWorld(peer.x, peer.y, .72),
          ]),
          new THREE.LineDashedMaterial({color: PALETTE.cyan, transparent: true,
            opacity: .22, dashSize: .25, gapSize: .18}),
        );
        link.computeLineDistances();
        this.routes.add(link);
      }
    }
  }

  _updateCamera(frame, simTime) {
    const selected = (frame.robots || []).find(robot => robot.id === this.selectedId)
      || (frame.robots || [])[0];
    if (!selected || this.cameraMode === 'overview' || this.cameraMode === 'tactical') return;
    const target = this._toWorld(selected.x, selected.y, .42);
    const forward = new THREE.Vector3(Math.cos(selected.th), 0, -Math.sin(selected.th));
    let desired;
    if (this.cameraMode === 'pov') {
      desired = target.clone().addScaledVector(forward, .55);
      desired.y = .82;
      this.camera.position.lerp(desired, .18);
      this.camera.lookAt(target.clone().addScaledVector(forward, 7).setY(.7));
    } else {
      desired = target.clone().addScaledVector(forward, -5.5);
      desired.y = 3.7;
      this.camera.position.lerp(desired, .085);
      const look = target.clone().addScaledVector(forward, 1.25);
      this.camera.lookAt(look);
    }
  }

  _selectAt(event) {
    const rect = this.canvas.getBoundingClientRect();
    this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects([...this.robots.values()], true);
    const hit = hits.find(item => {
      let node = item.object;
      while (node && !node.userData.robotId) node = node.parent;
      return Boolean(node && node.userData.robotId);
    });
    if (!hit) return;
    let node = hit.object;
    while (node && !node.userData.robotId) node = node.parent;
    if (node && node.userData.robotId && this.onSelect) this.onSelect(node.userData.robotId);
  }
}
