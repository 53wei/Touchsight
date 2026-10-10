let THREE;
try {
  THREE = await import("./vendor/three.module.js");
} catch (_localError) {
  THREE = await import("https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js");
}

/*
 * The surface is a digital twin of the current high-density target pin plate. The default
 * 3D view always fits the full board first, then lets the user orbit, zoom and
 * pan it. Raised pins are white and fully lowered pins are black so a judge can
 * read the intended physical state immediately. UInt8 height remains real
 * extrusion and is still legible through the rim and neutral cast shadow.
 */
export class PinRenderer {
  constructor(container) {
    this.container = container;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xf1f2ef);

    this.camera = new THREE.PerspectiveCamera(34, 1, 0.1, 1000);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.7));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    container.prepend(this.renderer.domElement);

    this.pitch = 1.0;
    this.radius = 0.30;
    this.inactiveHeight = 0.09;
    this.maxHeight = 1.45;
    this.cols = 0;
    this.rows = 0;
    this.levels = 256;
    this.currentLevels = new Float32Array(0);
    this.targetLevels = new Float32Array(0);
    this.motionSpeed = 8.5;
    this.isMoving = false;
    this.lastTick = performance.now();
    this.viewMode = "3d";
    this.defaultYaw = 0.46;
    this.defaultPitch = 0.88;
    this.yaw = this.defaultYaw;
    this.pitchAngle = this.defaultPitch;
    this.distance = 80;
    this.fitDistance = 80;
    this.target = new THREE.Vector3(0, this.maxHeight * 0.5, 0);
    this.userAdjusted = false;
    this._drag = null;

    this._raisedColor = new THREE.Color(0xffffff);
    this._loweredColor = new THREE.Color(0x050505);

    // Instance colour encodes only the binary physical state (raised/lowered),
    // never a semantic region and never a replacement for the UInt8 height.
    this.pinMaterial = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      metalness: 0.04,
      roughness: 0.48,
    });
    this.rimMaterial = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      metalness: 0.02,
      roughness: 0.52,
      transparent: true,
      opacity: 0.92,
    });
    this.shadowMaterial = new THREE.MeshBasicMaterial({
      color: 0x3b4244,
      transparent: true,
      opacity: 0.68,
      depthWrite: false,
    });
    this.boardMaterial = new THREE.MeshStandardMaterial({
      color: 0x303638,
      metalness: 0.08,
      roughness: 0.58,
    });

    this._addLights();
    this._resizeObserver = new ResizeObserver(() => this.resize());
    this._resizeObserver.observe(container);
    this._raycaster = new THREE.Raycaster();
    this._pointer = new THREE.Vector2();
    this._bindControls();
    this.resize();
    this.animate();
  }

  _addLights() {
    const hemi = new THREE.HemisphereLight(0xffffff, 0x596164, 1.8);
    this.scene.add(hemi);

    const key = new THREE.DirectionalLight(0xffffff, 3.8);
    key.position.set(-16, 30, 20);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.camera.near = 1;
    key.shadow.camera.far = 120;
    this.scene.add(key);

    const fill = new THREE.DirectionalLight(0xdbe8ee, 0.9);
    fill.position.set(18, 14, -10);
    this.scene.add(fill);
  }

  _disposeSurface() {
    for (const obj of [this.shafts, this.caps, this.rims, this.shadows, this.board]) {
      if (!obj) continue;
      this.scene.remove(obj);
      if (obj.geometry) obj.geometry.dispose();
    }
    this.shafts = null;
    this.caps = null;
    this.rims = null;
    this.shadows = null;
    this.board = null;
  }

  _buildSurface(cols, rows) {
    this._disposeSurface();
    this.cols = cols;
    this.rows = rows;
    const count = cols * rows;
    this.currentLevels = new Float32Array(count);
    this.targetLevels = new Float32Array(count);

    const shaftGeometry = new THREE.CylinderGeometry(this.radius, this.radius, 1, 12, 1, false);
    const capGeometry = new THREE.SphereGeometry(this.radius * 1.02, 12, 8);
    const rimGeometry = new THREE.RingGeometry(this.radius * 1.16, this.radius * 1.34, 12);
    rimGeometry.rotateX(-Math.PI / 2);
    const shadowGeometry = new THREE.CircleGeometry(this.radius * 1.28, 12);
    shadowGeometry.rotateX(-Math.PI / 2);

    this.shafts = new THREE.InstancedMesh(shaftGeometry, this.pinMaterial, count);
    this.caps = new THREE.InstancedMesh(capGeometry, this.pinMaterial, count);
    this.rims = new THREE.InstancedMesh(rimGeometry, this.rimMaterial, count);
    this.shadows = new THREE.InstancedMesh(shadowGeometry, this.shadowMaterial, count);
    for (const mesh of [this.shafts, this.caps, this.rims, this.shadows]) {
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      mesh.castShadow = mesh !== this.shadows;
      mesh.receiveShadow = mesh !== this.shadows;
      this.scene.add(mesh);
    }

    // One logical pin cell maps to one exact matrix cell.
    const boardW = Math.max(1, cols * this.pitch);
    const boardD = Math.max(1, rows * this.pitch);
    const boardGeometry = new THREE.BoxGeometry(boardW, 0.26, boardD);
    this.board = new THREE.Mesh(boardGeometry, this.boardMaterial);
    this.board.position.y = -0.16;
    this.board.receiveShadow = true;
    this.scene.add(this.board);

    this.resetView();
  }

  _applyLevels(values) {
    if (!this.shafts || values.length !== this.cols * this.rows) return;

    const shaftDummy = new THREE.Object3D();
    const capDummy = new THREE.Object3D();
    const rimDummy = new THREE.Object3D();
    const shadowDummy = new THREE.Object3D();
    const originX = -(this.cols * this.pitch) / 2 + this.pitch / 2;
    const originZ = -(this.rows * this.pitch) / 2 + this.pitch / 2;

    for (let i = 0; i < values.length; i++) {
      const xIndex = i % this.cols;
      const yIndex = Math.floor(i / this.cols);
      const level = Math.max(0, Number(values[i]) || 0);
      const normalized = Math.min(1, level / Math.max(1, this.levels - 1));
      const stateColor = level > 0.5 ? this._raisedColor : this._loweredColor;
      const h = this.inactiveHeight + normalized * (this.maxHeight - this.inactiveHeight);
      const x = originX + xIndex * this.pitch;
      const z = originZ + yIndex * this.pitch;

      shaftDummy.position.set(x, h / 2, z);
      shaftDummy.scale.set(1, h, 1);
      shaftDummy.rotation.set(0, 0, 0);
      shaftDummy.updateMatrix();
      this.shafts.setMatrixAt(i, shaftDummy.matrix);

      capDummy.position.set(x, h, z);
      capDummy.scale.set(1, 0.54, 1);
      capDummy.rotation.set(0, 0, 0);
      capDummy.updateMatrix();
      this.caps.setMatrixAt(i, capDummy.matrix);

      // Keep the base rim inside one cell. The old dense-display renderer used
      // a much larger expansion that visually overlapped on dense pin boards.
      rimDummy.position.set(x, 0.012, z);
      rimDummy.scale.set(1 + normalized * 0.10, 1 + normalized * 0.10, 1);
      rimDummy.rotation.set(0, 0, 0);
      rimDummy.updateMatrix();
      this.rims.setMatrixAt(i, rimDummy.matrix);

      // Neutral screen-plane shadow is the 2.5D cue; it is not semantic color.
      shadowDummy.position.set(x + normalized * 0.24, -0.018, z + normalized * 0.16);
      shadowDummy.scale.set(0.86 + normalized * 0.16, 0.86 + normalized * 0.16, 1);
      shadowDummy.rotation.set(0, 0, 0);
      shadowDummy.updateMatrix();
      this.shadows.setMatrixAt(i, shadowDummy.matrix);

      this.shafts.setColorAt(i, stateColor);
      this.caps.setColorAt(i, stateColor);
      this.rims.setColorAt(i, stateColor);
    }

    for (const mesh of [this.shafts, this.caps, this.rims, this.shadows]) {
      mesh.instanceMatrix.needsUpdate = true;
    }
    for (const mesh of [this.shafts, this.caps, this.rims]) {
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
  }

  updateFrame(frame, options = {}) {
    const animate = options.animate !== false;
    if (!this.shafts || frame.cols !== this.cols || frame.rows !== this.rows) {
      this._buildSurface(frame.cols, frame.rows);
    }
    this.levels = Math.max(2, Number(frame.levels) || 256);
    this.targetLevels = Float32Array.from(frame.pins || [], (value) => Math.max(0, Number(value) || 0));
    if (!animate) {
      this.currentLevels = this.targetLevels.slice();
      this._applyLevels(this.currentLevels);
      this.isMoving = false;
    } else {
      this.isMoving = true;
    }
    this.container.dispatchEvent(new CustomEvent("pinframe", { detail: { frame, animate } }));
  }

  retract() {
    if (!this.targetLevels.length) return;
    this.targetLevels.fill(0);
    this.isMoving = true;
  }

  replay(frame) {
    this.retract();
    window.setTimeout(() => this.updateFrame(frame), 520);
  }

  _selectPin(event) {
    if (!this.caps || !this.cols || !this.rows) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this._pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this._pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this._raycaster.setFromCamera(this._pointer, this.camera);
    const hit = this._raycaster.intersectObject(this.caps, false)[0];
    if (!hit || hit.instanceId == null) return;
    const index = hit.instanceId;
    this.container.dispatchEvent(new CustomEvent("pinselect", {
      detail: {
        index,
        x: index % this.cols,
        y: Math.floor(index / this.cols),
        level: Math.round(this.currentLevels[index] || 0),
      },
    }));
  }

  _bindControls() {
    const canvas = this.renderer.domElement;
    canvas.addEventListener("contextmenu", (event) => event.preventDefault());
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      this.zoomBy(Math.exp(event.deltaY * 0.0012));
    }, { passive: false });
    canvas.addEventListener("pointerdown", (event) => {
      if (!this.cols || !this.rows) return;
      const pan = event.shiftKey || event.button === 1 || event.button === 2;
      this._drag = {
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        startX: event.clientX,
        startY: event.clientY,
        pan,
      };
      canvas.setPointerCapture(event.pointerId);
      this.container.dataset.interacting = "true";
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!this._drag || this._drag.pointerId !== event.pointerId) return;
      const dx = event.clientX - this._drag.x;
      const dy = event.clientY - this._drag.y;
      this._drag.x = event.clientX;
      this._drag.y = event.clientY;
      if (this._drag.pan) this._panByPixels(dx, dy);
      else {
        this.yaw -= dx * 0.008;
        this.pitchAngle = THREE.MathUtils.clamp(this.pitchAngle + dy * 0.008, 0.16, 1.48);
        this.userAdjusted = true;
        this._applyCamera();
      }
    });
    const finishPointer = (event) => {
      if (!this._drag || this._drag.pointerId !== event.pointerId) return;
      const moved = Math.hypot(event.clientX - this._drag.startX, event.clientY - this._drag.startY);
      const wasPan = this._drag.pan;
      this._drag = null;
      this.container.dataset.interacting = "false";
      if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      if (moved < 5 && !wasPan && event.button === 0) this._selectPin(event);
    };
    canvas.addEventListener("pointerup", finishPointer);
    canvas.addEventListener("pointercancel", finishPointer);
    this.container.addEventListener("keydown", (event) => {
      const key = event.key;
      if (["+", "=", "-", "0", "Home", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(key)) {
        event.preventDefault();
      }
      if (key === "+" || key === "=") this.zoomBy(0.84);
      else if (key === "-") this.zoomBy(1.19);
      else if (key === "0" || key === "Home") this.fitToView({ preserveAngles: true });
      else if (key.startsWith("Arrow")) {
        if (event.shiftKey) {
          const dx = key === "ArrowLeft" ? 24 : key === "ArrowRight" ? -24 : 0;
          const dy = key === "ArrowUp" ? 24 : key === "ArrowDown" ? -24 : 0;
          this._panByPixels(dx, dy);
        } else {
          if (key === "ArrowLeft") this.yaw += 0.08;
          if (key === "ArrowRight") this.yaw -= 0.08;
          if (key === "ArrowUp") this.pitchAngle = Math.max(0.16, this.pitchAngle - 0.06);
          if (key === "ArrowDown") this.pitchAngle = Math.min(1.48, this.pitchAngle + 0.06);
          this.userAdjusted = true;
          this._applyCamera();
        }
      }
    });
  }

  _boardBounds() {
    const edge = this.radius * 1.7;
    return {
      minX: -(this.cols * this.pitch) / 2 - edge,
      maxX: (this.cols * this.pitch) / 2 + edge,
      minY: -0.35,
      maxY: this.maxHeight + edge,
      minZ: -(this.rows * this.pitch) / 2 - edge,
      maxZ: (this.rows * this.pitch) / 2 + edge,
    };
  }

  _applyCamera() {
    const horizontal = Math.cos(this.pitchAngle);
    this.camera.position.set(
      this.target.x + Math.sin(this.yaw) * horizontal * this.distance,
      this.target.y + Math.sin(this.pitchAngle) * this.distance,
      this.target.z + Math.cos(this.yaw) * horizontal * this.distance,
    );
    this.camera.up.set(0, 1, 0);
    this.camera.lookAt(this.target);
    this.camera.near = Math.max(0.05, this.distance / 1000);
    this.camera.far = Math.max(500, this.distance * 8);
    this.camera.updateProjectionMatrix();
    this.camera.updateMatrixWorld(true);
  }

  fitToView({ preserveAngles = true } = {}) {
    if (!this.cols || !this.rows) return;
    if (!preserveAngles) {
      this.yaw = this.defaultYaw;
      this.pitchAngle = this.defaultPitch;
    }
    const bounds = this._boardBounds();
    this.target.set(0, (bounds.minY + bounds.maxY) / 2, 0);
    this.distance = Math.max(this.cols, this.rows) * this.pitch * 2;
    this._applyCamera();

    const inverse = this.camera.quaternion.clone().invert();
    const verticalFov = THREE.MathUtils.degToRad(this.camera.fov);
    const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * this.camera.aspect);
    const tanX = Math.max(0.01, Math.tan(horizontalFov / 2));
    const tanY = Math.max(0.01, Math.tan(verticalFov / 2));
    let required = 1;
    for (const x of [bounds.minX, bounds.maxX]) {
      for (const y of [bounds.minY, bounds.maxY]) {
        for (const z of [bounds.minZ, bounds.maxZ]) {
          const local = new THREE.Vector3(x, y, z).sub(this.target).applyQuaternion(inverse);
          required = Math.max(required, local.z + Math.abs(local.x) / tanX, local.z + Math.abs(local.y) / tanY);
        }
      }
    }
    this.distance = required * 1.13;
    this.fitDistance = this.distance;
    this.userAdjusted = false;
    this._applyCamera();
  }

  resetView() {
    if (!this.cols || !this.rows) return;
    this.viewMode = "3d";
    this.yaw = this.defaultYaw;
    this.pitchAngle = this.defaultPitch;
    this.fitToView({ preserveAngles: true });
  }

  zoomBy(factor) {
    if (!this.cols || !this.rows || !Number.isFinite(factor)) return;
    const base = Math.max(1, this.fitDistance);
    this.distance = THREE.MathUtils.clamp(this.distance * factor, base * 0.28, base * 4.5);
    this.userAdjusted = true;
    this._applyCamera();
  }

  _panByPixels(dx, dy) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const worldHeight = 2 * this.distance * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2);
    const worldWidth = worldHeight * this.camera.aspect;
    const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0);
    const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 1);
    this.target.addScaledVector(right, -dx / rect.width * worldWidth);
    this.target.addScaledVector(up, dy / rect.height * worldHeight);
    this.userAdjusted = true;
    this._applyCamera();
  }

  // Kept for older callers. The dedicated top view is the separate 2D canvas.
  setTopView() {
    this.yaw = 0;
    this.pitchAngle = 1.47;
    this.fitToView({ preserveAngles: true });
  }

  setPerspectiveView() {
    this.resetView();
  }

  resize() {
    const width = Math.max(1, this.container.clientWidth);
    const height = Math.max(1, this.container.clientHeight);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    if (this.cols && this.rows) {
      if (!this.userAdjusted) this.fitToView({ preserveAngles: true });
      else this._applyCamera();
    }
  }

  animate() {
    const tick = (now) => {
      const dt = Math.min(0.05, Math.max(0.001, (now - this.lastTick) / 1000));
      this.lastTick = now;
      if (this.isMoving && this.currentLevels.length === this.targetLevels.length) {
        const blend = 1 - Math.exp(-this.motionSpeed * dt);
        let remaining = false;
        for (let i = 0; i < this.currentLevels.length; i++) {
          const delta = this.targetLevels[i] - this.currentLevels[i];
          if (Math.abs(delta) > 0.006) {
            this.currentLevels[i] += delta * blend;
            remaining = true;
          } else {
            this.currentLevels[i] = this.targetLevels[i];
          }
        }
        this._applyLevels(this.currentLevels);
        this.isMoving = remaining;
        this.container.dataset.motion = remaining ? "moving" : "settled";
      }
      this.renderer.render(this.scene, this.camera);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
}
