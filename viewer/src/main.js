// Promptable World viewer: Three.js + Spark for Gaussian splat rendering,
// click-to-edit UI that will call the pipeline's regenerate+fuse endpoint.

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { SplatMesh } from "@sparkjsdev/spark";

const app = document.getElementById("app");

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.01, 1000);
camera.position.set(0, 0, 3);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
app.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

// Demo placeholder: Spark's hosted sample splat, so the viewer has something
// real to render before reconstruct.py produces our own .ply files.
// Swap SPLAT_URL for a generated one once the pipeline is wired up.
const SPLAT_URL = "https://sparkjs.dev/assets/splats/butterfly.spz";

const splat = new SplatMesh({ url: SPLAT_URL });
splat.quaternion.set(1, 0, 0, 0);
splat.position.set(0, 0, -3);
scene.add(splat);

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});

// --- click-to-edit ---
// Raycast into the splat, capture the 3D point, prompt the user for an
// event description, POST to the pipeline (not implemented yet - this is
// the seam where reconstruct.py + fuse.py get wired in).

const promptInput = document.getElementById("prompt");
const submitButton = document.getElementById("submit");
let pendingPoint = null;

renderer.domElement.addEventListener("dblclick", (event) => {
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2(
    (event.clientX / window.innerWidth) * 2 - 1,
    -(event.clientY / window.innerHeight) * 2 + 1
  );
  raycaster.setFromCamera(pointer, camera);

  // TODO: Spark splats aren't standard raycastable meshes - use Spark's own
  // picking utility if it exposes one, or approximate with a depth read.
  const intersects = raycaster.intersectObject(splat, true);
  if (intersects.length === 0) return;

  pendingPoint = intersects[0].point;
  promptInput.disabled = false;
  submitButton.disabled = false;
  promptInput.focus();
});

const PIPELINE_URL = "http://localhost:8000";

submitButton.addEventListener("click", async () => {
  if (!pendingPoint || !promptInput.value.trim()) return;

  const point = pendingPoint;
  const prompt = promptInput.value;
  console.log("edit requested", point, prompt);

  submitButton.disabled = true;
  submitButton.textContent = "Generating...";

  try {
    const res = await fetch(`${PIPELINE_URL}/api/edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        point: { x: point.x, y: point.y, z: point.z },
        prompt,
      }),
    });
    if (!res.ok) throw new Error(`server responded ${res.status}`);
    const data = await res.json();
    console.log("edit response", data);
    showPreview(data.image_url, data.note);
  } catch (err) {
    console.error("edit request failed", err);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Regenerate";
    promptInput.value = "";
    pendingPoint = null;
  }
});

function showPreview(imageUrl, note) {
  // Not a 3D fusion yet (reconstruct.py / fuse.py are stubs) - just proves
  // the click -> server -> fal -> back-to-viewer round trip is real.
  let panel = document.getElementById("preview-panel");
  if (!panel) {
    panel = document.createElement("div");
    panel.id = "preview-panel";
    panel.style.cssText =
      "position:fixed;top:16px;right:16px;width:220px;background:#111;border:1px solid #333;" +
      "border-radius:8px;padding:8px;font:12px system-ui,sans-serif;color:#aaa;z-index:10;";
    document.body.appendChild(panel);
  }
  panel.innerHTML = `<img src="${imageUrl}" style="width:100%;border-radius:4px;display:block;margin-bottom:6px;" />${note}`;
}
