"""fal calls that produce the seed frames for a scene or a local edit patch.

Model choice notes (from research, verify current IDs on fal.ai/models before use):
- Wide scene establishing shot: a fal video model prompted for a looping/wide-arc
  orbit, e.g. fal-ai/kling-video/v2.6/standard (image-to-video) or fal-ai/veo3.1.
  fal has no parametric camera-trajectory API - control is prompt-driven only.
- Local edit patch: fal-ai/flux-2-lora-gallery/multiple-angles for parametric
  azimuth/elevation stills if the edit region is object-like, otherwise an
  image-to-image / inpaint model conditioned on the rendered current view.
"""

import os
from pathlib import Path

import fal_client
import requests
from dotenv import load_dotenv

load_dotenv()

if not os.environ.get("FAL_KEY"):
    raise RuntimeError("FAL_KEY not set - copy .env.example to .env and fill it in")

# image->3D model ids, tried in order until one succeeds.
IMAGE_TO_3D_MODELS = (
    "fal-ai/hunyuan3d-v3/image-to-3d",
    "fal-ai/hunyuan3d/v2",
    "fal-ai/hunyuan3d/v2/mini",
)


def generate_scene_video(prompt: str, model: str = "fal-ai/kling-video/v2.6/standard/text-to-video") -> str:
    """Generate the establishing video for a new scene. Returns a video URL."""
    result = fal_client.subscribe(model, arguments={"prompt": prompt})
    return result["video"]["url"]


def generate_edit_preview(prompt: str, model: str = "fal-ai/fast-sdxl") -> str:
    """Stand-in for the real edit step until reconstruct.py/fuse.py exist.

    Takes just the click prompt (no scene conditioning yet) and returns a
    single generated image URL. This is NOT the real pipeline - there's no
    scene context, no multi-view, no reconstruction, no fusion into the
    world splat. It exists so the click -> server -> fal -> back-to-viewer
    round trip is real and testable before the 3D half is built.
    """
    # num_inference_steps=4 + the default safety checker produced false-positive
    # NSFW flags on entirely benign prompts (low-step noise trips the
    # classifier), silently returning a blacked-out image. 25 steps fixes the
    # false positive on its own; safety checker is off here for fast dev
    # iteration - revisit before the real submission given the hackathon's
    # "nothing explicit" content rule.
    result = fal_client.subscribe(
        model,
        arguments={
            "prompt": prompt,
            "image_size": "square",
            "num_inference_steps": 25,
            "enable_safety_checker": False,
        },
    )
    return result["images"][0]["url"]


def _extract_glb_url(result: dict) -> str:
    """Pull the GLB url out of the many shapes fal image->3D models return."""
    for key in ("model_mesh", "model_glb", "mesh", "glb"):
        val = result.get(key)
        if isinstance(val, dict) and val.get("url"):
            return val["url"]
        if isinstance(val, str) and val.startswith("http"):
            return val
    # last resort: any nested {"url": ...} whose url looks like a .glb
    for val in result.values():
        if isinstance(val, dict) and isinstance(val.get("url"), str) and ".glb" in val["url"]:
            return val["url"]
    raise ValueError(f"no GLB url found in result keys={list(result.keys())}")


def image_to_3d(image_url_or_path: str, out_path: str | Path, model: str | None = None) -> Path:
    """Generate a 3D mesh (GLB) from a single (ideally background-removed) image.

    `image_url_or_path` may be an already-hosted url or a local file (uploaded
    automatically). If `model` is given only that id is tried; otherwise the
    ids in IMAGE_TO_3D_MODELS are tried in order until one succeeds. Downloads
    the resulting GLB to `out_path` and returns it.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if image_url_or_path.startswith("http"):
        image_url = image_url_or_path
    else:
        image_url = fal_client.upload_file(image_url_or_path)

    candidates = (model,) if model else IMAGE_TO_3D_MODELS
    errors: list[str] = []
    for mid in candidates:
        try:
            # Hunyuan3D image->3D models take `input_image_url` (not `image_url`).
            # Without explicit texture flags v3 defaults to geometry-only (white,
            # no baseColor) - so request the textured/PBR path per model. Verified
            # against the live schema: v3 uses generate_type/enable_pbr; the v2
            # family uses textured_mesh.
            args = {"input_image_url": image_url}
            if "hunyuan3d-v3" in mid:
                args["generate_type"] = "Normal"  # Normal=textured (vs LowPoly/Geometry)
                args["enable_pbr"] = True
            else:
                args["textured_mesh"] = True
            result = fal_client.subscribe(mid, arguments=args)
            glb_url = _extract_glb_url(result)
            resp = requests.get(glb_url, timeout=300)
            resp.raise_for_status()
            out_path.write_bytes(resp.content)
            return out_path
        except Exception as exc:  # noqa: BLE001 - try next model
            errors.append(f"{mid}: {exc}")
            continue
    raise RuntimeError("all image->3D models failed:\n" + "\n".join(errors))


def generate_edit_patch(prompt: str, reference_image_url: str, model: str = "fal-ai/flux-2-lora-gallery/multiple-angles") -> list[str]:
    """Generate the multi-view patch used to regenerate one region of the scene.

    reference_image_url should be the current render of the scene from the
    clicked viewpoint (viewer renders this and uploads it).
    """
    result = fal_client.subscribe(
        model,
        arguments={"prompt": prompt, "image_url": reference_image_url},
    )
    return [img["url"] for img in result.get("images", [])]
