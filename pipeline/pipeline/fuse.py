"""Splice newly reconstructed Gaussians into an existing world splat.

Deliberately simple, not differentiable local editing (GaussianEditor-style
optimization is per-scene and slow - not a 48h bet). Approach:
  1. Reconstruct the edit patch into its own small splat (reconstruct.py)
  2. Register it into the world's coordinate frame (shared reference frame
     from the click point + camera pose at click time)
  3. Prune existing Gaussians inside a spatial mask (sphere/box around the
     clicked point + some radius)
  4. Concatenate the patch's Gaussians into the world splat

This is a spatial swap, not a blend - accept visible seams for v1, revisit
only if time remains.
"""

from pathlib import Path


def fuse_patch_into_world(world_splat: Path, patch_splat: Path, center, radius: float, out_path: Path) -> Path:
    """Prune world_splat Gaussians within radius of center, splice in patch_splat, write out_path."""
    raise NotImplementedError
