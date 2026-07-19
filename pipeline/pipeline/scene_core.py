"""POPFRAME scene core: SAM 3 segmentation + SAM 3D Align reconstruction.

Ported (adapted, not imported) from segment/pipeline_core.py into the pipeline
package so the FastAPI service owns the whole flow:

    frame.png -> fal upload -> segment() [SAM 3 per-concept + LLM common-word
    retry] -> cutouts from masks (drop empty/tiny) -> reconstruct() [SAM 3D
    Align, one shared scene] -> per-object describe (Claude, failsafe) ->
    scene.json + trace.json under outputs/scene/<id>/.

Every fal-touching callable is module-level so tests can monkeypatch it.

Mandatory edge cases (see build_scene):
- no object matches prompt   -> objects: [] and Align is NOT called
- empty/tiny mask            -> object dropped, label logged in scene["dropped"]
- Align fails entirely       -> objects keep cutouts, scene["error"] set
- Align misses an object     -> that object's glb is null, others kept
- describe failure           -> info fields null/empty, never fatal
"""

from __future__ import annotations

import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from pipeline.subject import describe_subject

MAX_CONCEPTS = 4
AUTO_MAX_OBJECTS = 4          # auto mode ("" prompt): generic "object", top-N by score
MIN_MASK_PIXELS = 64          # absolute floor for a usable mask
MIN_MASK_FRACTION = 0.0005    # relative floor vs frame area
CUTOUT_PAD = 6                # px of breathing room around the cutout crop


def _url(x):
    return x.get("url") if isinstance(x, dict) else (x if isinstance(x, str) else None)


# ---------- fal I/O (module-level for monkeypatching) ----------
def fal_upload(data: bytes, content_type: str) -> str:
    import fal_client

    return fal_client.upload(data, content_type)


def download_bytes(url: str, timeout: int = 120) -> bytes:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def image_to_png_bytes(data: bytes) -> bytes:
    """Normalize arbitrary image bytes to PNG (raises on undecodable input)."""
    im = Image.open(io.BytesIO(data))
    im.load()
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


# ---------- segment (SAM 3, per-concept + LLM retry) ----------
def _seg_one(image_url, concept, per):
    import fal_client

    r = fal_client.subscribe(
        "fal-ai/sam-3/image",
        arguments={
            "image_url": image_url,
            "prompt": concept,
            "return_multiple_masks": True,
            "max_masks": min(12, max(per, 3)),
            "include_boxes": True,
            "include_scores": True,
        },
        with_logs=False,
    )
    masks = [_url(x) for x in (r.get("masks") or [])]
    boxes, scores = r.get("boxes") or [], r.get("scores") or []
    items = []
    for i, m in enumerate(masks):
        if not m or len(items) >= per:
            continue
        items.append(
            {
                "url": m,
                "box": boxes[i] if i < len(boxes) else None,
                "score": scores[i] if i < len(scores) else None,
            }
        )
    return items


def common_word(concept):
    import fal_client

    try:
        r = fal_client.subscribe(
            "fal-ai/any-llm",
            arguments={
                "model": "google/gemini-2.5-flash-lite",
                "system_prompt": (
                    "Map an object name to ONE more common, generic English noun an "
                    "open-vocabulary image segmenter recognizes. Reply with only that "
                    "single lowercase word — no punctuation, no explanation."
                ),
                "prompt": f"Object: {concept}",
            },
            with_logs=False,
        )
        out = (r.get("output") or "").strip().lower()
        return ("".join(ch for ch in out.split()[0] if ch.isalpha()) or None) if out else None
    except Exception:
        return None


