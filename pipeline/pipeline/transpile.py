"""Stage 3b (deterministic transpile): GLB -> NATIVE Three.js component.

Unlike compile_glb.py (which emits a react-three-fiber wrapper that *loads* the
GLB via GLTFLoader at runtime), this produces a self-contained vanilla Three.js
module whose geometry is *constructed in code*:

    import * as THREE from 'three'
    export function createModel(): THREE.Mesh

No GLTFLoader, no React, no external .glb. The mesh's vertex data is inlined as
base64 typed arrays; per-vertex colors are sampled from the baseColor texture so
the model keeps its color with zero texture files (vertexColors: true).

Pipeline:
  1. gltf-transform `simplify` (meshoptimizer, via npx) decimates the raw GLB to
     ~target_faces, keeping it uncompressed so trimesh can read it.
  2. trimesh loads it; positions/faces come straight from the mesh, per-vertex
     RGB via `visual.to_color()` (samples the texture through the UVs).
  3. Geometry is centered + unit-scaled, positions quantized to uint16, and the
     whole thing emitted as one .js module. Normals are recomputed in-browser
     (geometry.computeVertexNormals()) to save bytes.

Deterministic, no LLM. Requires: node/npx (offline-cached ok), trimesh, numpy,
pillow.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np


def _simplify_glb(src: Path, dst: Path, ratio: float) -> bool:
    """Decimate `src` -> `dst` via gltf-transform simplify (meshoptimizer).

    ratio is the fraction of triangles to KEEP. Returns True on success; on any
    failure the caller should fall back to the undecimated mesh.
    """
    if shutil.which("npx") is None:
        return False
    try:
        subprocess.run(
            [
                "npx", "--offline", "@gltf-transform/cli", "simplify",
                str(src), str(dst),
                "--ratio", f"{ratio:.4f}",
                "--error", "0.001",
            ],
            capture_output=True, text=True, check=True,
        )
        return dst.is_file()
    except Exception:
        # Retry without --offline in case the cache lacks it but network is up.
        try:
            subprocess.run(
                ["npx", "@gltf-transform/cli", "simplify", str(src), str(dst),
                 "--ratio", f"{ratio:.4f}", "--error", "0.001"],
                capture_output=True, text=True, check=True,
            )
            return dst.is_file()
        except Exception:
            return False


def _load_mesh(glb: Path):
    """Load a GLB into a single concatenated trimesh mesh (transforms baked)."""
    import trimesh

    scene = trimesh.load(str(glb), process=False)
    if isinstance(scene, trimesh.Scene):
        mesh = scene.to_geometry() if hasattr(scene, "to_geometry") else scene.dump(concatenate=True)
    else:
        mesh = scene
    return mesh


def _vertex_colors(mesh) -> np.ndarray:
    """(N,3) uint8 RGB for every vertex.

    Handles all trimesh visual types: TextureVisuals (sample the baseColor
    texture through the UVs), ColorVisuals (already per-vertex, e.g. an
    untextured/geometry-only mesh), or none (flat mid-grey fallback).
    """
    n = len(mesh.vertices)
    vis = mesh.visual
    vc = None
    try:
        if hasattr(vis, "to_color"):  # TextureVisuals
            vc = np.asarray(vis.to_color().vertex_colors)
        elif getattr(vis, "vertex_colors", None) is not None:  # ColorVisuals
            vc = np.asarray(vis.vertex_colors)
    except Exception:
        vc = None
    if vc is None or vc.ndim != 2 or vc.shape[0] != n:
        vc = np.full((n, 4), 200, dtype=np.uint8)  # neutral grey
    return vc[:, :3].astype(np.uint8)


def _b64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")


_JS_TEMPLATE = """\
// Auto-generated NATIVE Three.js component (deterministic GLB transpile).
// Geometry is constructed in-code from inlined data - no GLTFLoader, no assets.
//   import {{ createModel }} from './{name}.js'
//   scene.add(createModel());
import * as THREE from 'three';

