"""Stage 1b (multiview branch): synthesize back/left/right views of the subject.

Single-view image->3D is ambiguous: a straight-on photo often reconstructs as a
flat relief on a slab instead of a solid volume. Hunyuan3D v3 accepts optional
`back_image_url` / `left_image_url` / `right_image_url` inputs that force it to
reconstruct real volume. We don't have those photos, so we GENERATE them from
the cutout with a parametric novel-view model, then feed all four views in.

Model: fal-ai/flux-2-lora-gallery/multiple-angles
  image_urls        the object image (we pass the RGBA cutout)
  horizontal_angle  0=front, 90=right, 180=back, 270=left
  vertical_angle    camera elevation (0 = eye level)

Fully failsafe: any view that fails is simply omitted, and the caller falls back
to whatever views succeeded (down to single-view = original behavior).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import fal_client

MULTIVIEW_MODEL = "fal-ai/flux-2-lora-gallery/multiple-angles"

# Hunyuan input slot -> horizontal angle (degrees) fed to the view generator.
ORBIT_ANGLES = {
    "right_image_url": 90,
    "back_image_url": 180,
    "left_image_url": 270,
}


def _one_view(image_url: str, angle: int, model: str) -> str | None:
    """Render a single orbit view; return its URL or None on any failure."""
    try:
        result = fal_client.subscribe(
            model,
            arguments={
                "image_urls": [image_url],
                "horizontal_angle": angle,
                "vertical_angle": 0,
                "num_images": 1,
            },
        )
        imgs = result.get("images") or []
        if imgs and isinstance(imgs[0], dict) and imgs[0].get("url"):
            return imgs[0]["url"]
    except Exception:
        return None
    return None


def generate_orbit_views(image_url: str, model: str = MULTIVIEW_MODEL) -> dict[str, str]:
    """Generate back/left/right views for the subject at `image_url`.

    Returns a dict keyed by Hunyuan's input-slot names
    (`back_image_url`, `left_image_url`, `right_image_url`) containing only the
    views that generated successfully. Empty dict means fall back to single-view.
    """
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            slot: pool.submit(_one_view, image_url, angle, model)
            for slot, angle in ORBIT_ANGLES.items()
        }
    views: dict[str, str] = {}
    for slot, future in futures.items():
        url = future.result()
        if url:
            views[slot] = url
    return views
