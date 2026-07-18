"""Shared fal + GCS operations — the single source of truth for the hologram pipeline.

Used by both the dashboard (dashboard/server.py, direct calls) and the Temporal
activities (temporal_pipeline/activities.py). fal_client / google-cloud-storage are
imported lazily so importing this module is always cheap and dependency-free.
"""
import base64
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MAX_CONCEPTS = 4


def _url(x):
    return x.get("url") if isinstance(x, dict) else (x if isinstance(x, str) else None)


# ---------- fal input ----------
def fal_upload(data: bytes, content_type: str) -> str:
    import fal_client
    return fal_client.upload(data, content_type)


def data_url_to_fal(data_url: str) -> str:
    header, b64 = data_url.split(",", 1)
    ctype = header.split(";")[0].split(":")[1] if ":" in header else "image/jpeg"
    return fal_upload(base64.b64decode(b64), ctype)


# ---------- segment (SAM 3, per-concept + LLM retry) ----------
def _seg_one(image_url, concept, per):
    import fal_client
    r = fal_client.subscribe("fal-ai/sam-3/image", arguments={
        "image_url": image_url, "prompt": concept, "return_multiple_masks": True,
        "max_masks": min(12, max(per, 3)), "include_boxes": True, "include_scores": True,
    }, with_logs=False)
    masks = [_url(x) for x in (r.get("masks") or [])]
    boxes, scores = r.get("boxes") or [], r.get("scores") or []
    items = []
    for i, m in enumerate(masks):
        if not m or len(items) >= per:
            continue
        items.append({"url": m, "box": boxes[i] if i < len(boxes) else None,
                      "score": scores[i] if i < len(scores) else None})
    return items


def common_word(concept):
    import fal_client
    try:
        r = fal_client.subscribe("fal-ai/any-llm", arguments={
            "model": "google/gemini-2.5-flash-lite",
            "system_prompt": ("Map an object name to ONE more common, generic English noun an "
                              "open-vocabulary image segmenter recognizes. Reply with only that "
                              "single lowercase word — no punctuation, no explanation."),
            "prompt": f"Object: {concept}",
        }, with_logs=False)
        out = (r.get("output") or "").strip().lower()
        return ("".join(ch for ch in out.split()[0] if ch.isalpha()) or None) if out else None
    except Exception:
        return None


def segment(image_url, prompt, per=1, retry=True):
    """One SAM call per comma-concept (max 4, parallel). Misses get one LLM common-word retry.
    Returns parallel arrays (masks/boxes/scores/labels) — masks == mask_urls (the mask image URLs)."""
    concepts = [c.strip() for c in (prompt or "object").split(",") if c.strip()][:MAX_CONCEPTS] or ["object"]
    masks, boxes, scores, labels, misses, retried, errors = [], [], [], [], [], {}, []

    def _take(items, label):
        for it in items:
            masks.append(it["url"]); boxes.append(it["box"]); scores.append(it["score"]); labels.append(label)

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

    return {"image_url": image_url, "masks": masks, "mask_urls": masks, "boxes": boxes,
            "scores": scores, "labels": labels, "misses": misses, "retried": retried,
            "errors": errors, "count": len(masks), "concepts": concepts}


# ---------- reconstruct (SAM 3D) ----------
def reconstruct(image_url, mask_urls, labels=None):
    import fal_client
    args = {"image_url": image_url}
    clean = [m for m in (mask_urls or []) if isinstance(m, str) and m.startswith("http")]
    if clean:
        args["mask_urls"] = clean
    r = fal_client.subscribe("fal-ai/sam-3/3d-objects", arguments=args, with_logs=False)
    glbs = [g for g in (_url(x) for x in (r.get("individual_glbs") or [])) if g]
    splats = [_url(x) for x in (r.get("individual_splats") or [])]
    labels = labels or []
    parts = [{"label": labels[i] if i < len(labels) else f"part_{i}", "glb_url": g,
              "splat_url": splats[i] if i < len(splats) else None} for i, g in enumerate(glbs)]
    return {"image_url": image_url, "model_glb": _url(r.get("model_glb")) or "",
            "individual_glbs": glbs, "parts": parts, "count": len(parts), "used_masks": bool(clean)}


# ---------- publish ----------
def assets_from(seg, rec):
    """Final publishable assets: cutout masks + per-object GLBs + combined scene."""
    assets = [{"label": l, "kind": "image", "url": u} for u, l in zip(seg.get("masks", []), seg.get("labels", []))]
    assets += [{"label": p["label"], "kind": "glb", "url": p["glb_url"]}
               for p in (rec.get("parts") or []) if p.get("glb_url")]
    if rec.get("model_glb"):
        assets.append({"label": "scene", "kind": "glb", "url": rec["model_glb"]})
    return assets


def image_id_for(seed):
    """Stable per-input id (mirrors the Hackathon repo's sha16 keying)."""
    import hashlib
    return hashlib.sha256(str(seed).encode()).hexdigest()[:16]


def upload_to_gcs(assets, bucket, prefix="output", image_id=None):
    """Upload assets to gs://bucket/prefix[/image_id]/. One input image → one image_id
    folder holding its N object cutouts (there can be more than one object)."""
    from google.cloud import storage
    bkt = storage.Client().bucket(bucket)
    base = f"{prefix}/{image_id}" if image_id else prefix
    uploaded = []
    for a in assets:
        url = a.get("url")
        if not url:
            continue
        kind = a.get("kind", "image")
        ext = {"glb": ".glb", "splat": ".ply"}.get(kind, ".png")
        ctype = {"glb": "model/gltf-binary", "splat": "application/octet-stream"}.get(kind, "image/png")
        safe = "".join(ch if ch.isalnum() else "_" for ch in str(a.get("label", "asset")))
        name = f"{base}/{safe}_{len(uploaded)}{ext}".lstrip("/")
        data = urllib.request.urlopen(url, timeout=120).read()
        bkt.blob(name).upload_from_string(data, content_type=ctype)
        uploaded.append({"label": a.get("label"), "kind": kind, "gcs_uri": f"gs://{bucket}/{name}"})
    return uploaded


def stage1(image_url, prompt, per=1, retry=True, bucket=None, image_id=None, prefix="output"):
    """Input + Stage 1 ONLY: segment the original into N object cutouts and upload them
    to a per-image folder gs://<bucket>/<prefix>/<image_id>/. 3D happens downstream
    (Pran-Ker/Hackathon `/imagine` → Hunyuan3D), not here."""
    seg = segment(image_url, prompt, per, retry)
    image_id = image_id or image_id_for(image_url)
    assets = [{"label": l, "kind": "image", "url": u} for u, l in zip(seg["masks"], seg["labels"])]
    uploaded = upload_to_gcs(assets, bucket, prefix, image_id) if (bucket and assets) else []
    return {"image_id": image_id, "objects": len(assets),
            "folder": (f"gs://{bucket}/{prefix}/{image_id}" if bucket else None),
            "segment": seg, "uploaded": uploaded}
