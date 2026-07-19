"""Deploy wrapper: pipeline.server:app + the built viewer, one ASGI app.

Owned by the deploy setup; pipeline/server.py stays untouched. This module:

1. Seeds committed HTML (studio.html, native.html) into OUTPUTS_DIR/imagine
   when missing — a Railway volume mounted at outputs/ starts empty and would
   otherwise hide the copies baked into the image.
2. Mounts the built viewer (vite dist) at /app with html=True, so
   GET /app/ serves index.html and GET /app/mesh.html works too.

Run:  uvicorn pipeline.deploy_app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from fastapi.staticfiles import StaticFiles

from pipeline.server import OUTPUTS_DIR, app

log = logging.getLogger("deploy_app")

SEED_DIR = Path(os.environ.get("SEED_DIR", "/srv/seed"))
VIEWER_DIST = Path(os.environ.get("VIEWER_DIST", "/srv/viewer-dist"))


def _seed_outputs() -> None:
    """Copy committed seed files into outputs/ if they aren't there yet."""
    if not SEED_DIR.is_dir():
        return
    for src in SEED_DIR.rglob("*"):
        if not src.is_file():
            continue
        dst = OUTPUTS_DIR / src.relative_to(SEED_DIR)
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        log.info("seeded %s", dst)


_seed_outputs()

if VIEWER_DIST.is_dir():
    app.mount("/app", StaticFiles(directory=str(VIEWER_DIST), html=True), name="viewer")
else:
    log.warning("viewer dist not found at %s — /app not mounted", VIEWER_DIST)
