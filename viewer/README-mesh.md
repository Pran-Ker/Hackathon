# Mesh — image → 3D component

The mesh path turns a single product photo into an optimized 3D GLB (plus a
typed react-three-fiber component). `mesh.html` is the viewer; the pipeline API
does the generation.

## Run both servers

```bash
# 1. Pipeline API (image → 3D). Restart this after editing server.py.
cd pipeline
uv run uvicorn pipeline.server:app --port 8000
#   stale process? lsof -ti:8000 | xargs kill -9

# 2. Viewer (Vite dev, hot-reloads automatically).
cd viewer
npm run dev            # http://localhost:5173/mesh.html
```

Open **http://localhost:5173/mesh.html**. `npm run build` produces a static
`dist/` with both `index.html` (splat) and `mesh.html`.

## Demo preset flow (the reliable path)

The bottom bar has instant demo presets that load pre-baked GLBs from
`viewer/public/` with **no wait**: **Sneaker**, **Chair**, **Plant**. Sneaker
loads on startup. Use these to guarantee something on screen; the live drag-drop
gen is the "wow" but takes minutes.

The Plant button auto-hides if `viewer/public/plant.glb` is missing.

## Live generation (drag & drop)

Drag any image anywhere onto the window. It POSTs to `/imagine` and loads the
returned GLB. The endpoint is **synchronous (~2–3 min)**, so the viewer shows a
progress card with an elapsed timer and staged labels (isolating subject →
generating 3D mesh → compiling & optimizing) driven on a time estimate — the
sync endpoint reports no real progress. The previously loaded model stays dimmed
behind the card. On success a caption shows the detected name/tags and the
`size_before → size_after` reduction. On any failure an error toast appears and
the current model is kept (never wiped).

## `/imagine` contract

```
POST http://localhost:8000/imagine
  multipart/form-data, field "image" = the photo (PNG/JPG/WEBP)

200 OK
{
  "glb_url":       "http://localhost:8000/files/imagine/<id>/compiled/....glb",
  "component_url": "http://localhost:8000/files/imagine/<id>/compiled/....tsx",
  "name":          "sneaker" | null,
  "tags":          ["shoe", "footwear", ...],
  "size_before":   <bytes, raw GLB>,
  "size_after":    <bytes, optimized GLB>
}

400 { "error": "Upload must be an image (PNG, JPG, or WEBP)." }   # bad/empty upload
500 { "error": "3D generation failed: <detail>" }                # pipeline error
```

Errors are JSON (never an HTML 500 page) and go through CORS so the viewer can
read `error` off the body. Static outputs are mounted at `/files`. CORS allows
`http://localhost:5173` and `:5174`.
