// POPFRAME hologram viewer: frozen frame plane + objects popping out on
// glowing leader lines with CSS2D HUD cards. Dev mode: ?mock=1 uses
// fixtures/scene3d.json and never touches the backend.

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { CSS2DRenderer, CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import fixture from "../fixtures/scene3d.json";

const MOCK = new URLSearchParams(location.search).get("mock") === "1";
// Served from the FastAPI box itself (deploy mounts the built viewer at /app)
// -> same origin; otherwise assume the pipeline API on :8000 of this host.
const API = window.PIPELINE_URL
  || (location.pathname.startsWith("/app") ? location.origin : `${location.protocol}//${location.hostname}:8000`);
const ACCENT = 0x37e0c8;
const PLANE_H = 2.4;
const MIN_SCAN_MS = 1250;

const $ = (id) => document.getElementById(id);
const landing = $("landing");
const dropzone = $("dropzone");
const fileInput = $("fileInput");
const dropHint = $("dropHint");
const framePreview = $("framePreview");
const previewImg = $("previewImg");
const scanline = $("scanline");
const promptInput = $("promptInput");
const scanBtn = $("scanBtn");
const ticker = $("ticker");
const landingError = $("landingError");
const emptyState = $("emptyState");
const banner = $("banner");
const hudFooter = $("hudFooter");
const sceneMeta = $("sceneMeta");
const clockEl = $("clock");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const absUrl = (u) => {
  if (!u) return null;
  if (/^(https?:|data:|blob:)/.test(u)) return u;
  if (MOCK) return u; // fixture assets are served by vite itself
  return `${API}${u.startsWith("/") ? "" : "/"}${u}`;
};

// ---- landing --------------------------------------------------------------
let pickedFile = null;
let scanStartedAt = null;

if (MOCK) {
  $("mockChip").hidden = false;
  promptInput.value = fixture.prompt;
  previewImg.src = fixture.frame_url;
  dropHint.hidden = true;
  framePreview.hidden = false;
  scanBtn.disabled = false;
}

// Deep-link a cached scene: hologram.html?scene=<id> skips the landing flow.
const DEEP_SCENE = new URLSearchParams(location.search).get("scene");
if (DEEP_SCENE && !MOCK) {
  scanStartedAt = performance.now();
  fetch(`${API}/api/scene/${encodeURIComponent(DEEP_SCENE)}`)
    .then(async (res) => {
      if (!res.ok) throw new Error(await errorDetail(res));
      return res.json();
    })
    .then((scene3d) => enterResultStage(scene3d))
    .catch((err) => {
      landing.classList.remove("hidden"); // enterResultStage may have hidden it
      hudFooter.hidden = true;
      landingError.textContent = `SCENE LOAD FAILED — ${err.message || err}`;
      landingError.hidden = false;
    });
}

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("over"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("over"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("over");
  const f = e.dataTransfer.files?.[0];
  if (f) acceptFile(f);
});
fileInput.addEventListener("change", () => { if (fileInput.files[0]) acceptFile(fileInput.files[0]); });

function acceptFile(f) {
  pickedFile = f;
  landingError.hidden = true;
  if (f.type.startsWith("video/")) {
    grabVideoFrame(f).then((url) => showPreview(url)).catch(() => showPreview(null));
  } else {
    showPreview(URL.createObjectURL(f));
  }
  scanBtn.disabled = false;
}

function showPreview(url) {
  if (url) previewImg.src = url;
  dropHint.hidden = !url;
  framePreview.hidden = !url;
}

function grabVideoFrame(file) {
  return new Promise((resolve, reject) => {
    const v = document.createElement("video");
    v.muted = true;
    v.playsInline = true;
    v.src = URL.createObjectURL(file);
    v.addEventListener("loadeddata", () => {
      v.currentTime = Math.min(0.1, v.duration || 0);
    }, { once: true });
    v.addEventListener("seeked", () => {
      const c = document.createElement("canvas");
      c.width = v.videoWidth || 640;
      c.height = v.videoHeight || 360;
      c.getContext("2d").drawImage(v, 0, 0);
      resolve(c.toDataURL("image/jpeg", 0.85));
    }, { once: true });
    v.addEventListener("error", reject, { once: true });
  });
}

// Optimistic stage timeline — /api/scene is a single blocking call, so labels
// advance on time estimates and all complete when the response lands.
const STAGES = [
  { at: 0, label: "UPLOADING FRAME" },
  { at: 1.2, label: "SAM 3 SEGMENTATION" },
  { at: 7, label: "SAM 3D ALIGN RECONSTRUCT" },
  { at: 28, label: "COMPOSING SCENE" },
];
let tickerTimer = null;

function startTicker() {
  ticker.hidden = false;
  ticker.innerHTML = "";
  const rows = STAGES.map((s) => {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<span class="t">--:--.-</span><span>${s.label}</span>`;
    ticker.appendChild(row);
    return row;
  });
  const t0 = performance.now();
  tickerTimer = setInterval(() => {
    const el = (performance.now() - t0) / 1000;
    STAGES.forEach((s, i) => {
      if (el >= s.at) {
        rows[i].querySelector(".t").textContent = stamp(s.at);
        rows[i].classList.add("active");
        if (i > 0) { rows[i - 1].classList.remove("active"); rows[i - 1].classList.add("done"); }
      }
    });
  }, 120);
}

function finishTicker() {
  clearInterval(tickerTimer);
  ticker.querySelectorAll(".row").forEach((r) => { r.classList.remove("active"); r.classList.add("done"); });
}

const stamp = (s) => {
  const m = Math.floor(s / 60);
  return `T+${String(m).padStart(2, "0")}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
};

scanBtn.addEventListener("click", async () => {
  if (!pickedFile && !MOCK) return;
  scanBtn.disabled = true;
  promptInput.disabled = true;
  scanline.hidden = false;
  landingError.hidden = true;
  scanStartedAt = performance.now();
  startTicker();

  try {
    // Even instant cache hits get a ~1.2s scan sweep so the scan reads as live.
    const [scene3d] = await Promise.all([requestScene(), sleep(MIN_SCAN_MS)]);
    finishTicker();
    await sleep(250);
    await enterResultStage(scene3d); // awaited: async load failures land in this catch
  } catch (err) {
    console.error(err);
    clearInterval(tickerTimer);
    landing.classList.remove("hidden"); // enterResultStage may have hidden it
    hudFooter.hidden = true;
    scanline.hidden = true;
    scanBtn.disabled = false;
    promptInput.disabled = false;
    landingError.textContent = `SCAN FAILED — ${err.message || err}`;
    landingError.hidden = false;
  }
});

async function requestScene() {
  // Empty prompt stays empty: the backend's auto mode (top objects by score)
  // only triggers on "", so never substitute the placeholder text.
  const prompt = promptInput.value.trim();
  if (MOCK) {
    await sleep(400);
    return structuredClone(fixture);
  }
  const form = new FormData();
  form.append("file", pickedFile, pickedFile.name);
  form.append("prompt", prompt);
  const res = await fetch(`${API}/api/scene`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

// Backend errors are {"error": message} (server.py _error); keep .detail as a
// fallback for FastAPI-shaped errors.
async function errorDetail(res) {
  let detail = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    detail = body.error || body.detail || detail;
  } catch { /* keep */ }
  return detail;
}

// ---- three scene ----------------------------------------------------------
let renderer, labelRenderer, scene, camera, controls, raycaster;
const clock = new THREE.Clock();
const holo = { objects: [], frame: null, dimT: 0, selected: null, started: false };
const gltfLoader = new GLTFLoader();
gltfLoader.setMeshoptDecoder(MeshoptDecoder);
const texLoader = new THREE.TextureLoader();

function initThree() {
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x07090d);

  camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.05, 100);
  camera.position.set(0, 0.25, 3.8);

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(innerWidth, innerHeight);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  $("stage").appendChild(renderer.domElement);

  labelRenderer = new CSS2DRenderer();
  labelRenderer.setSize(innerWidth, innerHeight);
  labelRenderer.domElement.style.position = "fixed";
  labelRenderer.domElement.style.inset = "0";
  labelRenderer.domElement.style.pointerEvents = "none";
  labelRenderer.domElement.style.zIndex = "10";
  document.body.appendChild(labelRenderer.domElement);

  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.add(new THREE.HemisphereLight(0xbfd4dd, 0x0a1016, 0.7));
  const dir = new THREE.DirectionalLight(0xffffff, 1.6);
  dir.position.set(2.5, 4, 4);
  scene.add(dir);

  const grid = new THREE.GridHelper(30, 60, 0x1a2230, 0x121a26);
  grid.position.y = -PLANE_H / 2 - 0.35;
  grid.material.transparent = true;
  grid.material.opacity = 0.35;
  scene.add(grid);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.7;
  controls.minDistance = 1.2;
  controls.maxDistance = 12;
  controls.addEventListener("start", () => { controls.autoRotate = false; }); // permanent

  raycaster = new THREE.Raycaster();
  renderer.domElement.addEventListener("pointerdown", (e) => {
    holo.downAt = [e.clientX, e.clientY];
  });
  renderer.domElement.addEventListener("pointerup", (e) => {
    if (!holo.downAt) return;
    const moved = Math.hypot(e.clientX - holo.downAt[0], e.clientY - holo.downAt[1]);
    if (moved < 6) pickAt(e.clientX, e.clientY);
    holo.downAt = null;
  });

  addEventListener("resize", () => {
    camera.aspect = innerWidth / innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
    labelRenderer.setSize(innerWidth, innerHeight);
  });

  renderer.setAnimationLoop(tick);
}

const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

function tick() {
  const t = clock.getElapsedTime();
  if (scanStartedAt != null) clockEl.textContent = stamp((performance.now() - scanStartedAt) / 1000);

  // dim the frozen frame after scan
  if (holo.frame && holo.dimT < 1) {
    holo.dimT = Math.min(1, holo.dimT + 0.012);
    const v = 1 - 0.62 * easeOutCubic(holo.dimT);
    holo.frame.material.color.setScalar(v);
  }

  for (const h of holo.objects) {
    const k = easeOutCubic(Math.min(1, Math.max(0, (t - h.popAt) / h.popDur)));
    h.group.position.lerpVectors(h.from, h.to, k);
    const s = h.baseScale * (0.55 + 0.45 * k);
    h.group.scale.setScalar(s);
    if (k >= 1) {
      h.group.position.y = h.to.y + Math.sin(t * 1.1 + h.phase) * 0.018; // idle float
      if (!h.shown) { h.shown = true; h.card.classList.add("shown"); }
    }
    h.line.geometry.attributes.position.setXYZ(0, h.anchor.x, h.anchor.y, h.anchor.z);
    h.line.geometry.attributes.position.setXYZ(1, h.group.position.x, h.group.position.y, h.group.position.z);
    h.line.geometry.attributes.position.needsUpdate = true;

    const pulse = holo.selected === h ? 0.35 + 0.25 * Math.sin(t * 6) : 0;
    const shimmer = h.shimmerUntil && t < h.shimmerUntil ? 0.5 + 0.4 * Math.sin(t * 24) : 0;
    setEmissive(h, Math.max(pulse, shimmer));
    h.dot.material.opacity = 0.55 + 0.45 * Math.sin(t * 3 + h.phase);
  }

  controls.update();
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
}

function setEmissive(h, intensity) {
  h.group.traverse((o) => {
    if (o.isMesh && o.material && "emissive" in o.material) {
      o.material.emissive.setHex(ACCENT);
      o.material.emissiveIntensity = intensity;
    }
  });
}

// ---- result stage ---------------------------------------------------------
async function enterResultStage(scene3d) {
  initThree();

  const nObj = scene3d.objects.length;
  const total = scene3d.timings?.total ?? scene3d.timings?.total_s;
  sceneMeta.textContent =
    `${nObj} OBJECT${nObj === 1 ? "" : "S"} · "${scene3d.prompt}"` +
    (scene3d.cached ? " · CACHED" : "") +
    (total ? ` · ${total.toFixed(1)}s` : "");

  if (scene3d.error) showBanner(`PARTIAL SCENE — ${scene3d.error}`);

  // Start the (large, uncompressed) combined scene GLB streaming NOW so it
  // overlaps the frame load; progress surfaces in the banner below.
  const glbPromise = scene3d.scene_glb_url ? loadSceneGlb(absUrl(scene3d.scene_glb_url)) : null;
  glbPromise?.catch(() => {}); // handled at the await site; silence early unhandledrejection

  const frame = await loadFramePlane(absUrl(scene3d.frame_url));
  // Landing hides only after the frame is actually up — a failed load rejects
  // into the caller's catch with the landing (and its error box) still visible.
  landing.classList.add("hidden");
  hudFooter.hidden = false;
  scene.add(frame.mesh);
  holo.frame = frame.mesh;

  // Back the camera out until the whole frame fits (wide/panoramic frames).
  const halfV = THREE.MathUtils.degToRad(camera.fov / 2);
  const dist = Math.max(
    3.8,
    1.12 * Math.max((frame.h / 2) / Math.tan(halfV), (frame.w / 2) / (Math.tan(halfV) * camera.aspect)),
  );
  camera.position.set(0, 0.25, dist);
  controls.maxDistance = Math.max(12, dist * 2.5);
  controls.update();

  if (nObj === 0) {
    emptyState.hidden = false;
    return;
  }

  // Prefer one shared scene glb; fall back to per-object glbs.
  let sceneNodes = null;
  if (glbPromise) {
    try {
      const gltf = await glbPromise;
      sceneNodes = mapSceneNodes(gltf.scene, scene3d.objects);
    } catch (e) {
      console.warn("scene glb failed, falling back to per-object glbs", e);
    }
    if (banner.textContent.startsWith("LOADING SCENE MESH")) banner.hidden = true;
  }

  const fit = computeSceneFit(scene3d.objects, frame, dist);

  await Promise.all(scene3d.objects.map(async (obj, i) => {
    try {
      let content = sceneNodes?.get(obj.id) || null;
      if (!content && obj.glb_url) {
        const gltf = await gltfLoader.loadAsync(absUrl(obj.glb_url));
        content = gltf.scene;
      }
      if (!content && obj.cutout_url) content = await makeCutoutSprite(absUrl(obj.cutout_url));
      if (!content) { console.warn("object has no renderable content, skipping", obj.label); return; }
      spawnObject(obj, i, nObj, content, frame, fit);
    } catch (e) {
      console.warn(`object ${obj.id} (${obj.label}) failed to load`, e);
    }
  }));
}

// gltfLoader.load with download progress surfaced in the banner — the combined
// Align GLB is served uncompressed and can be tens of MB, so the stage must
// not read as frozen while it streams.
function loadSceneGlb(url) {
  return new Promise((resolve, reject) => {
    gltfLoader.load(url, resolve, (e) => {
      if (e.total) {
        banner.textContent = `LOADING SCENE MESH — ${Math.round((100 * e.loaded) / e.total)}%`;
        banner.hidden = false;
      }
    }, reject);
  });
}

function loadFramePlane(url) {
  return new Promise((resolve, reject) => {
    texLoader.load(url, (tex) => {
      tex.colorSpace = THREE.SRGBColorSpace;
      const iw = tex.image.width, ih = tex.image.height;
      const w = (iw / ih) * PLANE_H;
      const mesh = new THREE.Mesh(
        new THREE.PlaneGeometry(w, PLANE_H),
        new THREE.MeshBasicMaterial({ map: tex })
      );
      mesh.name = "frame";
      resolve({ mesh, w, h: PLANE_H, iw, ih });
    }, undefined, reject);
  });
}

function mapSceneNodes(root, objects) {
  // SAM 3D Align combined GLB names nodes object_00/object_01/… under a
  // "world" wrapper; per-object order == mask order == objects[] order.
  const map = new Map();
  objects.forEach((o, i) => {
    const node =
      root.getObjectByName(o.id) ||
      root.getObjectByName(`object_${String(i).padStart(2, "0")}`) ||
      root.getObjectByName(`object_${i}`);
    if (node) map.set(o.id, node);
  });
  if (map.size === 0 && root.children.length) {
    let top = root;
    while (top.children.length === 1 && !top.children[0].isMesh) top = top.children[0];
    [...top.children].forEach((child, i) => {
      if (objects[i]) map.set(objects[i].id, child);
    });
  }
  map.forEach((node) => {
    node.removeFromParent();
    // The combined GLB carries IDENTITY node transforms (all meshes
    // unit-normalized, overlapping at origin). True placement arrives in
    // obj.transform (Align metadata) and is applied by spawnObject — never
    // trust the GLB's own node TRS.
    node.position.set(0, 0, 0);
    node.quaternion.identity();
    node.scale.set(1, 1, 1);
  });
  return map;
}

// ---- Align metadata transforms ---------------------------------------------
// obj.transform is the backend-converted SAM 3D Align placement (three.js
// convention: +y up, camera at origin looking down -z; rotation xyzw quat).
// All objects share one camera frame, so a single uniform fit (scale k +
// offset) maps the whole arrangement in front of the frozen frame while
// preserving every relative position/size/depth.
// world-units footprint of the object's bbox on the frame plane
function worldSizeFor(obj, frame) {
  if (!obj.bbox) return 0.6;
  return Math.max((obj.bbox[2] / frame.iw) * frame.w, (obj.bbox[3] / frame.ih) * frame.h);
}

function computeSceneFit(objects, frame, camDist) {
  const withT = objects.filter((o) => o.transform?.position);
  if (!withT.length) return null;
  const min = new THREE.Vector3(Infinity, Infinity, Infinity);
  const max = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
  let maxSize = 0;
  const ratios = [];
  for (const o of withT) {
    const p = new THREE.Vector3().fromArray(o.transform.position);
    const s = Math.max(o.transform.scale?.[0] ?? 1, 1e-3);
    min.min(p.clone().subScalar(s / 2));
    max.max(p.clone().addScalar(s / 2));
    maxSize = Math.max(maxSize, s);
    // scale that makes this object read as big as its bbox does in the photo
    ratios.push(worldSizeFor(o, frame) / s);
  }
  ratios.sort((a, b) => a - b);
  const kSize = 0.85 * ratios[Math.floor(ratios.length / 2)]; // median, robust to one bad bbox
  const span = max.clone().sub(min);
  const k = Math.min(
    kSize,
    (frame.w * 1.05) / Math.max(span.x, 1e-3),        // stay within the frame width
    (frame.h * 0.95) / Math.max(span.y, 1e-3),        // …and height
    (camDist * 0.4) / Math.max(span.z, 1e-3),         // depth budget scales with view distance
    (frame.h * 0.9) / Math.max(maxSize, 1e-3),        // no object dwarfs the stage
  );
  const center = min.clone().add(max).multiplyScalar(0.5 * k);
  // x/y centered on the frame; nearest face pops ~0.35 in front of it
  const offset = new THREE.Vector3(-center.x, -center.y, 0.35 - min.z * k);
  return { k, offset };
}

function transformTarget(obj, fit) {
  return new THREE.Vector3().fromArray(obj.transform.position).multiplyScalar(fit.k).add(fit.offset);
}

async function makeCutoutSprite(url) {
  const tex = await texLoader.loadAsync(url);
  tex.colorSpace = THREE.SRGBColorSpace;
  const aspect = tex.image.width / tex.image.height;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true }));
  sprite.scale.set(aspect, 1, 1);
  sprite.userData.isCutout = true;
  return sprite;
}

