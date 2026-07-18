# segment — SAM 3 Stage 1 microservice

Input image → N object cutouts → GCS `output/<image_id>/`. The `pipeline` service turns each
cutout into 3D (`POST /imagine`). See [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## Run (standalone)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # FAL_KEY=... , GCS_BUCKET=warping-hologram-pipeline
python server.py            # :8799   (or use ../run-services.sh for both services)
```
GCS auth: ADC (`gcloud auth application-default login`) or `GOOGLE_APPLICATION_CREDENTIALS`.

## Endpoints
- `GET  /health`
- `POST /api/segment` — `{image_url|image_data, prompt, per}` → masks/boxes/labels (per-concept SAM 3 + LLM common-word retry on miss). No upload.
- `POST /api/stage1`  — `{image_url|image_data, prompt, per, bucket, image_id}` → segment **and** upload the cutouts to `gs://<bucket>/output/<image_id>/` (one folder per input image).

`pipeline_core.py` is the fal + GCS core (vendored from `floating-objects`).
