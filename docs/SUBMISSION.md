# Submission — project description (Developer Track)

Draft for the 100–200 word form field. Word count: ~160.

---

POPFRAME turns any photo or video into a hologram. Name the objects you care
about ("person, dog, chair") and the frame freezes on screen while those
objects lift out of it as real 3D meshes — still positioned and scaled exactly
as they were in the shot — each on a glowing leader line with an AI-written
info card.

Under the hood: SAM 3 segments each named concept (with an LLM retry that
rewrites concepts SAM can't find), SAM 3D Align reconstructs and places all
objects in one shared scene, Claude writes the cards, and a Three.js viewer
does the pop-out. Any object can then be "hero upgraded" through a second
pipeline (BiRefNet isolate, orbit multiview, Hunyuan3D v3) into a
presentation-quality mesh.

Everything is content-addressed and idempotent — a repeated photo returns
instantly from cache — and every failure degrades gracefully: if 3D fails you
still get 2D cutout cards. A scene builds in about 40 seconds; a hero mesh in
about three minutes. Built entirely on fal-hosted models.
