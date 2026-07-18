"""Stage 6 (export bundle): compiled asset -> drop-in r3f zip.

Packages an optimized GLB + generated <Model/> component into a single zip a
developer can drop into a react-three-fiber project. Stdlib only: no new deps.

Contents:
  model.glb   - the optimized GLB under a stable name
  Model.tsx   - the component, its useGLTF() path rewritten to /model.glb
  README.md   - what it is, install deps, minimal <Canvas><Model/></Canvas> usage
  preview.png - optional, if meta["thumbnail"] points at an image
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

GLB_MAGIC = b"glTF"
MODEL_GLB = "model.glb"
COMPONENT = "Model.tsx"
BUNDLED_PATH = "/model.glb"

INSTALL_CMD = "npm i three @react-three/fiber @react-three/drei three-stdlib"

# useGLTF('...') / useGLTF.preload('...') with the glb path in quotes.
_GLTF_ARG = re.compile(
    r"""(useGLTF(?:\.preload)?\s*\(\s*)(['"])(?P<path>[^'"]*?\.glb)(['"])""",
)

# gltfjsx emits `import { GLTF } from 'three-stdlib'` as a value import, but GLTF
# is a type-only export there -> value-import bundlers (vite/rolldown) fail. Make
# it a type import so the component is genuinely drop-in.
_GLTF_TYPE_IMPORT = re.compile(
    r"""import\s+\{\s*GLTF\s*\}\s+from\s+(['"])three-stdlib\1""",
)


def _is_glb(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(4) == GLB_MAGIC


def _rewrite_component(src: str) -> str:
    """Point useGLTF() at the bundled model and type-only the GLTF import."""
    src = _GLTF_ARG.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{BUNDLED_PATH}{m.group(4)}",
        src,
    )
    src = _GLTF_TYPE_IMPORT.sub(
        lambda m: f"import type {{ GLTF }} from {m.group(1)}three-stdlib{m.group(1)}",
        src,
    )
    return src


def _readme(meta: dict | None) -> str:
    name = (meta or {}).get("name")
    tags = (meta or {}).get("tags") or []
    tris = (meta or {}).get("triangles") or (meta or {}).get("polys")

    title = name or "3D Model Bundle"
    lines = [
        f"# {title}",
        "",
        "A drop-in [react-three-fiber](https://github.com/pmndrs/react-three-fiber) "
        "asset bundle: an optimized glTF-binary model plus a typed `<Model/>` "
        "component generated from it.",
        "",
    ]

    meta_lines = []
    if name:
        meta_lines.append(f"- **Name:** {name}")
    if tags:
        meta_lines.append(f"- **Tags:** {', '.join(str(t) for t in tags)}")
    if tris:
        meta_lines.append(f"- **Triangles:** {tris}")
    if meta_lines:
        lines += ["## Asset", "", *meta_lines, ""]

    lines += [
        "## Contents",
        "",
        f"- `{MODEL_GLB}` - the optimized model (glTF-binary).",
        f"- `{COMPONENT}` - typed r3f component; loads `{BUNDLED_PATH}`.",
        "",
        "## Install",
        "",
        "```bash",
        INSTALL_CMD,
        "```",
        "",
        "## Usage",
        "",
        f"Copy `{COMPONENT}` into your source tree and `{MODEL_GLB}` into your "
        "app's `public/` directory (so it is served at `/model.glb`), then:",
        "",
        "```tsx",
        "import { Canvas } from '@react-three/fiber'",
        "import { Model } from './Model'",
        "",
        "export default function App() {",
        "  return (",
        "    <Canvas>",
        "      <ambientLight />",
        "      <Model />",
        "    </Canvas>",
        "  )",
        "}",
        "```",
        "",
    ]
    return "\n".join(lines)


def export_bundle(optimized_glb, component, out_zip, meta=None) -> Path:
    """Assemble a drop-in r3f zip bundle.

    Args:
        optimized_glb: path to the optimized .glb.
        component:     path to the generated Model.tsx.
        out_zip:       path to write the .zip.
        meta:          optional dict; keys used: name, tags, triangles/polys,
                       thumbnail (path to a preview image).

    Returns:
        Path to the written zip.
    """
    optimized_glb = Path(optimized_glb).resolve()
    component = Path(component).resolve()
    out_zip = Path(out_zip).resolve()

    if not optimized_glb.is_file():
        raise FileNotFoundError(optimized_glb)
    if not _is_glb(optimized_glb):
        raise ValueError(f"not a glTF-binary file (bad magic): {optimized_glb}")
    if not component.is_file():
        raise FileNotFoundError(component)

    tsx = _rewrite_component(component.read_text(encoding="utf-8"))
    readme = _readme(meta)

    thumb = (meta or {}).get("thumbnail")
    thumb = Path(thumb).resolve() if thumb else None

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(optimized_glb, MODEL_GLB)
        zf.writestr(COMPONENT, tsx)
        zf.writestr("README.md", readme)
        if thumb and thumb.is_file():
            zf.write(thumb, "preview.png")

    return out_zip


if __name__ == "__main__":
    import sys

    glb = Path(sys.argv[1])
    comp = Path(sys.argv[2])
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("outputs/bundle.zip")
    print(export_bundle(glb, comp, out))