def segment(image_url, prompt, per=1, retry=True):
    """One SAM call per comma-concept (max 4, parallel). Misses get one LLM
    common-word retry. Returns parallel arrays (masks/boxes/scores/labels)."""
    concepts = [c.strip() for c in (prompt or "object").split(",") if c.strip()][:MAX_CONCEPTS] or ["object"]
    masks, boxes, scores, labels, misses, retried, errors = [], [], [], [], [], {}, []

    def _take(items, label):
        for it in items:
            masks.append(it["url"])
            boxes.append(it["box"])
            scores.append(it["score"])
            labels.append(label)

    def _run(c):
        try:
            return c, _seg_one(image_url, c, per), None
        except Exception as e:
            return c, [], str(e)[:160]

    with ThreadPoolExecutor(max_workers=min(4, len(concepts))) as ex:
        for c, items, err in ex.map(_run, concepts):
            if err:
                errors.append(f"{c}: {err}")
            elif items:
                _take(items, c)
            else:
                misses.append(c)

    if retry and misses:

        def _retry(c):
            syn = common_word(c)
            if not syn or syn == c.lower():
                return c, syn, []
            try:
                return c, syn, _seg_one(image_url, syn, per)
            except Exception:
                return c, syn, []

        still = []
        with ThreadPoolExecutor(max_workers=min(4, len(misses))) as ex:
            for c, syn, items in ex.map(_retry, misses):
                if items:
                    retried[c] = syn
                    _take(items, c)
                else:
                    still.append(c)
        misses = still

    return {
        "image_url": image_url,
        "masks": masks,
        "mask_urls": masks,
        "boxes": boxes,
        "scores": scores,
        "labels": labels,
        "misses": misses,
        "retried": retried,
        "errors": errors,
        "count": len(masks),
        "concepts": concepts,
    }


# ---------- reconstruct (SAM 3D Align: all objects, one shared scene) ----------
def reconstruct(image_url, mask_urls, labels=None):
    """SAM 3D Align. mask_urls are REQUIRED in practice: image_url-only 422s
    with 'Auto-segmentation produced no masks' (verified live). Callers must
    never invoke this without at least one mask (build_scene guarantees it).

    The combined model_glb has IDENTITY node transforms (every mesh is
    unit-normalized at origin, overlapping) — the true per-object placement
    lives ONLY in the response's `metadata` (per-object TRS, PyTorch3D
    camera frame). We pass it through so build_scene can emit transforms."""
    import fal_client

    args = {"image_url": image_url}
    clean = [m for m in (mask_urls or []) if isinstance(m, str) and m.startswith("http")]
    if not clean:
        raise ValueError("reconstruct() requires mask_urls — Align 422s without masks")
    args["mask_urls"] = clean
    r = fal_client.subscribe("fal-ai/sam-3/3d-objects", arguments=args, with_logs=False)
    glbs = [_url(x) for x in (r.get("individual_glbs") or [])]
    splats = [_url(x) for x in (r.get("individual_splats") or [])]
    labels = labels or []
    parts = [
        {
            "label": labels[i] if i < len(labels) else f"part_{i}",
            "glb_url": g,
            "splat_url": splats[i] if i < len(splats) else None,
        }
        for i, g in enumerate(glbs)
    ]
    return {
        "image_url": image_url,
        "model_glb": _url(r.get("model_glb")) or "",
        "individual_glbs": glbs,
        "parts": parts,
        "metadata": r.get("metadata") or [],
        "count": len([g for g in glbs if g]),
        "used_masks": bool(clean),
    }


# ---------- Align metadata -> three.js/glTF transform ----------
def _flat(x) -> list[float] | None:
    """Align metadata values arrive batch-nested ([[...]]) — flatten one level."""
    if x is None:
        return None
    if isinstance(x, (list, tuple)) and x and isinstance(x[0], (list, tuple)):
        x = x[0]
    try:
        return [float(v) for v in x]
    except (TypeError, ValueError):
        return None


def transform_from_align_meta(entry: dict) -> dict | None:
    """Align metadata TRS (PyTorch3D camera frame: +x left, +y up, +z forward;
    quaternion wxyz applied ROW-form, v_cam = (s*v) @ R(q) + t — verified
    against facebookresearch/sam-3d-objects compose_transform) -> a three.js /
    glTF-style transform (right-handed, +y up, camera at origin looking down
    -z): position [x,y,z], rotation quaternion [x,y,z,w], scale [x,y,z].

    Derivation: frame change is Ry(180deg) (x -> -x, z -> -z); row-form R(q)
    equals column-form R(q^-1), so q_col = conj(q); conjugating by Ry(180)
    maps (qx,qy,qz,qw) -> (-qx,qy,-qz,qw). Net: [w,x,y,z] -> [x,-y,z,w].
    Empirically validated: projecting the smoke-test meshes with this TRS
    matches the SAM-3 mask silhouettes (chair/plant/sneaker, left-to-right)."""
    t = _flat(entry.get("translation"))
    q = _flat(entry.get("rotation"))
    s = _flat(entry.get("scale"))
    if not t or len(t) != 3 or not q or len(q) != 4:
        return None
    w, x, y, z = q
    if not s or len(s) != 3:
        s = [1.0, 1.0, 1.0]
    return {
        "position": [-t[0], t[1], -t[2]],
        "rotation": [x, -y, z, w],
        "scale": s,
    }


