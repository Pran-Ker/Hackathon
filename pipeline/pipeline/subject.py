"""Stage 1: isolate the subject from a photo (background removal) and, optionally,
attach lightweight semantic metadata (name + tags) via the Anthropic API.

BiRefNet v2 (fal-ai/birefnet/v2) returns an RGBA cutout + a matte mask. The
Anthropic call is SEMANTIC ONLY (what is this object) - never geometry - and is
fully failsafe: any error returns None and never blocks the pipeline.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Optional, TypedDict

import fal_client
import requests
from dotenv import load_dotenv

load_dotenv()

BIREFNET_MODEL = "fal-ai/birefnet/v2"


class SubjectResult(TypedDict):
    rgba: Path
    mask: Path
    cutout_url: str


class SubjectDescription(TypedDict):
    name: str
    tags: list[str]


def _download(url: str, dest: Path) -> Path:
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


def isolate_subject(image_path: str | Path, out_dir: str | Path) -> SubjectResult:
    """Upload `image_path`, run BiRefNet v2, download the RGBA cutout + mask.

    Returns the local paths plus the hosted cutout URL (handy to feed straight
    into an image->3D model without re-uploading).
    """
    image_path = Path(image_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    image_url = fal_client.upload_file(str(image_path))
    # output_mask=True is required or `mask_image` comes back null.
    result = fal_client.subscribe(
        BIREFNET_MODEL,
        arguments={"image_url": image_url, "output_format": "png", "output_mask": True},
    )

    cutout_url = result["image"]["url"]
    stem = image_path.stem
    rgba_path = _download(cutout_url, out_dir / f"{stem}_cutout.png")

    # Pad + center the cutout onto a larger transparent square so the subject
    # never touches the frame edges. This reduces flat "relief on a slab" 3D
    # outputs. Fully failsafe: on ANY error keep cutout_url + rgba_path as-is.
    try:
        from PIL import Image

        PAD_RATIO = 1.35
        with Image.open(rgba_path) as im:
            im = im.convert("RGBA")
            bbox = im.getchannel("A").getbbox()
            if bbox is not None:
                cropped = im.crop(bbox)
                w, h = cropped.size
                side = int(round(max(w, h) * PAD_RATIO))
                side = max(side, w, h, 1)
                canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
                canvas.paste(cropped, ((side - w) // 2, (side - h) // 2), cropped)
                padded_path = out_dir / f"{stem}_cutout_padded.png"
                canvas.save(padded_path)
                padded_url = fal_client.upload_file(str(padded_path))
                rgba_path = padded_path
                cutout_url = padded_url
    except Exception:
        pass

    mask = result.get("mask_image")
    mask_url = mask["url"] if isinstance(mask, dict) and mask.get("url") else cutout_url
    mask_path = _download(mask_url, out_dir / f"{stem}_mask.png")

    return SubjectResult(rgba=rgba_path, mask=mask_path, cutout_url=cutout_url)


def _load_anthropic_key() -> Optional[str]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    # Fallback: the key lives in the user's secrets file, not .env.
    for candidate in (Path.home() / ".local/secrets", Path.home() / ".local/secrets-extra"):
        try:
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or "ANTHROPIC_API_KEY" not in line:
                    continue
                _, _, val = line.partition("ANTHROPIC_API_KEY=")
                val = val.strip().strip('"').strip("'")
                if val:
                    return val
        except OSError:
            continue
    return None


def describe_subject(
    image_path: str | Path,
    model: str = "claude-haiku-4-5-20251001",
) -> Optional[SubjectDescription]:
    """Optional semantic metadata: a short name + a few tags for the subject.

    Fully failsafe - returns None on any error (missing key, network, parse)
    so it can never block Stage 1/2.
    """
    try:
        key = _load_anthropic_key()
        if not key:
            return None

        image_path = Path(image_path)
        media_type = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")

        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 256,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": (
                                    "Identify the single main subject in this image. "
                                    "Respond with ONLY minified JSON of the form "
                                    '{"name": "<short noun phrase>", '
                                    '"tags": ["<tag>", ...]} '
                                    "with 3-6 lowercase descriptive tags. No prose."
                                ),
                            },
                        ],
                    }
                ],
            },
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):]
        data = json.loads(text)
        return SubjectDescription(
            name=str(data.get("name", "")),
            tags=[str(t) for t in data.get("tags", [])],
        )
    except Exception:
        return None
