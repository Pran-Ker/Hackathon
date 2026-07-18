// Mesh viewer: Three.js + GLTFLoader for the image -> 3D component pipeline.
// Instant demo presets + a live drag-drop path that POSTs to /imagine.

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";

const PIPELINE_URL = "http://localhost:8000";

// Pre-baked demos load instantly from viewer/public/. Reliable demo path.
const PRESETS = [
  { id: "sneaker", label: "Sneaker", url: "/sneaker.glb" },
  { id: "chair", label: "Chair", url: "/chair.glb" },
  { id: "plant", label: "Plant", url: "/plant.glb" },
];
const DEFAULT_PRESET = "sneaker";

// Live-gen stage schedule (seconds). Sync endpoint gives no real progress,
// so labels advance on a realistic time estimate of the ~170s pipeline.
const STAGES = [
  { at: 0, label: "Isolating subject" },
  { at: 14, label: "Generating 3D mesh" },
  { at: 150, label: "Compiling & optimizing" },
];
const EST_SECONDS = 170;

const app = document.getElementById("app");
const presetsEl = document.getElementById("presets");
const captionEl = document.getElementById("caption");
const hintEl = document.getElementById("hint");
const dropzone = document.getElementById("dropzone");
const toastEl = document.getElementById("toast");
const historyEl = document.getElementById("history");
const historyClearEl = document.getElementById("historyClear");
const progressEl = document.getElementById("progress");
const timerEl = document.getElementById("timer");
const fillEl = document.getElementById("fill");
const stepsEl = document.getElementById("steps");

// ---- Scene ----------------------------------------------------------------
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0a0a0a);

const camera = new THREE.PerspectiveCamera(50, innerWidth / innerHeight, 0.01, 5000);
camera.position.set(0, 0, 5);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(devicePixelRatio);
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
app.appendChild(renderer.domElement);

const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

scene.add(new THREE.HemisphereLight(0xffffff, 0x333344, 0.6));
const dir = new THREE.DirectionalLight(0xffffff, 2.0);
dir.position.set(3, 5, 4);
scene.add(dir);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.autoRotate = true;
controls.autoRotateSpeed = 1.5;
controls.addEventListener("start", () => { controls.autoRotate = false; });
controls.addEventListener("end", () => { controls.autoRotate = true; });

const loader = new GLTFLoader();
// compile_glb outputs EXT_meshopt_compression; GLTFLoader needs the decoder
// registered up front or it refuses to load the compressed GLB.
loader.setMeshoptDecoder(MeshoptDecoder);
let currentModel = null;

// ---- Model loading --------------------------------------------------------
function frameModel(object) {
  const box = new THREE.Box3().setFromObject(object);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  object.position.sub(center);

  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  const fov = THREE.MathUtils.degToRad(camera.fov);
  const dist = (maxDim / 2) / Math.tan(fov / 2) * 1.6;

  camera.position.set(0, maxDim * 0.15, dist);
  camera.near = dist / 100;
  camera.far = dist * 100;
  camera.updateProjectionMatrix();
  controls.target.set(0, 0, 0);
  controls.update();
}

function disposeModel(model) {
  model.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    if (o.material) {
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      mats.forEach((m) => m.dispose());
    }
  });
}

function loadGLB(url) {
  return new Promise((resolve, reject) => {
    loader.load(
      url,
      (gltf) => {
        if (currentModel) { scene.remove(currentModel); disposeModel(currentModel); }
        currentModel = gltf.scene;
        scene.add(currentModel);
        frameModel(currentModel);
        resolve(gltf);
      },
      undefined,
      (err) => { console.error("GLB load failed:", url, err); reject(err); }
    );
  });
}

// ---- Presets --------------------------------------------------------------
function buildPresets() {
  PRESETS.forEach((p) => {
    const btn = document.createElement("button");
    btn.className = "preset";
    btn.textContent = p.label;
    btn.dataset.id = p.id;
    btn.addEventListener("click", () => selectPreset(p));
    presetsEl.appendChild(btn);
  });
}

