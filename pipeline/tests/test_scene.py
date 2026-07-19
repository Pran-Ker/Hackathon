"""POST /api/scene tests — all fal calls monkeypatched (no network, no credits).

Covers the mandatory edge cases: no-match -> Align not called, tiny mask
dropped, Align total failure -> cutouts + error, Align partial miss -> null glb,
describe failure -> empty info, unreadable video -> 400, duplicate request ->
cache hit keyed sha256(bytes + prompt)."""

import io

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pipeline import scene_core, server

SIZE = 128
FRAME_URL = "http://fake.fal/frame.png"


# ---------- synthetic assets ----------
def frame_png() -> bytes:
    im = Image.new("RGB", (SIZE, SIZE), (30, 40, 50))
    for x in range(20, 60):
        for y in range(20, 60):
            im.putpixel((x, y), (200, 90, 40))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def mask_png(box: tuple[int, int, int, int] | None) -> bytes:
    """White-on-black binary mask; box=(x0,y0,x1,y1) exclusive."""
    arr = np.zeros((SIZE, SIZE), dtype=np.uint8)
    if box:
        x0, y0, x1, y1 = box
        arr[y0:y1, x0:x1] = 255
    buf = io.BytesIO()
    Image.fromarray(arr, "L").save(buf, "PNG")
    return buf.getvalue()


MASK_BIG = "http://fake.fal/mask_big.png"      # 40x40 square
MASK_MED = "http://fake.fal/mask_med.png"      # 30x30 square
MASK_SML = "http://fake.fal/mask_sml.png"      # 30x30 square (bottom-left)
MASK_TINY = "http://fake.fal/mask_tiny.png"    # 2x2 -> must be dropped
GLB_0 = "http://fake.fal/obj0.glb"
GLB_2 = "http://fake.fal/obj2.glb"
SCENE_GLB = "http://fake.fal/scene.glb"

FAKE_FILES = {
    MASK_BIG: mask_png((20, 20, 60, 60)),
    MASK_MED: mask_png((70, 70, 100, 100)),
    MASK_SML: mask_png((10, 70, 40, 100)),
    MASK_TINY: mask_png((5, 5, 7, 7)),
    GLB_0: b"GLB0-BYTES",
    GLB_2: b"GLB2-BYTES",
    SCENE_GLB: b"SCENE-GLB-BYTES",
}


def seg_result(masks, labels, scores):
    return {
        "image_url": FRAME_URL,
        "masks": list(masks),
        "mask_urls": list(masks),
        "boxes": [None] * len(masks),
        "scores": list(scores),
        "labels": list(labels),
        "misses": [],
        "retried": {},
        "errors": [],
        "count": len(masks),
        "concepts": sorted(set(labels)) or ["object"],
    }


# ---------- fixtures ----------
@pytest.fixture
def fal(monkeypatch):
    """Monkeypatch every fal/network touchpoint in scene_core; returns a control
    object whose fields the test can reassign."""

    class Ctl:
        seg = seg_result([], [], [])
        rec: dict | Exception = {"model_glb": "", "individual_glbs": [], "parts": [], "count": 0}
        describe: dict | None = {"name": "mug", "tags": ["ceramic", "red"]}
        segment_calls = 0
        align_calls = 0
        align_mask_urls = None

    ctl = Ctl()

    def fake_segment(image_url, prompt, per=1, retry=True):
        ctl.segment_calls += 1
        return ctl.seg

    def fake_reconstruct(image_url, mask_urls, labels=None):
        ctl.align_calls += 1
        ctl.align_mask_urls = list(mask_urls)
        if isinstance(ctl.rec, Exception):
            raise ctl.rec
        return ctl.rec

    monkeypatch.setattr(scene_core, "fal_upload", lambda data, ctype: FRAME_URL)
    monkeypatch.setattr(scene_core, "segment", fake_segment)
    monkeypatch.setattr(scene_core, "reconstruct", fake_reconstruct)
    monkeypatch.setattr(scene_core, "download_bytes", lambda url, timeout=120: FAKE_FILES[url])
    monkeypatch.setattr(scene_core, "describe_subject", lambda path, **kw: ctl.describe)
    monkeypatch.setattr(scene_core, "describe_fallback", lambda path: ctl.describe)
    return ctl


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUTS_DIR", tmp_path)
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    monkeypatch.setattr(server, "SCENE_DIR", scene_dir)
    with TestClient(server.app) as c:
        yield c


def post_scene(client, data=None, prompt="cup", filename="photo.png", ctype="image/png"):
    return client.post(
        "/api/scene",
        files={"file": (filename, data if data is not None else frame_png(), ctype)},
        data={"prompt": prompt},
    )


