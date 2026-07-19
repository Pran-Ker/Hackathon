"""Pick the sharpest frame from an uploaded video.

Strategy: sample ~12 frames evenly across the clip, score each with
Laplacian-variance sharpness (on a <=640px grayscale copy for speed), return the
winner as PNG bytes plus its frame index / timestamp.

Dependency choice: opencv-python-headless (not the ffmpeg CLI). One in-process
dependency gives decoding, seeking, Laplacian scoring, PNG encoding AND synthetic
video generation for tests — no subprocess output parsing and no reliance on a
system ffmpeg install, so the test suite stays hermetic.

Edge cases handled:
- empty upload / undecodable bytes  -> FramePickError (caller maps to HTTP 400)
- tiny videos (< sample count)      -> every frame is scored
- unknown frame count / bad seeking -> sequential scan fallback (capped)
- a still image passed as "video"   -> decoded via cv2.imdecode, index 0
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import cv2
import numpy as np

DEFAULT_SAMPLES = 12
SEQUENTIAL_CAP = 1200  # hard cap on frames scanned when the container lies about its length


class FramePickError(ValueError):
    """Raised when no usable frame can be extracted from the upload."""


def _sharpness(bgr: np.ndarray) -> float:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    h, w = gray.shape[:2]
    scale = 640.0 / max(h, w)
    if scale < 1.0:
        gray = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))))
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _sample_indices(total: int, samples: int) -> list[int]:
    if total <= samples:
        return list(range(total))
    step = (total - 1) / (samples - 1)
    return sorted({round(i * step) for i in range(samples)})


def pick_frame(data: bytes, samples: int = DEFAULT_SAMPLES) -> dict:
    """video bytes -> {"png": bytes, "index": int, "timestamp": float|None,
    "sharpness": float, "frames_scored": int}. Raises FramePickError if nothing
    decodable is found."""
    if not data:
        raise FramePickError("Uploaded video is empty.")

    best: tuple[float, int, np.ndarray] | None = None  # (sharpness, index, frame)
    scored = 0
    fps = 0.0

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        cap = cv2.VideoCapture(tmp)
        try:
            if cap.isOpened():
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if total > 0:
                    for idx in _sample_indices(total, samples):
                        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            continue
                        s = _sharpness(frame)
                        scored += 1
                        if best is None or s > best[0]:
                            best = (s, idx, frame.copy())
                if best is None:
                    # Unknown/lying frame count or seek failure: scan sequentially.
                    cap.release()
                    cap = cv2.VideoCapture(tmp)
                    idx = 0
                    while idx < SEQUENTIAL_CAP:
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            break
                        s = _sharpness(frame)
                        scored += 1
                        if best is None or s > best[0]:
                            best = (s, idx, frame.copy())
                        idx += 1
        finally:
            cap.release()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    if best is None:
        # Maybe it's a still image mislabeled as video.
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            best = (_sharpness(img), 0, img)
            scored = 1
            fps = 0.0
        else:
            raise FramePickError(
                "No readable frames in the uploaded video (undecodable or empty stream)."
            )

    sharp, index, frame = best
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise FramePickError("Failed to encode the selected frame as PNG.")
    return {
        "png": buf.tobytes(),
        "index": index,
        "timestamp": (index / fps) if fps > 0 else None,
        "sharpness": round(sharp, 2),
        "frames_scored": scored,
    }