async function selectPreset(p) {
  setActivePreset(p.id);
  setActiveHistory(null);
  hideCaption();
  try {
    await loadGLB(p.url);
  } catch {
    // Plant preset may be absent if its generation failed — hide it gracefully.
    showError(`Couldn't load ${p.label}.`);
  }
}

function setActivePreset(id) {
  [...presetsEl.children].forEach((b) => b.classList.toggle("active", b.dataset.id === id));
}

// ---- Live generation ------------------------------------------------------
let timerHandle = null;

function fmtTime(s) {
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

function buildSteps() {
  stepsEl.innerHTML = "";
  STAGES.forEach((s) => {
    const el = document.createElement("div");
    el.className = "step";
    el.innerHTML = `<span class="mark"></span><span>${s.label}</span>`;
    stepsEl.appendChild(el);
  });
}

function renderStages(elapsed) {
  let activeIdx = 0;
  for (let i = 0; i < STAGES.length; i++) if (elapsed >= STAGES[i].at) activeIdx = i;
  [...stepsEl.children].forEach((el, i) => {
    el.classList.toggle("done", i < activeIdx);
    el.classList.toggle("active", i === activeIdx);
  });
}

function startProgress() {
  buildSteps();
  progressEl.classList.add("show");
  renderer.toneMappingExposure = 0.35; // dim the previous model behind the card
  const t0 = Date.now();
  const tick = () => {
    const elapsed = Math.floor((Date.now() - t0) / 1000);
    timerEl.textContent = fmtTime(elapsed);
    // Cap the bar near the end so it never claims "done" before the model loads.
    fillEl.style.width = Math.min(96, (elapsed / EST_SECONDS) * 100).toFixed(1) + "%";
    renderStages(elapsed);
  };
  tick();
  timerHandle = setInterval(tick, 1000);
}

function stopProgress() {
  if (timerHandle) { clearInterval(timerHandle); timerHandle = null; }
  progressEl.classList.remove("show");
  renderer.toneMappingExposure = 1.0;
}

function fmtMB(bytes) { return (bytes / (1024 * 1024)).toFixed(1); }

function showCaption(data) {
  const parts = [];
  if (data.name) parts.push(`<div class="name">${data.name}</div>`);
  if (data.tags && data.tags.length) parts.push(`<div class="tags">${data.tags.slice(0, 6).join(" · ")}</div>`);
  if (data.size_before && data.size_after) {
    const pct = Math.round((1 - data.size_after / data.size_before) * 100);
    parts.push(`<div class="stat">${fmtMB(data.size_before)} MB → ${fmtMB(data.size_after)} MB (−${pct}%)</div>`);
  }
  captionEl.innerHTML = parts.join("");
  captionEl.classList.toggle("show", parts.length > 0);
}
function hideCaption() { captionEl.classList.remove("show"); }

let toastHandle = null;
function showError(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  if (toastHandle) clearTimeout(toastHandle);
  toastHandle = setTimeout(() => toastEl.classList.remove("show"), 6000);
}

async function generateFromImage(file) {
  hideCaption();
  setActivePreset(null); // no preset owns a live result
  startProgress();
  const form = new FormData();
  form.append("image", file);
  try {
    const res = await fetch(`${PIPELINE_URL}/imagine`, { method: "POST", body: form });
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON error body */ }
    if (!res.ok) throw new Error(data?.error || `Server error (HTTP ${res.status})`);
    if (!data?.glb_url) throw new Error("Response missing glb_url");

    const glbUrl = /^https?:\/\//.test(data.glb_url)
      ? data.glb_url
      : `${PIPELINE_URL}${data.glb_url.startsWith("/") ? "" : "/"}${data.glb_url}`;

    await loadGLB(glbUrl);
    stopProgress();
    showCaption(data);
    addHistory({
      id: `gen-${historySeq++}`,
      thumbUrl: URL.createObjectURL(file),
      glbUrl,
      name: data.name,
      data,
      active: true,
    });
  } catch (err) {
    console.error("Generate failed:", err);
    stopProgress();
    showError(`Generation failed: ${err.message}. Current model kept.`);
    // Previous model stays on screen; nothing is cleared.
  }
}