# ---------- tests ----------
def test_full_flow_with_tiny_drop_and_partial_align(client, fal):
    """3 masks: tiny one dropped pre-Align; Align returns 1 of 2 glbs -> the
    other object keeps cutout with glb_url null; scene glb downloaded + served."""
    fal.seg = seg_result(
        [MASK_BIG, MASK_TINY, MASK_MED], ["cup", "ghost", "plant"], [0.9, 0.8, 0.7]
    )
    fal.rec = {
        "model_glb": SCENE_GLB,
        "individual_glbs": [GLB_0],
        "parts": [{"label": "cup", "glb_url": GLB_0, "splat_url": "http://fake.fal/obj0.ply"}],
        "metadata": [
            {
                "object_index": 0,
                "scale": [[0.5, 0.5, 0.5]],
                "rotation": [[1.0, 0.0, 0.0, 0.0]],  # identity, wxyz
                "translation": [[1.0, 2.0, 3.0]],
                "camera_pose": None,
            }
        ],
        "count": 1,
    }
    r = post_scene(client, prompt="cup,ghost,plant")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["cached"] is False
    assert body["prompt"] == "cup,ghost,plant"
    assert body["error"] is None
    assert "/files/scene/" in body["scene_glb_url"]
    assert "/files/scene/" in body["frame_url"]

    # tiny mask dropped, label logged
    assert [d["label"] for d in body["dropped"]] == ["ghost"]
    assert len(body["objects"]) == 2

    o0, o1 = body["objects"]
    assert o0["id"] == "obj_0" and o0["label"] == "cup"
    assert o0["glb_url"] and "/files/scene/" in o0["glb_url"]
    assert o0["splat_url"] == "http://fake.fal/obj0.ply"
    assert o0["cutout_url"] and "/files/scene/" in o0["cutout_url"]
    assert o0["bbox"] == [20, 20, 40, 40]
    assert o0["score"] == 0.9
    assert o0["info"]["name"] == "mug" and o0["info"]["tags"] == ["ceramic", "red"]

    # Align metadata TRS -> three.js-convention transform (x/z negated, wxyz->xyzw)
    assert o0["transform"] == {
        "position": [-1.0, 2.0, -3.0],
        "rotation": [0.0, 0.0, 0.0, 1.0],
        "scale": [0.5, 0.5, 0.5],
    }
    assert o0["align_meta"]["object_index"] == 0

    # Align missed obj_1 -> glb null, object kept, no transform
    assert o1["label"] == "plant" and o1["glb_url"] is None
    assert o1["transform"] is None and o1["align_meta"] is None
    assert o1["cutout_url"] and o1["bbox"] == [70, 70, 30, 30]

    # Align received exactly the kept masks, in order
    assert fal.align_mask_urls == [MASK_BIG, MASK_MED]

    # on disk: scene.json + frame + glbs + trace with timings
    d = server.SCENE_DIR / body["id"]
    for name in ("scene.json", "trace.json", "frame.png", "scene.glb", "obj_0.glb", "cutout_0.png"):
        assert (d / name).is_file(), name
    assert (d / "obj_0.glb").read_bytes() == b"GLB0-BYTES"
    assert {"upload", "segment", "cutouts", "align", "describe", "total"} <= set(body["timings"])