def image_id_for(seed) -> str:
    """Stable sha16 id for arbitrary seed material."""
    import hashlib

    return hashlib.sha256(seed if isinstance(seed, bytes) else str(seed).encode()).hexdigest()[:16]


# ---------- cutouts from masks ----------
def _mask_alpha(mask_bytes: bytes, size: tuple[int, int]) -> np.ndarray:
    """Mask image bytes -> uint8 alpha at frame size. Works for binary masks
    (grayscale) and RGBA cutouts (alpha channel)."""
    im = Image.open(io.BytesIO(mask_bytes))
    im.load()
    alpha = im.getchannel("A") if "A" in im.getbands() else im.convert("L")
    if alpha.size != size:
        alpha = alpha.resize(size, Image.NEAREST)
    return np.asarray(alpha)


def _extract_objects(frame_path: Path, seg: dict, work_dir: Path) -> tuple[list[dict], list[dict]]:
    """Download each mask, drop empty/tiny ones, write RGBA cutout crops.

    Returns (objects, dropped). Each object dict keeps its fal mask_url so Align
    receives exactly the kept masks, in order."""
    frame = Image.open(frame_path).convert("RGBA")
    frame_arr = np.asarray(frame)
    w, h = frame.size
    min_pixels = max(MIN_MASK_PIXELS, int(w * h * MIN_MASK_FRACTION))

    objects: list[dict] = []
    dropped: list[dict] = []
    for i, (mask_url, label) in enumerate(zip(seg.get("masks", []), seg.get("labels", []))):
        score = seg["scores"][i] if i < len(seg.get("scores", [])) else None
        obj = {
            "id": f"obj_{len(objects)}",
            "label": label,
            "score": score,
            "mask_url": mask_url,
            "cutout": None,
            "bbox": None,
            "glb": None,
            "glb_remote": None,
            "splat_url": None,
            "transform": None,
            "align_meta": None,
        }
        try:
            alpha = _mask_alpha(download_bytes(mask_url), (w, h))
        except Exception as e:
            # Mask fetch failed locally; keep the object — Align can still use the URL.
            print(f"[scene] mask download failed for '{label}': {str(e)[:120]}", flush=True)
            objects.append(obj)
            continue

        ys, xs = np.nonzero(alpha > 127)
        if len(ys) < min_pixels:
            print(f"[scene] dropping '{label}': empty/tiny mask ({len(ys)} px)", flush=True)
            dropped.append({"label": label, "reason": f"empty/tiny mask ({len(ys)} px)"})
            continue

        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        obj["bbox"] = [x0, y0, x1 - x0 + 1, y1 - y0 + 1]

        rgba = frame_arr.copy()
        rgba[..., 3] = np.minimum(rgba[..., 3], alpha)
        cx0, cy0 = max(0, x0 - CUTOUT_PAD), max(0, y0 - CUTOUT_PAD)
        cx1, cy1 = min(w, x1 + 1 + CUTOUT_PAD), min(h, y1 + 1 + CUTOUT_PAD)
        cut_name = f"cutout_{len(objects)}.png"
        Image.fromarray(rgba[cy0:cy1, cx0:cx1]).save(work_dir / cut_name)
        obj["cutout"] = cut_name
        objects.append(obj)

    # Re-id after drops so ids are dense: obj_0..obj_{n-1}.
    for n, obj in enumerate(objects):
        obj["id"] = f"obj_{n}"
    return objects, dropped