// ---- History sidebar ------------------------------------------------------
// Each live generation is remembered as a thumbnail (the dropped image) plus
// the resulting GLB url, so previous results can be revisited or deleted.
const history = [];

function renderHistory() {
  historyEl.classList.toggle("show", history.length > 0);
  // Wipe the thumbnails but keep the header row (first child).
  [...historyEl.querySelectorAll(".thumb")].forEach((el) => el.remove());
  history.forEach((item) => {
    const thumb = document.createElement("div");
    thumb.className = "thumb" + (item.active ? " active" : "");
    thumb.title = item.name || "Generated model";
    thumb.innerHTML = `<img src="${item.thumbUrl}" alt="${item.name || "model"}" />` +
      `<button class="del" title="Delete">&times;</button>`;
    thumb.addEventListener("click", (e) => {
      if (e.target.closest(".del")) return;
      revisitHistory(item);
    });
    thumb.querySelector(".del").addEventListener("click", (e) => {
      e.stopPropagation();
      deleteHistory(item);
    });
    historyEl.appendChild(thumb);
  });
}

function addHistory(item) {
  history.forEach((h) => (h.active = false));
  item.active = true;
  history.unshift(item);
  renderHistory();
}

function setActiveHistory(id) {
  history.forEach((h) => (h.active = h.id === id));
  renderHistory();
}

async function revisitHistory(item) {
  setActivePreset(null);
  try {
    await loadGLB(item.glbUrl);
    setActiveHistory(item.id);
    showCaption(item.data || {});
  } catch {
    showError("Couldn't reload that model.");
  }
}

function deleteHistory(item) {
  const idx = history.indexOf(item);
  if (idx === -1) return;
  history.splice(idx, 1);
  // Release the object URL for the dropped-image thumbnail to avoid leaks.
  if (item.thumbUrl.startsWith("blob:")) URL.revokeObjectURL(item.thumbUrl);
  renderHistory();
}

function clearHistory() {
  history.forEach((item) => {
    if (item.thumbUrl.startsWith("blob:")) URL.revokeObjectURL(item.thumbUrl);
  });
  history.length = 0;
  renderHistory();
}

historyClearEl.addEventListener("click", clearHistory);

let historySeq = 0;

// ---- Drag & drop ----------------------------------------------------------
let dragDepth = 0;
addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; dropzone.classList.add("active"); });
addEventListener("dragover", (e) => e.preventDefault());
addEventListener("dragleave", (e) => {
  e.preventDefault();
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) dropzone.classList.remove("active");
});
addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  dropzone.classList.remove("active");
  const file = [...(e.dataTransfer?.files || [])].find((f) => f.type.startsWith("image/"));
  if (file) generateFromImage(file);
  else showError("Please drop an image file (PNG or JPG).");
});

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});

// ---- Boot -----------------------------------------------------------------
buildPresets();
// Hide the Plant preset unless its GLB actually serves. Vite's dev server
// returns an HTML fallback (200) for missing files, so also reject text/html.
function hidePlant() { presetsEl.querySelector('[data-id="plant"]')?.setAttribute("hidden", ""); }
fetch("/plant.glb", { method: "HEAD" }).then((r) => {
  const ct = r.headers.get("content-type") || "";
  if (!r.ok || ct.includes("text/html")) hidePlant();
}).catch(hidePlant);

const boot = PRESETS.find((p) => p.id === DEFAULT_PRESET) || PRESETS[0];
selectPreset(boot);