function anchorFor(obj, i, n, frame) {
  if (obj.bbox) {
    const [x, y, w, h] = obj.bbox;
    return new THREE.Vector3(
      ((x + w / 2) / frame.iw - 0.5) * frame.w,
      (0.5 - (y + h / 2) / frame.ih) * frame.h,
      0.01
    );
  }
  return new THREE.Vector3((i - (n - 1) / 2) * (frame.w / Math.max(n, 1)) * 0.8, 0, 0.01);
}

function targetSizeFor(obj, frame) {
  if (!obj.bbox) return 0.6;
  const rel = Math.max(obj.bbox[2] / frame.iw, obj.bbox[3] / frame.ih);
  return THREE.MathUtils.clamp(rel * frame.w * 0.9, 0.35, 0.95);
}

function normalizeContent(content, targetSize) {
  if (content.userData.isCutout) {
    const a = content.scale.x / content.scale.y;
    content.scale.set(targetSize * a, targetSize, 1);
    return 1; // sprites carry their own scale; group baseScale stays 1
  }
  const box = new THREE.Box3().setFromObject(content);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  content.position.sub(center);
  return targetSize / (Math.max(size.x, size.y, size.z) || 1);
}

function spawnObject(obj, i, n, content, frame, fit) {
  const anchor = anchorFor(obj, i, n, frame);
  const hasTransform = Boolean(fit && obj.transform?.position);

  const inner = new THREE.Group();
  inner.add(content);

  let baseScale, targetSize, to;
  if (hasTransform) {
    // True Align placement: rotation/scale on inner (canonical -> camera),
    // fitted world position as the pop-out target. Meshes arrive
    // unit-normalized at origin; recenter defensively (no-op when centered).
    const s = obj.transform.scale?.[0] ?? 1;
    if (content.userData.isCutout) {
      const a = content.scale.x / content.scale.y;
      content.scale.set(s * a, s, 1);
    } else {
      // Align meshes are ~unit-normalized already; dividing by maxDim makes
      // this exact for them and safe for any other mesh.
      const box = new THREE.Box3().setFromObject(content);
      const dim = box.getSize(new THREE.Vector3());
      const maxDim = Math.max(dim.x, dim.y, dim.z) || 1;
      content.position.sub(box.getCenter(new THREE.Vector3()));
      if (obj.transform.rotation?.length === 4) inner.quaternion.fromArray(obj.transform.rotation);
      inner.scale.setScalar(s / maxDim);
    }
    baseScale = fit.k;
    targetSize = s * fit.k; // world footprint, for card offset + raycast pad
    to = transformTarget(obj, fit);
  } else {
    targetSize = targetSizeFor(obj, frame);
    baseScale = content.userData.isCutout ? 1 : normalizeContent(content, targetSize);
    const popZ = 0.55 + (i % 4) * 0.08;
    to = anchor.clone();
    to.z = Math.min(popZ, 0.8);
    to.x *= 1.12; // fan slightly outward so objects don't crowd center
    to.y *= 1.12;
  }

  const group = new THREE.Group();
  group.name = obj.id;
  group.userData.objectId = obj.id;
  group.add(inner);
  group.position.copy(anchor);
  scene.add(group);

  // leader line, additive accent
  const lineGeo = new THREE.BufferGeometry().setFromPoints([anchor, anchor]);
  const line = new THREE.Line(lineGeo, new THREE.LineBasicMaterial({
    color: ACCENT, transparent: true, opacity: 0.75,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  scene.add(line);

  // anchor dot on the frame
  const dot = new THREE.Mesh(
    new THREE.CircleGeometry(0.016, 24),
    new THREE.MeshBasicMaterial({
      color: ACCENT, transparent: true,
      blending: THREE.AdditiveBlending, depthWrite: false,
    })
  );
  dot.position.copy(anchor);
  scene.add(dot);

  const h = {
    obj, group, inner, line, dot, anchor, baseScale,
    from: anchor.clone(), to,
    popAt: clock.getElapsedTime() + 0.25 + i * 0.16,
    popDur: 0.9,
    phase: i * 1.7,
    shown: false,
    visible: true,
    shimmerUntil: 0,
  };
  h.card = makeCard(h);
  const side = anchor.x >= 0 ? 1 : -1;
  const label = new CSS2DObject(h.card);
  if (hasTransform) {
    // group is scaled by fit.k, so express the offset in group-local units
    const s = obj.transform.scale?.[0] ?? 1;
    label.position.set(side * (s / 2 + 0.28 / fit.k), s * 0.25, 0);
  } else {
    label.position.set(side * (targetSize / 2 + 0.28), targetSize * 0.25, 0);
  }
  group.add(label);
  h.label = label;
  holo.objects.push(h);
}

// ---- HUD cards ------------------------------------------------------------
function makeCard(h) {
  const { obj } = h;
  const el = document.createElement("div");
  el.className = "holo-card";
  const name = obj.info?.name || obj.label || obj.id;
  const score = obj.score != null ? obj.score.toFixed(2) : "--";
  const tags = (obj.info?.tags || []).map((t) => `<span class="hc-tag">${escapeHtml(t)}</span>`).join("");
  const facts = (obj.info?.facts || []).map((f) => `<div>· ${escapeHtml(f)}</div>`).join("");
  el.innerHTML = `
    <div class="hc-head">
      <span class="hc-name">${escapeHtml(name)}</span>
      <span class="hc-score">${score}</span>
    </div>
    ${tags ? `<div class="hc-tags">${tags}</div>` : ""}
    <div class="hc-stats">${meshStatsText(h)}</div>
    ${facts ? `<div class="hc-facts">${facts}</div>` : ""}
    <div class="hc-actions">
      <button class="hc-vis">HIDE</button>
      ${obj.cutout_url ? `<button class="hc-hero">HERO UPGRADE</button>` : ""}
    </div>`;

  el.addEventListener("click", (e) => {
    if (e.target.closest("button")) return;
    select(h);
  });
  el.querySelector(".hc-vis").addEventListener("click", () => toggleVisible(h));
  el.querySelector(".hc-hero")?.addEventListener("click", () => heroUpgrade(h));
  return el;
}

function meshStatsText(h) {
  let tris = 0, meshes = 0;
  h.group?.traverse?.((o) => {
    if (o.isMesh && o.geometry) {
      meshes++;
      const idx = o.geometry.index;
      tris += (idx ? idx.count : o.geometry.attributes.position?.count || 0) / 3;
    }
  });
  if (!meshes) return h.obj.glb_url === null ? "2D CUTOUT — NO MESH" : "";
  return `${meshes} MESH · ${Math.round(tris).toLocaleString()} TRIS`;
}

function refreshStats(h) {
  const statsEl = h.card.querySelector(".hc-stats");
  if (statsEl) statsEl.textContent = meshStatsText(h);
}

function select(h) {
  if (holo.selected === h) { deselect(); return; }
  deselect();
  holo.selected = h;
  h.card.classList.add("selected");
}

function deselect() {
  if (!holo.selected) return;
  setEmissive(holo.selected, 0);
  holo.selected.card.classList.remove("selected");
  holo.selected = null;
}

function toggleVisible(h) {
  h.visible = !h.visible;
  h.inner.visible = h.visible;
  h.line.visible = h.visible;
  h.dot.visible = h.visible;
  h.card.classList.toggle("ghost", !h.visible);
  h.card.querySelector(".hc-vis").textContent = h.visible ? "HIDE" : "SHOW";
}

function pickAt(cx, cy) {
  const ndc = new THREE.Vector2((cx / innerWidth) * 2 - 1, -(cy / innerHeight) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  const hits = raycaster.intersectObjects(holo.objects.filter((h) => h.visible).map((h) => h.group), true);
  if (!hits.length) { deselect(); return; }
  let node = hits[0].object;
  while (node && !node.userData.objectId) node = node.parent;
  const h = holo.objects.find((o) => o.group === node);
  if (h) select(h);
}

// ---- hero upgrade ---------------------------------------------------------
async function heroUpgrade(h) {
  const btn = h.card.querySelector(".hc-hero");
  if (!btn || btn.classList.contains("busy")) return;
  btn.classList.add("busy");
  btn.textContent = "UPGRADING…";
  try {
    let heroGlbUrl;
    if (MOCK) {
      await sleep(1500); // fake pipeline latency, then swap in a local glb
      heroGlbUrl = "/sample.glb";
    } else {
      const cutout = await fetch(absUrl(h.obj.cutout_url));
      if (!cutout.ok) throw new Error(`cutout fetch HTTP ${cutout.status}`);
      const blob = await cutout.blob();
      const form = new FormData();
      form.append("image", blob, `${h.obj.id}_cutout.png`);
      const res = await fetch(`${API}/imagine`, { method: "POST", body: form });
      if (!res.ok) throw new Error(`imagine HTTP ${res.status}`);
      const data = await res.json();
      if (!data.glb_url) throw new Error("no glb_url in /imagine response");
      heroGlbUrl = data.glb_url;
    }

    const gltf = await gltfLoader.loadAsync(absUrl(heroGlbUrl));
    const content = gltf.scene;
    // hot-swap in place: keep the group position and current world footprint.
    // sizeOf measures WORLD size, so reset any Align-metadata rotation/scale
    // on inner first (the hero mesh has its own canonical orientation).
    const keepSize = THREE.MathUtils.clamp(sizeOf(h), 0.3, 1.1);
    h.inner.clear();
    h.inner.quaternion.identity();
    h.inner.scale.setScalar(1);
    h.inner.add(content);
    h.baseScale = normalizeContent(content, keepSize);
    h.group.scale.setScalar(h.baseScale);
    h.shimmerUntil = clock.getElapsedTime() + 1.4;
    refreshStats(h);
    btn.textContent = "HERO ✓";
  } catch (e) {
    console.warn("hero upgrade failed", e);
    btn.textContent = "UPGRADE FAILED";
    setTimeout(() => { btn.textContent = "HERO UPGRADE"; btn.classList.remove("busy"); }, 2200);
    return;
  }
  btn.classList.remove("busy");
}

function sizeOf(h) {
  // current world size of the inner content ≈ its normalized target size
  const box = new THREE.Box3().setFromObject(h.inner);
  const s = box.getSize(new THREE.Vector3());
  const m = Math.max(s.x, s.y, s.z);
  return Number.isFinite(m) && m > 0 ? m : 0.6;
}

// ---- misc UI --------------------------------------------------------------
function showBanner(text) {
  banner.textContent = text;
  banner.hidden = false;
  setTimeout(() => { banner.hidden = true; }, 8000);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

$("retryBtn").addEventListener("click", () => location.reload());
$("rescanBtn").addEventListener("click", () => location.reload());