const _d = (s) => {{ const b = atob(s), n = b.length, u = new Uint8Array(n);
  for (let i = 0; i < n; i++) u[i] = b.charCodeAt(i); return u; }};

const POS_Q = new Uint16Array(_d('{pos_b64}').buffer);   // quantized positions
const COL   = _d('{col_b64}');                            // per-vertex RGB (uint8)
const IDX   = new {idx_type}(_d('{idx_b64}').buffer);     // triangle index
const QMIN   = {qmin};   // dequant: p = q/65535 * QSCALE + QMIN
const QSCALE = {qscale};

export function createModel() {{
  const n = POS_Q.length / 3;
  const pos = new Float32Array(POS_Q.length);
  for (let i = 0; i < n; i++)
    for (let k = 0; k < 3; k++)
      pos[i * 3 + k] = (POS_Q[i * 3 + k] / 65535) * QSCALE[k] + QMIN[k];

  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('color', new THREE.BufferAttribute(COL, 3, true));
  g.setIndex(new THREE.BufferAttribute(IDX, 1));
  g.computeVertexNormals();

  const m = new THREE.MeshStandardMaterial({{
    vertexColors: true, metalness: 0, roughness: 1, side: THREE.DoubleSide,
  }});
  return new THREE.Mesh(g, m);
}}

export default createModel;
"""


def transpile_glb_to_component(
    glb_path,
    out_js_path,
    target_faces: int = 40000,
    name: str = "Model",
) -> Path:
    """Transpile a GLB into a self-contained native Three.js module.

    Returns the written .js path. Falls back to the undecimated mesh if
    gltf-transform simplify is unavailable.
    """
    glb_path = Path(glb_path).resolve()
    out_js_path = Path(out_js_path)
    out_js_path.parent.mkdir(parents=True, exist_ok=True)

    # Quick load to get the current face count so we can target a ratio.
    base_mesh = _load_mesh(glb_path)
    n_faces = len(base_mesh.faces)

    mesh = base_mesh
    if n_faces > target_faces:
        ratio = max(0.01, min(1.0, target_faces / n_faces))
        with tempfile.TemporaryDirectory() as td:
            simplified = Path(td) / "simplified.glb"
            if _simplify_glb(glb_path, simplified, ratio):
                mesh = _load_mesh(simplified)

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    colors = _vertex_colors(mesh)

    # Center at origin and scale so the largest dimension == 1 (drop-in sane).
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    center = (lo + hi) / 2.0
    scale = float((hi - lo).max()) or 1.0
    verts = (verts - center) / scale

    # Quantize positions to uint16 over their (post-normalize) bbox.
    qlo, qhi = verts.min(axis=0), verts.max(axis=0)
    qscale = (qhi - qlo)
    qscale[qscale == 0] = 1.0
    pos_q = np.round((verts - qlo) / qscale * 65535.0).astype(np.uint16)

    n_verts = len(verts)
    if n_verts <= 65535:
        idx = faces.astype(np.uint16)
        idx_type = "Uint16Array"
    else:
        idx = faces.astype(np.uint32)
        idx_type = "Uint32Array"

    js = _JS_TEMPLATE.format(
        name=name,
        pos_b64=_b64(pos_q),
        col_b64=_b64(colors),
        idx_b64=_b64(idx.ravel()),
        idx_type=idx_type,
        qmin="[" + ", ".join(f"{x:.6f}" for x in qlo) + "]",
        qscale="[" + ", ".join(f"{x:.6f}" for x in qscale) + "]",
    )
    out_js_path.write_text(js)
    return out_js_path


if __name__ == "__main__":
    import sys

    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".native.js")
    tf = int(sys.argv[3]) if len(sys.argv) > 3 else 40000
    out = transpile_glb_to_component(src, dst, target_faces=tf)
    size_kb = out.stat().st_size / 1024
    print(f"wrote {out} ({size_kb:.0f} KB) at target_faces={tf}")
