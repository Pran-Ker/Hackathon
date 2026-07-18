"""Local dev server: viewer-facing HTTP for the promptable-world pipeline.

Endpoints:
- GET  /api/health          liveness
- POST /api/edit            OLD splat project: click-to-edit -> fal image (real)
- POST /imagine             Stage 5: photo -> isolated subject -> image_to_3d ->
                            optimized GLB + typed r3f component, served over HTTP.

Static files (the produced GLBs + components) are mounted at /files so the
viewer can fetch them cross-origin.

Run: uv run uvicorn pipeline.server:app --reload --port 8000
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline.compile_glb import compile_glb
from pipeline.generate import generate_edit_preview, image_to_3d
from pipeline.subject import describe_subject, isolate_subject

# outputs/ lives at pipeline/outputs (one level above this package dir).
OUTPUTS_DIR = (Path(__file__).resolve().parent.parent / "outputs")
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

# Absolute base so the viewer (different origin) can load produced files.
PUBLIC_BASE_URL = "http://localhost:8000"

app = FastAPI(title="promptable-world-pipeline")

app.add_middleware(
    CORSMiddleware,
    # Studio + vite dev origins vary (5173/5174, the static studio on 8778, the
    # LAN IP). No credentials are used, so allow any origin for dev simplicity.
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve everything under outputs/ at /files/... (CORSMiddleware wraps this too).
app.mount("/files", StaticFiles(directory=str(OUTPUTS_DIR)), name="files")


def _file_url(path: Path) -> str:
    """Absolute http URL for a file living under OUTPUTS_DIR."""
    rel = Path(path).resolve().relative_to(OUTPUTS_DIR)
    return f"{PUBLIC_BASE_URL}/files/{rel.as_posix()}"


class Point(BaseModel):
    x: float
    y: float
    z: float


class EditRequest(BaseModel):
    point: Point
    prompt: str


class EditResponse(BaseModel):
    image_url: str
    note: str


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/edit", response_model=EditResponse)
def edit(req: EditRequest):
    image_url = generate_edit_preview(req.prompt)
    return EditResponse(
        image_url=image_url,
        note="fal call is real; reconstruction/fusion into the 3D world is not implemented yet",
    )


def _error(status: int, message: str) -> JSONResponse:
    """JSON error response. CORSMiddleware wraps this so it stays cross-origin
    friendly (the viewer reads `error` off the body)."""
    return JSONResponse(status_code=status, content={"error": message})


@app.post("/imagine")
async def imagine(image: UploadFile = File(...)):
    """Photo -> 3D: isolate subject, image_to_3d, compile to an optimized GLB
    + typed r3f component. Returns absolute URLs the viewer can load."""
    # Validate the upload is actually an image before spending any pipeline time.
    content_type = (image.content_type or "").lower()
    suffix = Path(image.filename or "upload.png").suffix.lower()
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
    if not (content_type.startswith("image/") or suffix in image_exts):
        return _error(400, "Upload must be an image (PNG, JPG, or WEBP).")

    data = await image.read()
    if not data:
        return _error(400, "Uploaded image is empty.")

    req_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    work_dir = OUTPUTS_DIR / "imagine" / req_id
    work_dir.mkdir(parents=True, exist_ok=True)

    src_path = work_dir / f"input{suffix or '.png'}"
    src_path.write_bytes(data)

    # Per-generation trace: timings + outcome, written to work_dir/trace.json and
    # echoed to stdout (captured in the uvicorn log) so each image is auditable.
    trace: dict = {"req_id": req_id, "input": src_path.name, "stages": {}}

    def _stage(name: str, fn):
        t0 = time.time()
        try:
            out = fn()
            trace["stages"][name] = {"ok": True, "seconds": round(time.time() - t0, 2)}
            return out
        except Exception:
            trace["stages"][name] = {"ok": False, "seconds": round(time.time() - t0, 2)}
            raise
        finally:
            print(f"[imagine {req_id}] {name}: {trace['stages'][name]}", flush=True)
            (work_dir / "trace.json").write_text(__import__("json").dumps(trace, indent=2))

    try:
        # Stage 1: background removal (also gives us a fal-hosted cutout URL).
        subject = _stage("isolate", lambda: isolate_subject(src_path, work_dir))

        # Optional semantic metadata (failsafe: returns None on any error).
        desc = _stage("describe", lambda: describe_subject(src_path))
        trace["name"] = desc["name"] if desc else None

        # Stage 2: image -> 3D. Feed the hosted cutout URL to avoid a re-upload.
        raw_glb = _stage("image_to_3d", lambda: image_to_3d(subject["cutout_url"], work_dir / "model.glb"))

        # Stage 3: optimize + generate typed r3f component.
        compiled_dir = work_dir / "compiled"
        result = _stage("compile", lambda: compile_glb(raw_glb, compiled_dir))
        trace["size_before"] = result["size_before"]
        trace["size_after"] = result["size_after"]
        trace["ok"] = True
        (work_dir / "trace.json").write_text(__import__("json").dumps(trace, indent=2))
    except Exception as exc:  # noqa: BLE001 - surface any pipeline failure as JSON
        import traceback

        traceback.print_exc()
        trace["ok"] = False
        trace["error"] = str(exc)
        (work_dir / "trace.json").write_text(__import__("json").dumps(trace, indent=2))
        return _error(500, f"3D generation failed: {exc}")

    return {
        "glb_url": _file_url(result["optimized_glb"]),
        "component_url": _file_url(result["component"]),
        "name": desc["name"] if desc else None,
        "tags": desc["tags"] if desc else [],
        "size_before": result["size_before"],
        "size_after": result["size_after"],
    }
