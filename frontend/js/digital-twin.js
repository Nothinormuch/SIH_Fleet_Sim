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

function makeLabel(text, colour = '#eaf6ff', compact = false) {
  const canvas = document.createElement('canvas');
  canvas.width = compact ? 256 : 512;
  canvas.height = compact ? 72 : 96;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = 'rgba(5, 13, 22, .88)';
  ctx.strokeStyle = colour;
  ctx.lineWidth = 3;
  const radius = 16;
  ctx.beginPath();
  ctx.roundRect(3, 3, canvas.width - 6, canvas.height - 6, radius);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = '#ffffff';
  ctx.font = `700 ${compact ? 30 : 34}px Inter, system-ui, sans-serif`;
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

export class DigitalTwin {
  constructor(canvas, onSelect) {
    this.canvas = canvas;
    this.onSelect = onSelect;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x071019);
    this.scene.fog = new THREE.FogExp2(0x071019, 0.009);
    this.camera = new THREE.PerspectiveCamera(43, 1, 0.1, 1000);
    this.camera.position.set(18, 25, 24);
    this.renderer = new THREE.WebGLRenderer({canvas, antialias: true, powerPreference: 'high-performance'});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.065;
    this.controls.maxPolarAngle = Math.PI * 0.47;
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
    this.deadZones = [];
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
    this.deadZones = [];
    this.map = data.map;
    this.meta = data.meta;
    this._buildWarehouse();
    this._buildTasks();
    const first = data.frames[0] || {robots: [], humans: []};
    for (const robot of first.robots) this._ensureRobot(robot.id);
    for (const human of first.humans || []) this._ensureHuman(human.id);
    this.resize();
    const widthM = this.map.width * this.meta.cell_m;
    const heightM = this.map.height * this.meta.cell_m;
    const span = Math.max(widthM, heightM);
    this.camera.position.set(span * .72, span * .82, span * .78);
    this.controls.target.set(0, 0, 0);
    this.controls.maxDistance = span * 2.8;
    this.controls.update();
  }

  resize() {
    const width = Math.max(1, this.canvas.clientWidth);
    const height = Math.max(1, this.canvas.clientHeight);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  setCameraMode(mode) {
    this.cameraMode = mode;
    this.controls.enabled = mode === 'overview' || mode === 'tactical';
    if (mode === 'tactical' && this.map && this.meta) {
      const span = Math.max(this.map.width, this.map.height) * this.meta.cell_m;
      this.camera.position.set(span * .08, span * 1.18, span * .48);
      this.controls.target.set(0, 0, 0);
      this.controls.update();
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
    this.controls.update();
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
    const floor = new THREE.Mesh(
      new THREE.BoxGeometry(widthM + 1.2, .35, heightM + 1.2),
      new THREE.MeshStandardMaterial({color: PALETTE.floor, roughness: .88, metalness: .08}),
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

    const rackCells = [];
    for (let y = 0; y < this.map.height; y++) {
      for (let x = 0; x < this.map.width; x++) {
        if (this.map.grid[y][x] === 1) rackCells.push([x, y]);
      }
    }
    // Build recognisable industrial shelving instead of opaque rack-shaped blocks.
    // Instancing keeps the richer geometry inexpensive even on a large warehouse.
    const uprightGeometry = new THREE.BoxGeometry(cell * .055, cell * 1.18, cell * .055);
    const shelfGeometry = new THREE.BoxGeometry(cell * .9, cell * .045, cell * .9);
    const cartonGeometry = new THREE.BoxGeometry(cell * .62, cell * .24, cell * .64);
    const rackMaterial = new THREE.MeshStandardMaterial({color: PALETTE.steel, roughness: .32, metalness: .76});
    const shelfMaterial = new THREE.MeshStandardMaterial({color: PALETTE.rack, roughness: .38, metalness: .64});
    const cartonMaterial = new THREE.MeshStandardMaterial({color: 0xb47a43, roughness: .84, metalness: .02});
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
    const colours = {normal: 0x5caeff, fragile: 0xc084fc, heavy: 0xf59e0b, hazardous: 0xfb7185};
    for (const task of catalog) {
      const colour = colours[task.cargo_type] || PALETTE.cyan;
      const marker = new THREE.Group();
      marker.position.copy(this._cellToWorld(task.pick[0], task.pick[1], .12));
      const box = new THREE.Mesh(
        new THREE.BoxGeometry(.58, .52, .58),
        new THREE.MeshStandardMaterial({color: colour, roughness: .55, metalness: .08}),
      );
      box.position.y = .28;
      box.castShadow = true;
      marker.add(box);
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(.42, .49, 32),
        new THREE.MeshBasicMaterial({color: colour, transparent: true, opacity: .75,
          side: THREE.DoubleSide, depthWrite: false}),
      );
      ring.rotation.x = -Math.PI / 2;
      marker.add(ring);
      marker.userData.taskMarker = true;
      this.world.add(marker);
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
    const top = new THREE.Mesh(
      new THREE.BoxGeometry(.68, .22, .66),
      new THREE.MeshStandardMaterial({color: 0x10202c, roughness: .28, metalness: .68}),
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
    group.userData = {robotId: id, colour, halo, selection, label, beacon, wheels};
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
    group.add(body, head, helmet, stripe, pauseRing);
    const label = makeLabel(`${id} · WORKER`, '#f5b843', true);
    label.position.y = 2.08;
    label.scale.multiplyScalar(.72);
    group.add(label);
    group.userData = {limbs, pauseRing};
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
      group.userData.beacon.material.color.setHex(stateColour);
      group.userData.beacon.scale.setScalar(.82 + .25 * (1 + Math.sin(simTime * 5)) / 2);
      for (const wheel of group.userData.wheels) wheel.rotation.x = -simTime * 4;
      group.visible = true;
    }
    for (const human of frame.humans || []) {
      const group = this._ensureHuman(human.id);
      group.position.copy(this._toWorld(human.x, human.y, 0));
      group.rotation.y = -human.th - Math.PI / 2;
      const stride = human.paused ? 0 : Math.sin(simTime * 7 + Number(human.id.replace(/\D/g, '') || 0)) * .32;
      group.userData.limbs.forEach((limb, index) => {
        limb.rotation.x = index % 2 ? -stride : stride;
      });
      group.userData.pauseRing.visible = Boolean(human.paused);
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
