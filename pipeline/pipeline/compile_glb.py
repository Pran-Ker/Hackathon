"""Stage 3 (deterministic compile): GLB -> reusable react-three-fiber bundle.

No LLM here - pure tooling. Two node CLIs, shelled out via npx so we never
touch viewer/package.json:

  1. gltf-transform (@gltf-transform/cli): dedup + prune + resize textures
     (max 1024) + meshopt compression -> an optimized .glb.
  2. gltfjsx: generate a typed r3f <Model/> component (--types) that
     useGLTF()s the optimized .glb.

Missing tools auto-install because we always invoke `npx -y <tool>`.
stderr is captured and surfaced on failure.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

GLB_MAGIC = b"glTF"
TEXTURE_MAX = 1024


def _run_npx(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run `npx -y <args>`; on failure raise with captured stderr surfaced."""
    if shutil.which("npx") is None:
        raise RuntimeError("npx not found on PATH - Node.js is required for compile_glb")
    cmd = ["npx", "-y", *args]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    return proc


def _is_glb(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(4) == GLB_MAGIC


def _fix_gltf_type_import(component: Path) -> None:
    """gltfjsx emits `import { GLTF } from 'three-stdlib'` as a value import, but
    GLTF is type-only in current three-stdlib and modern Vite/rolldown rejects it.
    Rewrite to a type-only import so the raw component is drop-in valid."""
    text = component.read_text()
    fixed = text.replace(
        "import { GLTF } from 'three-stdlib'",
        "import type { GLTF } from 'three-stdlib'",
    )
    if fixed != text:
        component.write_text(fixed)


def compile_glb(glb_path, out_dir) -> dict:
    """Optimize a GLB and generate a typed r3f <Model/> component.

    Args:
        glb_path: path to the source .glb
        out_dir:  directory for the optimized .glb + generated component

    Returns:
        {"optimized_glb": Path, "component": Path,
         "size_before": int, "size_after": int}
    """
    glb_path = Path(glb_path).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not glb_path.is_file():
        raise FileNotFoundError(glb_path)
    if not _is_glb(glb_path):
        raise ValueError(f"not a glTF-binary file (bad magic): {glb_path}")

    size_before = glb_path.stat().st_size
    stem = glb_path.stem
    optimized_glb = out_dir / f"{stem}.optimized.glb"

    # gltf-transform's `optimize` bundles dedup+prune+resize+compress. Pin
    # meshopt (draco needs a separate encoder module) and cap textures at 1024.
    _run_npx([
        "@gltf-transform/cli", "optimize",
        str(glb_path), str(optimized_glb),
        "--compress", "meshopt",
        "--texture-size", str(TEXTURE_MAX),
    ])

    if not optimized_glb.is_file() or not _is_glb(optimized_glb):
        raise RuntimeError(f"gltf-transform did not produce a valid GLB: {optimized_glb}")

    size_after = optimized_glb.stat().st_size

    # gltfjsx writes <Name>.tsx next to / relative to the output arg. Emit into
    # out_dir with --types; useGLTF() path resolves to the optimized glb name.
    component = out_dir / "Model.tsx"
    _run_npx([
        "gltfjsx",
        str(optimized_glb),
        "--types",
        "--output", str(component),
    ])

    if not component.is_file():
        # gltfjsx sometimes names by the model; fall back to newest .tsx/.jsx.
        candidates = sorted(
            [*out_dir.glob("*.tsx"), *out_dir.glob("*.jsx")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise RuntimeError(f"gltfjsx produced no component in {out_dir}")
        component = candidates[0]

    _fix_gltf_type_import(component)

    return {
        "optimized_glb": optimized_glb,
        "component": component,
        "size_before": size_before,
        "size_after": size_after,
    }


if __name__ == "__main__":
    import json
    import sys

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("outputs/_sample.glb")
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("outputs/compiled")
    result = compile_glb(src, dst)
    print(json.dumps({k: str(v) for k, v in result.items()}, indent=2))
