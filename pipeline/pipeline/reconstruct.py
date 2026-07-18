"""Frames -> Gaussian splat.

Stack (from research): VGGT for pose + geometry recovery (sub-second, exports
COLMAP-format), then gsplat for the actual splat optimization (minutes,
depends on scene complexity and iteration count).

VGGT:   https://github.com/facebookresearch/vggt
gsplat: https://docs.gsplat.studio/main/

Both are separate installs (not on PyPI as a single package) - pull them in
as git submodules or pip-install from source during setup; not vendored here.
"""

from pathlib import Path


def frames_to_splat(frame_paths: list[Path], out_path: Path) -> Path:
    """Run VGGT -> gsplat on a list of frames, write a .ply to out_path.

    Implementation TODO during hackathon:
      1. VGGT inference on frame_paths -> camera poses + point map
      2. Write COLMAP-format cameras/images/points3D
      3. gsplat training loop on that COLMAP data -> Gaussians
      4. Export Gaussians to out_path as .ply
    """
    raise NotImplementedError


def sample_frames(video_path: Path, out_dir: Path, every_n: int = 5) -> list[Path]:
    """Extract frames from a generated video for reconstruction input.

    Narrow baseline between consecutive frames hurts reconstruction quality -
    tune every_n against the scene's camera speed, don't just dump every frame.
    Uses ffmpeg; requires ffmpeg on PATH.
    """
    import subprocess

    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-i", str(video_path),
            "-vf", f"select=not(mod(n\\,{every_n}))",
            "-vsync", "vfr",
            str(out_dir / "frame_%04d.png"),
        ],
        check=True,
    )
    return sorted(out_dir.glob("frame_*.png"))
