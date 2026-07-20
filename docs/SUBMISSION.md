# Submission — fal x Sequoia 72-Hour Video Hackathon

Developer Track · team **Warping** · submitted 2026-07-19.

- Demo video: [popframe-demo.vercel.app](https://popframe-demo.vercel.app)
- Repo: [github.com/Pran-Ker/Hackathon](https://github.com/Pran-Ker/Hackathon)

## Project description (as submitted)

Every photo is a frozen 3D scene. Hologram unfreezes it.

Upload a photo or a short video and name what you see: "chair, plant,
sneaker". SAM 3 segments each object. SAM 3D Align (`fal-ai/sam-3/3d-objects`)
rebuilds them as meshes and places them in one shared scene. Position and
scale come from the photo, not from us. Claude names each object and writes
its info card.

The viewer freezes the frame, tilts it back, and lifts every object out on a
glowing leader line. Each one stands exactly where it stood in the photo.
Orbit the scene. Click an object and it glows with its card. One more click
swaps it for a hero-quality Hunyuan3D v3 mesh.

A cold scan takes about 40 seconds. Repeat scans return instantly; the backend
caches by content hash. If reconstruction fails you still get 2D cutout cards.
Partial beats nothing.

Stack: fal (SAM 3, SAM 3D Align, Hunyuan3D v3, BiRefNet), Claude, FastAPI,
Three.js.
