"""framepick unit tests — synthetic videos generated in-process with OpenCV
(no fal, no network, no system ffmpeg needed)."""

import os
import tempfile

import cv2
import numpy as np
import pytest

from pipeline.framepick import FramePickError, pick_frame

SIZE = 128


def _noise(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (SIZE, SIZE, 3), dtype=np.uint8)


def _blurry(seed: int) -> np.ndarray:
    return cv2.GaussianBlur(_noise(seed), (31, 31), 12)


def make_video(frames: list[np.ndarray], fps: float = 10.0) -> bytes:
    """Encode frames to bytes, trying a few codecs (headless builds vary)."""
    for codec, suffix in (("mp4v", ".mp4"), ("avc1", ".mp4"), ("MJPG", ".avi")):
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        try:
            w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*codec), fps, (SIZE, SIZE))
            if not w.isOpened():
                continue
            for fr in frames:
                w.write(fr)
            w.release()
            data = open(path, "rb").read()
            if data:
                return data
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    pytest.skip("no usable VideoWriter codec in this OpenCV build")


def test_picks_sharpest_frame():
    sharp_at = 7
    frames = [_blurry(i) for i in range(12)]
    frames[sharp_at] = _noise(99)  # unblurred noise = far higher Laplacian variance
    out = pick_frame(make_video(frames))
    assert out["index"] == sharp_at
    assert out["timestamp"] == pytest.approx(sharp_at / 10.0, abs=0.2)
    img = cv2.imdecode(np.frombuffer(out["png"], np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape[:2] == (SIZE, SIZE)
    assert out["frames_scored"] >= 10


def test_tiny_video_all_frames_scored():
    frames = [_blurry(0), _noise(1)]
    out = pick_frame(make_video(frames))
    assert out["index"] == 1
    assert out["frames_scored"] == 2


def test_long_video_samples_evenly():
    frames = [_blurry(i) for i in range(60)]
    frames[59] = _noise(42)  # last frame must be in the even sample set
    out = pick_frame(make_video(frames), samples=12)
    assert out["index"] == 59
    assert out["frames_scored"] <= 12


def test_image_passed_as_video():
    ok, buf = cv2.imencode(".png", _noise(3))
    assert ok
    out = pick_frame(buf.tobytes())
    assert out["index"] == 0
    assert out["png"]


def test_garbage_bytes_raise():
    with pytest.raises(FramePickError):
        pick_frame(b"this is definitely not a video" * 100)


def test_empty_bytes_raise():
    with pytest.raises(FramePickError):
        pick_frame(b"")
