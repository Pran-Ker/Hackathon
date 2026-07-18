# Promptable World

fal x Sequoia 72-Hour Video Hackathon — Developer Track

A persistent, explorable 3D scene you can edit by pointing and prompting. Generate
a scene, reconstruct it into a navigable Gaussian splat, click anywhere in it, type
what should happen there, and watch that region regenerate and fuse back into the
world — live.

## Why this and not a plain "video to 3D" converter

"Video → Gaussian splat" already exists as a shipped product (Luma AI, Polycam,
KIRI Engine). A reconstructed splat alone is also not a world model — it's a
static, non-parametric snapshot. Nothing in it reacts to anything.

The actual research pattern behind Genie 3 / Voyager / CAT4D / Lyra splits
world-model-like behavior into three separable parts:

- **Imagination** — a diffusion model generates content beyond what was captured
- **Memory** — a persistent, explorable 3D representation holding world state
- **Dynamics** — something that changes the memory in response to an action

fal gives us imagination (video/image diffusion models) cleanly. We build memory
(Gaussian splat reconstruction) and dynamics (click → prompt → regenerate → fuse)
ourselves. That's the project: an explicit, inspectable version of a pattern
frontier labs bundle into one opaque model.

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │              viewer (web)                │
                    │  Spark.js + Three.js, click-to-edit UI   │
                    └───────────────┬───────────────────────────┘
                                    │ click at 3D point + text prompt
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │            pipeline (python)              │
                    │                                           │
                    │  1. generate.py                          │
                    │     fal image/video model                │
                    │     → seed frames for a region             │
                    │                                           │
                    │  2. reconstruct.py                       │
                    │     VGGT (pose + geometry, <1s)          │
                    │     → gsplat (splat optimization)        │
                    │     → .ply                                │
                    │                                           │
                    │  3. fuse.py                               │
                    │     spatial mask around the clicked point │
                    │     prune old Gaussians in that region     │
                    │     splice in newly reconstructed ones     │
                    └─────────────────────────────────────────┘
```

## Build order (safety-net first)

1. **Get the boring path working end-to-end on a real (non-generated) video
   clip first.** VGGT → gsplat → Spark viewer, static scene, no editing. This
   de-risks the whole demo before touching generative content.
2. Swap the real video for a fal-generated one. Prompt for a looping/wide-arc
   camera move (orbit, not a straight push-in) — narrow-baseline frames and
   diffusion's lack of hard 3D consistency are the most likely failure points.
   Keep the demo scene static (no people, no moving objects) — 3DGS assumes a
   static scene, dynamic content needs a much heavier 4D pipeline.
3. Add the click-to-edit loop: click point in viewer → render current view from
   that camera → fal diffusion regenerates that patch → re-reconstruct just the
   new frames → fuse into the existing splat by spatial mask.
4. Polish: a "render this camera path as a shareable video" export doubles as
   demo-video fallback content and as a literal video deliverable if the
   interactive demo is flaky during recording.
5. Stretch only if 1-4 land early: swap the "regenerate + fuse" dynamics layer
   for a tiny fine-tuned DIAMOND-style action-conditioned diffusion model for
   true frame-level dynamics. Real training risk — don't bet the submission on
   this.

## Judging rubric alignment (Developer Track)

- **Creativity (25%)** — no open hackathon-scale implementation of an
  editable persistent Gaussian world exists; this isn't a rebrand of an
  existing "video to 3D" product.
- **User value (25%)** — iterative scene-sketching tool for game/VFX previs;
  directly satisfies "make creating/editing AI-generated video faster, easier,
  more powerful."
- **Technical execution (35%)** — real pose/geometry estimation, splat
  optimization, spatial fusion, and a real-time interactive viewer, wired
  together live.
- **Demo (15%)** — a live click-to-edit moment in a 3D scene is a genuinely
  watchable beat, not just a video playing.

## Repo layout

- `pipeline/` — Python: fal API orchestration, VGGT/gsplat reconstruction, fusion logic
- `viewer/` — Web: Spark.js viewer with click-to-edit UI
- `scripts/` — setup / one-off utility scripts

## Setup

```bash
# pipeline
cd pipeline
uv sync
cp .env.example .env   # fill in FAL_KEY

# viewer
cd ../viewer
npm install
npm run dev
```

## Submission checklist (Developer Track)

- [ ] Public GitHub repo (this one)
- [ ] ≤3 min demo video
- [ ] Project description (100–200 words)
- [ ] Everything genuinely built during the hackathon window (Jul 17 12:30pm PT
      – Jul 19 9:00am PT) — open-source libraries/pretrained models (VGGT, gsplat,
      Spark.js) are tooling, not "previous work"; keep commit history honest.