def test_partial_align_mid_list_miss_keys_parts_by_object_index(client, fal):
    """Align misses the MIDDLE object: individual_glbs/parts arrive as a
    COMPACTED success list ([glb0, glb2], no null placeholder) with metadata
    keyed by object_index (0 and 2). Object 1 must get glb_url null and object
    2 must receive glb2 + its own transform — not object 2's mesh under obj_1."""
    fal.seg = seg_result(
        [MASK_BIG, MASK_MED, MASK_SML], ["cup", "plant", "book"], [0.9, 0.8, 0.7]
    )
    fal.rec = {
        "model_glb": SCENE_GLB,
        "individual_glbs": [GLB_0, GLB_2],
        "parts": [
            {"label": "cup", "glb_url": GLB_0, "splat_url": "http://fake.fal/obj0.ply"},
            {"label": "book", "glb_url": GLB_2, "splat_url": "http://fake.fal/obj2.ply"},
        ],
        "metadata": [
            {
                "object_index": 0,
                "scale": [[0.5, 0.5, 0.5]],
                "rotation": [[1.0, 0.0, 0.0, 0.0]],
                "translation": [[1.0, 2.0, 3.0]],
            },
            {
                "object_index": 2,
                "scale": [[0.4, 0.4, 0.4]],
                "rotation": [[1.0, 0.0, 0.0, 0.0]],
                "translation": [[4.0, 5.0, 6.0]],
            },
        ],
        "count": 2,
    }
    r = post_scene(client, prompt="cup,plant,book")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    o0, o1, o2 = body["objects"]

    assert o0["glb_url"] and "/files/scene/" in o0["glb_url"]
    assert o0["splat_url"] == "http://fake.fal/obj0.ply"
    assert o0["transform"]["position"] == [-1.0, 2.0, -3.0]

    # the missed middle object: no mesh, no splat, no transform — cutout kept
    assert o1["label"] == "plant" and o1["glb_url"] is None
    assert o1["splat_url"] is None and o1["transform"] is None
    assert o1["cutout_url"]

    # the trailing success keeps ITS OWN mesh + transform (not shifted onto o1)
    assert o2["label"] == "book" and o2["glb_url"] and "/files/scene/" in o2["glb_url"]
    assert o2["splat_url"] == "http://fake.fal/obj2.ply"
    assert o2["transform"]["position"] == [-4.0, 5.0, -6.0]

    d = server.SCENE_DIR / body["id"]
    assert (d / "obj_0.glb").read_bytes() == b"GLB0-BYTES"
    assert not (d / "obj_1.glb").exists()
    assert (d / "obj_2.glb").read_bytes() == b"GLB2-BYTES"


def test_no_match_skips_align(client, fal):
    fal.seg = seg_result([], [], [])
    r = post_scene(client, prompt="unicorn")
    assert r.status_code == 200
    body = r.json()
    assert body["objects"] == []
    assert body["scene_glb_url"] is None
    assert fal.align_calls == 0  # EDGE: Align must NOT be called


def test_align_total_failure_returns_cutouts_and_error(client, fal):
    fal.seg = seg_result([MASK_BIG], ["cup"], [0.9])
    fal.rec = RuntimeError("fal exploded")
    r = post_scene(client)
    assert r.status_code == 200  # partial > nothing
    body = r.json()
    assert "align failed" in body["error"]
    assert len(body["objects"]) == 1
    assert body["objects"][0]["glb_url"] is None
    assert body["objects"][0]["cutout_url"]  # viewer falls back to 2D cards
    assert body["scene_glb_url"] is None


def test_describe_failure_never_fatal(client, fal):
    fal.seg = seg_result([MASK_BIG], ["cup"], [0.9])
    fal.describe = None
    r = post_scene(client)
    assert r.status_code == 200
    info = r.json()["objects"][0]["info"]
    assert info == {"name": None, "tags": [], "facts": []}


def test_duplicate_request_is_cache_hit(client, fal):
    fal.seg = seg_result([MASK_BIG], ["cup"], [0.9])
    r1 = post_scene(client)
    r2 = post_scene(client)
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["cached"] is False
    assert r2.json()["cached"] is True
    assert fal.segment_calls == 1  # pipeline ran exactly once
    b1, b2 = r1.json(), r2.json()
    b1["cached"] = b2["cached"]
    assert b1 == b2


def test_failed_scene_is_cache_miss_on_post_but_replayable_on_get(client, fal):
    """A scene persisted with `error` (Align outage) must NOT be served as a
    POST cache hit — the retry re-runs the pipeline and heals the cache.
    GET /api/scene/{id} still replays the partial (partial > nothing)."""
    fal.seg = seg_result([MASK_BIG], ["cup"], [0.9])
    fal.rec = RuntimeError("transient fal outage")
    r1 = post_scene(client)
    assert r1.status_code == 200 and "align failed" in r1.json()["error"]
    scene_id = r1.json()["id"]

    # the errored partial is still replayable by id
    g = client.get(f"/api/scene/{scene_id}")
    assert g.status_code == 200 and "align failed" in g.json()["error"]

    # retry with the identical bytes+prompt re-runs and heals
    fal.rec = {
        "model_glb": SCENE_GLB,
        "individual_glbs": [GLB_0],
        "parts": [{"label": "cup", "glb_url": GLB_0, "splat_url": None}],
        "metadata": [],
        "count": 1,
    }
    r2 = post_scene(client)
    assert r2.status_code == 200
    body = r2.json()
    assert body["cached"] is False and body["error"] is None
    assert body["objects"][0]["glb_url"]
    assert fal.segment_calls == 2  # pipeline genuinely re-ran

    # healed result is now the cached one
    r3 = post_scene(client)
    assert r3.json()["cached"] is True and r3.json()["error"] is None

    # genuine no-match (error=None, objects=[]) stays cached — not a failure
    fal.seg = seg_result([], [], [])
    n1 = post_scene(client, prompt="unicorn")
    n2 = post_scene(client, prompt="unicorn")
    assert n1.json()["error"] is None and n1.json()["objects"] == []
    assert n2.json()["cached"] is True


