#!/usr/bin/env python3
"""Segment microservice — SAM 3 Stage 1.

Input image -> N object cutouts -> GCS folder-per-image. 3D is done by the sibling
`pipeline` service (POST /imagine); this service never renders 3D.

    GET  /health
    POST /api/segment  { image_url | image_data, prompt, per }
                       -> masks/boxes/labels (interactive, no upload)
    POST /api/stage1   { image_url | image_data, prompt, per, bucket, image_id }
                       -> segment + upload cutouts to gs://<bucket>/output/<image_id>/

See ../ARCHITECTURE.md for the integration with the pipeline (3D) service.
"""
import json
import os
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pipeline_core  # same directory

ROOT = pathlib.Path(__file__).parent
MAX_BODY = 25 * 1024 * 1024
DEFAULT_BUCKET = os.environ.get("GCS_BUCKET", "warping-hologram-pipeline")


def load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()


class ApiError(Exception):
    def __init__(self, stage, msg, code=400, hint=None):
        super().__init__(msg)
        self.stage, self.msg, self.code, self.hint = stage, msg, code, hint


def _guard(fn, stage):
    try:
        return fn()
    except ApiError:
        raise
    except Exception as e:
        low = str(e).lower()
        if any(w in low for w in ("exhausted", "locked", "insufficient", "402")):
            raise ApiError(stage, "fal balance exhausted", 402, "top up at fal.ai/dashboard/billing")
        if any(w in low for w in ("401", "403", "unauthorized", "forbidden", "credential")):
            raise ApiError(stage, "auth failed", 401, "check FAL_KEY / GCP credentials")
        raise ApiError(stage, f"{stage} failed: {str(e)[:180]}", 502)


def resolve_image(req):
    if req.get("image_url"):
        u = req["image_url"]
        if not isinstance(u, str) or not u.startswith("http"):
            raise ApiError("input", "image_url must be an http(s) URL", 400)
        return u
    data_url = req.get("image_data")
    if not isinstance(data_url, str) or "," not in data_url:
        raise ApiError("input", "no image provided (image_data or image_url required)", 400)
    return _guard(lambda: pipeline_core.data_url_to_fal(data_url), "upload")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _fail(self, e):
        if isinstance(e, ApiError):
            payload = {"error": e.msg, "stage": e.stage}
            if e.hint:
                payload["hint"] = e.hint
            self._send(e.code, payload)
        else:
            self._send(500, {"error": "internal error", "stage": "server", "detail": str(e)[:180]})

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "service": "segment",
                                    "fal_key": bool(os.environ.get("FAL_KEY")), "bucket": DEFAULT_BUCKET})
        return self._send(404, {"error": "not found", "stage": "route"})

    def do_POST(self):
        try:
            if self.path not in ("/api/segment", "/api/stage1"):
                return self._send(404, {"error": "unknown route", "stage": "route"})
            n = int(self.headers.get("Content-Length", 0) or 0)
            if n <= 0 or n > MAX_BODY:
                raise ApiError("input", "empty or too-large body", 413 if n else 400)
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                raise ApiError("input", "invalid JSON body", 400)
            if not os.environ.get("FAL_KEY"):
                raise ApiError("config", "FAL_KEY not set", 500, "add FAL_KEY to segment/.env")
            image_url = resolve_image(req)
            prompt = req.get("prompt") if isinstance(req.get("prompt"), str) else ""
            try:
                per = max(1, min(8, int(req.get("per", 1))))
            except (TypeError, ValueError):
                per = 1
            if self.path == "/api/segment":
                return self._send(200, _guard(lambda: pipeline_core.segment(image_url, prompt, per, bool(req.get("retry", True))), "segment"))
            # /api/stage1 — segment + upload N cutouts to output/<image_id>/
            bucket = req.get("bucket") or DEFAULT_BUCKET
            return self._send(200, _guard(lambda: pipeline_core.stage1(image_url, prompt, per, bool(req.get("retry", True)), bucket, req.get("image_id")), "stage1"))
        except Exception as e:
            self._fail(e)

    def log_message(self, *a):
        pass


def main():
    port = int(os.environ.get("PORT", "8799"))
    print(f"segment service up · port={port} · bucket={DEFAULT_BUCKET}", flush=True)
    if not os.environ.get("FAL_KEY"):
        print("WARNING: FAL_KEY not set (segment/.env)", flush=True)
    try:
        ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
    except OSError as e:
        raise SystemExit(f"cannot bind port {port}: {e}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