# ---------- describe (Claude, failsafe; fal any-llm/vision fallback) ----------
def describe_fallback(image_path: Path) -> dict | None:
    """When describe_subject fails (e.g. missing/invalid Anthropic key), get
    {name, tags} from fal any-llm/vision instead. Failsafe: None on any error."""
    import fal_client

    try:
        url = fal_client.upload(Path(image_path).read_bytes(), "image/png")
        r = fal_client.subscribe(
            "fal-ai/any-llm/vision",
            arguments={
                "model": "google/gemini-2.5-flash-lite",
                "prompt": (
                    "Identify the single main subject in this image. Respond with "
                    'ONLY minified JSON of the form {"name": "<short noun phrase>", '
                    '"tags": ["<tag>", ...]} with 3-6 lowercase descriptive tags. No prose.'
                ),
                "image_url": url,
            },
            with_logs=False,
        )
        text = (r.get("output") or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):]
        data = json.loads(text)
        return {
            "name": str(data.get("name", "")) or None,
            "tags": [str(t) for t in data.get("tags", [])],
        }
    except Exception:
        return None

def _describe_objects(work_dir: Path, objects: list[dict], frame_path: Path) -> None:
    """Attach info {name, tags, facts} per object. Per-object on the cutout when
    one exists (cheap: <=4 haiku calls, parallel); objects without a cutout share
    a single frame-level describe. Any failure -> null/empty info, never fatal."""
    with_cutout = [o for o in objects if o.get("cutout")]

    def _one(o):
        try:
            path = work_dir / o["cutout"]
            return o["id"], describe_subject(path) or describe_fallback(path)
        except Exception:
            return o["id"], None

    results: dict[str, dict | None] = {}
    if with_cutout:
        with ThreadPoolExecutor(max_workers=min(4, len(with_cutout))) as ex:
            for oid, d in ex.map(_one, with_cutout):
                results[oid] = d

    frame_desc: dict | None = None
    frame_done = False
    for o in objects:
        d = results.get(o["id"])
        if d is None and not o.get("cutout"):
            if not frame_done:
                try:
                    frame_desc = describe_subject(frame_path) or describe_fallback(frame_path)
                except Exception:
                    frame_desc = None
                frame_done = True
            d = frame_desc
        o["info"] = {
            "name": (d or {}).get("name") or None,
            "tags": [str(t) for t in (d or {}).get("tags") or []],
            "facts": [],
        }


# ---------- top-N for auto mode ----------
def _top_n(seg: dict, n: int) -> dict:
    if seg.get("count", 0) <= n:
        return seg
    order = sorted(
        range(seg["count"]),
        key=lambda i: (seg["scores"][i] if seg["scores"][i] is not None else -1.0),
        reverse=True,
    )[:n]
    order.sort()  # keep original (spatial/confidence) ordering among the kept
    out = dict(seg)
    for key in ("masks", "boxes", "scores", "labels"):
        out[key] = [seg[key][i] for i in order]
    out["mask_urls"] = out["masks"]
    out["count"] = n
    return out