def test_cache_key_includes_prompt(client, fal):
    fal.seg = seg_result([MASK_BIG], ["cup"], [0.9])
    r1 = post_scene(client, prompt="cup")
    r2 = post_scene(client, prompt="plant")
    assert r1.json()["id"] != r2.json()["id"]
    assert r2.json()["cached"] is False
    assert fal.segment_calls == 2


def test_auto_mode_caps_top_n(client, fal):
    """Empty prompt -> generic 'object' segmentation, top-4 by score."""
    masks = [f"http://fake.fal/auto{i}.png" for i in range(6)]
    for m in masks:
        FAKE_FILES[m] = mask_png((20, 20, 60, 60))
    fal.seg = seg_result(masks, ["object"] * 6, [0.4, 0.9, 0.2, 0.8, 0.7, 0.6])
    r = post_scene(client, prompt="")
    assert r.status_code == 200
    body = r.json()
    assert len(body["objects"]) == 4
    kept = {u.rsplit("/", 1)[-1] for u in fal.align_mask_urls}
    assert kept == {"auto1.png", "auto3.png", "auto4.png", "auto5.png"}  # top scores


def test_unreadable_video_is_400(client, fal):
    r = post_scene(client, data=b"not a video at all" * 50, filename="clip.mp4", ctype="video/mp4")
    assert r.status_code == 400
    assert "frame" in r.json()["error"].lower() or "video" in r.json()["error"].lower()


def test_empty_upload_is_400(client, fal):
    r = post_scene(client, data=b"")
    assert r.status_code == 400


def test_unreadable_image_is_400(client, fal):
    r = post_scene(client, data=b"\x00\x01garbage", filename="x.png", ctype="image/png")
    assert r.status_code == 400


def test_video_end_to_end_with_frame_meta(client, fal):
    """Real framepick on a synthetic clip; fal still mocked."""
    from tests.test_framepick import _blurry, _noise, make_video

    frames = [_blurry(i) for i in range(8)]
    frames[5] = _noise(9)
    fal.seg = seg_result([MASK_BIG], ["cup"], [0.9])
    r = post_scene(client, data=make_video(frames), filename="clip.mp4", ctype="video/mp4")
    assert r.status_code == 200
    meta = r.json()["frame_meta"]
    assert meta["frame_index"] == 5
    # the chosen frame was decoded + saved
    d = server.SCENE_DIR / r.json()["id"]
    img = cv2.imread(str(d / "frame.png"))
    assert img is not None and img.shape[0] > 0


def test_scenes_listing(client, fal):
    fal.seg = seg_result([MASK_BIG], ["cup"], [0.9])
    post_scene(client, prompt="cup")
    r = client.get("/api/scenes")
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) == 1
    e = entries[0]
    assert e["labels"] == ["cup"] and e["object_count"] == 1
    assert "/files/scene/" in e["frame_url"]


def test_transform_from_align_meta_conversion():
    """PyTorch3D camera frame (x left, y up, z fwd; wxyz row-form quat) ->
    three.js (x right, y up, -z fwd; xyzw quat): pos x/z negate, quat [w,x,y,z]
    -> [x,-y,z,w]. Handles both nested ([[...]]) and flat metadata values."""
    nested = {
        "object_index": 2,
        "scale": [[0.51, 0.51, 0.51]],
        "rotation": [[0.7, 0.1, -0.2, 0.3]],
        "translation": [[-0.53, 0.03, 1.65]],
    }
    t = scene_core.transform_from_align_meta(nested)
    assert t["position"] == [0.53, 0.03, -1.65]
    assert t["rotation"] == [0.1, 0.2, 0.3, 0.7]
    assert t["scale"] == [0.51, 0.51, 0.51]

    flat = {"scale": [1, 1, 1], "rotation": [1, 0, 0, 0], "translation": [1, 2, 3]}
    t = scene_core.transform_from_align_meta(flat)
    assert t == {"position": [-1.0, 2.0, -3.0], "rotation": [0.0, 0.0, 0.0, 1.0], "scale": [1.0, 1.0, 1.0]}

    assert scene_core.transform_from_align_meta({}) is None
    assert scene_core.transform_from_align_meta({"rotation": [[1, 0, 0, 0]]}) is None
    assert scene_core.transform_from_align_meta({"rotation": [["x", 0, 0, 0]], "translation": [[1, 2, 3]]}) is None


def test_existing_endpoints_still_alive(client):
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/runs").status_code == 200
