# Deploying POPFRAME to Railway (single service)

One Docker image runs everything:

- **API** — `pipeline/pipeline/server.py` (FastAPI): `/imagine`, `/api/runs`,
  `/api/health`, `/files/...` static outputs.
- **Viewer** — vite build of `viewer/`, served at `/app` (and `/app/mesh.html`).
- **Studio** — committed `studio.html` / `native.html`, served at
  `/files/imagine/studio.html` (seeded into the volume on boot).

The glue is `pipeline/pipeline/deploy_app.py`, which imports the untouched
`pipeline.server:app`, seeds the committed HTML into `outputs/`, and mounts the
viewer dist. The container CMD targets `pipeline.deploy_app:app`.

## Environment variables

| Var | Required | Purpose |
|---|---|---|
| `FAL_KEY` | yes | fal.ai key (Hunyuan3D, SAM 3, image models). `pipeline/generate.py` **raises at import** if unset — the app will not boot without it. |
| `ANTHROPIC_API_KEY` | yes | Claude subject description (`subject.py`). Missing key is non-fatal per request, but set it. |
| `PORT` | set by Railway | Listen port; CMD expands `${PORT:-8000}`. |
| `VIEWER_DIST` | no (baked: `/srv/viewer-dist`) | Viewer static dir for `deploy_app`. |
| `SEED_DIR` | no (baked: `/srv/seed`) | Committed-HTML seed dir for `deploy_app`. |

Never commit or echo these values. Locally they live in `pipeline/.env`
(gitignored); on Railway use service variables.

## Persistent volume (outputs/)

Generations are content-addressed under `outputs/imagine/<sha16>/` and the
manifest is rebuilt from disk on boot — so persistence = mounting one volume.

`server.py` hardcodes `OUTPUTS_DIR` relative to its own source tree
(`Path(__file__).parent.parent / "outputs"`); there is **no env override**.
In this image the source tree is `/srv/pipeline`, so mount the volume at:

```
/srv/pipeline/outputs
```

`deploy_app.py` re-seeds `studio.html` / `native.html` into the (initially
empty) volume on every boot, so the studio UI survives the mount.

### Requested follow-up (do not hack server.py ad hoc)

`server.py` should honor an `OUTPUTS_DIR` env var, e.g.:

```python
OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR") or Path(__file__).resolve().parent.parent / "outputs")
```

Once that lands, mount the Railway volume at `/data` and set
`OUTPUTS_DIR=/data` instead — that decouples persistence from the install
path and matches Railway convention. Until then, use `/srv/pipeline/outputs`.

## Railway CLI steps

```bash
# 0. one-time
npm i -g @railway/cli   # or: brew install railway
railway login

# 1. from the repo root
cd /path/to/Hackathon
railway init            # new project  (or: railway link  to attach to an existing one)

# 2. persistent storage for generations (see note above about the path)
railway volume add --mount-path /srv/pipeline/outputs

# 3. secrets (paste values from pipeline/.env — do not commit them)
railway variables --set "FAL_KEY=<your fal key>" --set "ANTHROPIC_API_KEY=<your anthropic key>"

# 4. deploy (uses railway.json -> Dockerfile; healthcheck /api/health)
railway up

# 5. public URL
railway domain
```

Smoke test after deploy:

```bash
curl https://<your-domain>/api/health
open https://<your-domain>/app/            # viewer
open https://<your-domain>/files/imagine/studio.html   # studio UI
```

## Local Docker test

```bash
cd /path/to/Hackathon
docker build -t popframe .
docker run --rm -p 8000:8000 \
  -e FAL_KEY="$(grep -m1 '^FAL_KEY=' pipeline/.env | cut -d= -f2-)" \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  -v popframe-outputs:/srv/pipeline/outputs \
  popframe

curl http://localhost:8000/api/health
open http://localhost:8000/app/
```

## Notes / gotchas

- **Boot fails fast without FAL_KEY** (import-time RuntimeError in
  `generate.py`). If the Railway deploy crash-loops, check that variable first.
- The image installs `ffmpeg`, `libgl1`, `libglib2.0-0` — not needed by the
  current dependency set (no opencv today) but required the moment video-frame
  extraction or cv2-based segmentation moves into this service.
- `uv sync --frozen` installs from `pipeline/uv.lock`; update the lockfile
  locally (`uv lock` in `pipeline/`) whenever `pyproject.toml` changes, or the
  build fails.
- URLs returned by the API are rooted at the request host, so they work
  unchanged behind Railway's proxy.
- The `segment/` service (SAM 3, port 8799) is **not** in this image; per
  project direction, new backend code should land in the FastAPI service.
- Docker build was **not run** in the authoring environment (no local Docker
  daemon); the Dockerfile is review-verified only. Run the local test above
  before first deploy.