# ---------- the whole scene, blocking (run via run_in_threadpool) ----------
def build_scene(work_dir: Path, prompt: str, frame_meta: dict | None = None) -> dict:
    """frame.png in work_dir -> scene dict (relative file paths) + scene.json,
    trace.json on disk. Raises only on fatal early failures (upload/total
    segment outage); Align/describe failures degrade per the edge-case rules."""
    work_dir = Path(work_dir)
    frame_path = work_dir / "frame.png"
    timings: dict[str, float] = {}
    trace: dict = {"id": work_dir.name, "prompt": prompt, "stages": {}}
    t_start = time.time()

    def _stage(name: str, fn):
        t0 = time.time()
        try:
            out = fn()
            trace["stages"][name] = {"ok": True, "seconds": round(time.time() - t0, 2)}
            return out
        except Exception as exc:
            trace["stages"][name] = {"ok": False, "seconds": round(time.time() - t0, 2), "error": str(exc)[:300]}
            raise
        finally:
            timings[name] = round(time.time() - t0, 2)
            print(f"[scene {work_dir.name}] {name}: {trace['stages'][name]}", flush=True)
            (work_dir / "trace.json").write_text(json.dumps(trace, indent=2))

    image_url = _stage("upload", lambda: fal_upload(frame_path.read_bytes(), "image/png"))

    auto = not (prompt or "").strip()
    seg = _stage(
        "segment",
        lambda: segment(image_url, prompt if not auto else "object", per=(AUTO_MAX_OBJECTS if auto else 1)),
    )
    if auto:
        seg = _top_n(seg, AUTO_MAX_OBJECTS)

    error: str | None = None
    if seg["count"] == 0 and seg.get("errors"):
        error = "segment: " + "; ".join(seg["errors"])[:300]

    objects, dropped = _stage("cutouts", lambda: _extract_objects(frame_path, seg, work_dir))

    scene_glb: str | None = None
    scene_glb_remote: str | None = None
    if objects:  # EDGE: zero objects -> DO NOT call Align.
        try:
            rec = _stage(
                "align",
                lambda: reconstruct(image_url, [o["mask_url"] for o in objects], [o["label"] for o in objects]),
            )
            parts = rec.get("parts") or []
            # True placement comes from Align's metadata (the combined GLB's
            # node transforms are identity). Keyed by object_index == mask order.
            meta_by_index: dict[int, dict] = {}
            for entry in rec.get("metadata") or []:
                if isinstance(entry, dict) and isinstance(entry.get("object_index"), int):
                    meta_by_index[entry["object_index"]] = entry
            # Partial Align returns COMPACTED success lists (no null placeholder
            # for a missed object) with metadata parallel to them. Key parts by
            # object_index too, else a mid-list miss shifts every later mesh
            # onto the wrong object. Falls back to position when metadata is
            # absent/unusable (identical to the old behavior).
            metadata = rec.get("metadata") or []
            part_by_index: dict[int, dict] = {}
            for j, part in enumerate(parts):
                entry = metadata[j] if j < len(metadata) else None
                idx = (
                    entry["object_index"]
                    if isinstance(entry, dict) and isinstance(entry.get("object_index"), int)
                    else j
                )
                part_by_index[idx] = part
            for i, obj in enumerate(objects):
                part = part_by_index.get(i) or {}
                remote_glb = part.get("glb_url")
                obj["splat_url"] = part.get("splat_url")
                meta = meta_by_index.get(i)
                if meta:
                    obj["align_meta"] = meta
                    obj["transform"] = transform_from_align_meta(meta)
                if remote_glb:
                    try:
                        (work_dir / f"{obj['id']}.glb").write_bytes(download_bytes(remote_glb))
                        obj["glb"] = f"{obj['id']}.glb"
                    except Exception:
                        obj["glb_remote"] = remote_glb  # serve the fal URL as fallback
                # else: Align missed this object -> glb stays null, others kept.
            if rec.get("model_glb"):
                try:
                    (work_dir / "scene.glb").write_bytes(download_bytes(rec["model_glb"]))
                    scene_glb = "scene.glb"
                except Exception:
                    scene_glb_remote = rec["model_glb"]
        except Exception as exc:
            # EDGE: Align failed entirely -> keep 2D cutouts, surface the error.
            error = f"align failed: {str(exc)[:300]}"
    else:
        trace["stages"]["align"] = {"ok": True, "skipped": "no objects"}

    _stage("describe", lambda: _describe_objects(work_dir, objects, frame_path))

    timings["total"] = round(time.time() - t_start, 2)
    scene = {
        "id": work_dir.name,
        "prompt": prompt,
        "frame": "frame.png",
        "frame_meta": frame_meta,
        "scene_glb": scene_glb,
        "scene_glb_remote": scene_glb_remote,
        "objects": objects,
        "dropped": dropped,
        "segment": {
            "concepts": seg.get("concepts", []),
            "misses": seg.get("misses", []),
            "retried": seg.get("retried", {}),
            "errors": seg.get("errors", []),
        },
        "error": error,
        "timings": timings,
    }

    trace["ok"] = True
    (work_dir / "trace.json").write_text(json.dumps(trace, indent=2))
    # Atomic scene.json write: a crash mid-write must not create a broken cache hit.
    tmp = work_dir / "scene.json.tmp"
    tmp.write_text(json.dumps(scene, indent=2))
    tmp.replace(work_dir / "scene.json")
    return scene
