# P&ID Extraction API

FastAPI service implementing the pipeline: PDF → page images → CV symbol
detection → OCR/VLM text extraction → rule-based (+ optional GNN)
relationship inference → structured digital twin → pluggable database.

## Quickstart (local, no Docker)

```bash
pip install -r requirements.txt --break-system-packages   # or use a venv
cp .env.example .env
# Edit .env — at minimum, set DATABASE_TYPE and DATABASE_URL for a DB you
# actually have running. SQLite is NOT supported out of the box (spec calls
# for MSSQL/Postgres/MySQL/Mongo/Neo4j); easiest local option is Postgres:
#   DATABASE_TYPE=postgres
#   DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/pid_extraction

uvicorn app.main:app --reload
# API docs: http://localhost:8000/docs
# Unknown-symbol labeling UI: http://localhost:8000/ui
```

## Quickstart (Docker Compose — full stack incl. MSSQL + Redis + Celery worker)

```bash
cp .env.example .env
docker compose up --build
```

## What's real vs. what's a placeholder — read this before demoing

This is a complete, runnable service architecture: every endpoint, the
pluggable DB layer, the human-in-the-loop flow, and the rule-based
relationship engine work end-to-end against real input. Two pieces are
intentionally stubbed because they require assets this repo can't ship:

1. **CV symbol detection** (`app/services/cv/symbol_detector.py`) expects a
   YOLOv8 model fine-tuned on YOUR P&ID symbol set at `YOLO_MODEL_PATH`. No
   such weights exist yet — training one needs a labeled symbol dataset
   (Roboflow has some public P&ID symbol sets to start from) and
   `training/prepare_yolo_dataset.py` (see "Training the CV symbol
   detector" below) to turn it into an augmented, ultralytics-ready
   dataset. Without weights configured, the service falls back to a
   contour-based heuristic detector that finds plausible symbol-sized
   regions and marks them all as low-confidence "unknown" — every one
   routes to the human-in-the-loop labeling flow rather than guessing.
   This makes the pipeline usable end-to-end today; classification quality
   is only as good as the model you eventually drop in.

2. **GNN relationship refinement** (`app/services/gnn/gnn_model.py`) is
   architecturally wired (PyTorch Geometric, USE_GNN=true) and now has a
   real training loop (`app/services/gnn/train_gnn.py`), but still no
   trained weights — there's no public P&ID connectivity graph corpus to
   pretrain on. **Relationships are actually produced by the rule-based
   engine** (`app/services/gnn/rule_based_relations.py`): tag proximity,
   ISA-5.1 function-letter conventions, and Hough-based line tracing with
   endpoint snapping. This is the correct primary path, not a placeholder
   — see "Training the relationship GNN" below for what it takes to
   bootstrap a real dataset (human-in-the-loop labels + expert-corrected
   rule-based output) once you've accumulated it from real usage.

Everything else — the FastAPI endpoints, the five pluggable DB backends,
symbol-dictionary learning via shape-signature matching, job pause/resume,
Docker Compose stack, and the labeling frontend — is functional as written.

## Architecture

```
/app
  /api/routes     - extract, jobs, symbols (human-in-the-loop), projects, db_admin
  /core           - config, logging, error handling
  /db             - BaseRepository + SQL (mssql/postgres/mysql) / Mongo / Neo4j implementations
  /models         - SQLAlchemy ORM models (SQL backends)
  /schemas        - Pydantic request/response contracts
  /services
    /cv           - symbol detection + shape-signature matching
    /ocr          - OCR + VLM fallback text extraction
    /vlm          - Ollama client + the standalone "expert P&ID analyst" full-page analysis
    /gnn          - rule-based relationship inference + optional GNN refinement
    /extraction   - PDF rendering, line tracing, classification, orchestrator, job runner
  main.py
  celery_app.py   - optional async worker path (USE_CELERY=true)
frontend/index.html - unknown-symbol labeling demo UI
migrations        - Alembic migrations (schema source of truth: app/models/orm.py)
training           - offline dataset prep for fine-tuning the YOLOv8 symbol detector
utilities/duplicate_tag_finder.py - standalone duplicate-tag scan, not tied to a job
```

## Expert P&ID analyst (VLM, local via Ollama — no API key)

`POST /vlm/analyze` (upload one page image) and
`GET /vlm/analyze-page/{job_id}/{page_number}` (reuse a page already
rendered by an earlier `/extract` job) run a strict, single-shot
vision-language-model pass over one P&ID page — or, if you pass
`x1`/`y1`/`x2`/`y2` query params, just a region of it — and extract
structured engineering data straight into four tables, independent of the
CV/OCR/GNN pipeline:

```json
{
  "instruments": [
    {
      "instrument_tag": "PT-101A",
      "instrument_type": "Pressure Transmitter",
      "identification": "PT = Pressure Transmitter (ISA-5.1)",
      "location": "local / panel / shared",
      "connected_to": ["equipment or line tags"],
      "page_number": 1,
      "bbox": [x1, y1, x2, y2],
      "attributes": {}
    }
  ],
  "equipment": [
    {
      "equipment_tag": "P-101A",
      "equipment_type": "Centrifugal Pump",
      "identification": "full description",
      "capacity": "50 m3/h",
      "other_data": {"material": "", "design_pressure": "", "design_temperature": "", "power": "", "notes": ""},
      "page_number": 1,
      "bbox": [x1, y1, x2, y2]
    }
  ],
  "pipe_runs": [
    {
      "pipe_run_tag": "4\"-P-1012-A1A",
      "size": "4\"",
      "fluid_code": "P",
      "pipe_material_spec": "A106 Gr.B / CS",
      "insulation": "no",
      "insulation_thickness": "",
      "other_information": {"piping_class": "", "from": "", "to": "", "design_pressure": "", "design_temperature": "", "notes": ""},
      "page_number": 1
    }
  ],
  "piping_components": [
    {
      "component_tag": "V-1012",
      "piping_component_type": "Gate Valve",
      "connected_pipe_run": "4\"-P-1012-A1A",
      "size": "4\"",
      "other_information": {"rating": "150#", "material": "", "end_connection": "Flanged", "notes": ""},
      "page_number": 1,
      "bbox": [x1, y1, x2, y2]
    }
  ]
}
```

classified per the standards the model is instructed to apply:

- **Instruments** — tagged/identified per ISA-5.1 (PT, FT, LT, TT, PIC,
  FIC, XV, HV, PV, ...), with mounting/location (local/panel/shared) and
  the equipment or line tags it's connected to.
- **Equipment** — pumps, tanks, vessels, heat exchangers, columns,
  compressors, filters, etc., per common PIP + ISA symbol conventions,
  with capacity and design data where legible.
- **Pipe runs** — main process/utility/signal lines, with size, fluid
  code, material spec, insulation, and piping class / from / to where
  visible. No `bbox`: a run is a whole line (often multiple segments
  across the page), not a single point symbol.
- **Piping components** — valves (gate/globe/ball/butterfly/check/
  control/relief...), fittings (elbow/tee/reducer/flange/strainer/
  spectacle blind...), and specials (orifice plate, rupture disc, ...),
  each linked back to its `connected_pipe_run` tag.

Every row's `page_number` is set by the API from the page actually being
analyzed (not left to the model to guess). `bbox` values are always in the
**original full-page pixel coordinate space**, even when a region was
analyzed — the crop offset is added back so results line up with the CV
pipeline. Nothing is guessed: a field the model can't clearly read on the
drawing comes back `null`/empty rather than invented.

Runs against a **local Ollama server by default** (`VLM_PROVIDER=ollama`)
— no API key, no cloud call:

```bash
# 1. Install Ollama: https://ollama.com
# 2. Pull a vision-capable model
ollama pull qwen3-vl:4b           # small, fast, strong text/OCR accuracy (default)
# ollama pull qwen2.5vl
# ollama pull llama3.2-vision
# ollama pull llava               # smallest/most broadly available if VRAM-limited
# 3. Start the server
ollama serve
# 4. Point the API at it (defaults already match this)
# OLLAMA_BASE_URL=http://localhost:11434
# OLLAMA_VLM_MODEL=qwen3-vl:4b
```

`VLM_PROVIDER=anthropic` / `openai` are still supported for the same
endpoints (and for the OCR low-confidence-crop fallback in
`app/services/ocr/text_extractor.py`) — those two do require the matching
API key in `.env`. `VLM_PROVIDER=none` disables VLM calls entirely; the
`/vlm/analyze*` endpoints then return all four tables empty with an
explanation in `extraction_notes` instead of failing.

If the model returns malformed/non-schema JSON, the service retries with a
corrective prompt (`OLLAMA_JSON_RETRY_ATTEMPTS`, default 2 extra attempts)
before giving up and returning an empty-tables result rather than a 500 —
a bad page never takes down a batch.

## Unknown/non-standard symbol teaching (VLM-assisted, human-confirmed)

Any symbol the CV pipeline or the expert analyst couldn't confidently
classify against ISA-5.1/PIP can be taught to the system in two steps —
an optional AI-assisted suggestion, then a human-confirmed save:

**1. `POST /vlm/suggest-teaching`** (multipart: crop image +
`nearby_text`, `proposed_class`, `confidence`) — asks the VLM to propose a
classification for a human to review, in this exact contract:

```json
{
  "action": "teach",
  "new_class_name": "e.g. Special Ball Valve - Fire Safe",
  "category": "instrument | equipment | piping_component | pipe_run | other",
  "standard_reference": "None / Custom / Company Standard",
  "description": "detailed description",
  "recommended_table": "instruments | equipment | piping_components | pipe_runs",
  "default_attributes": {"key": "value"},
  "isa_equivalent": "closest ISA-5.1 code if any",
  "pip_equivalent": "closest PIP symbol if any"
}
```

This is a *suggestion only* — nothing is written to the database here. If
the VLM can't produce one, the response comes back with `suggestion: null`
and a `notes` explanation instead of an error, so the frontend can fall
back to an empty popup.

**2. `POST /vlm/teach-symbol`** (multipart: crop image + the
Human-in-the-Loop popup fields — `category`, `name_type`, `tag_format`,
`description`, `recommended_table`, plus the optional AI-suggested
`standard_reference`/`isa_equivalent`/`pip_equivalent`/
`default_attributes` if the human confirmed a suggestion) — this is the
actual save, matching the popup:

> This symbol is not recognized according to ISA-5.1 / PIP standards.
> Please teach the system: **Category** · **Name / Type** · **Tag format
> (if any)** · **Description** · **Which table should this data go into?**

On submit the system:
1. Adds the symbol to `symbol_dictionary` (reusing the same table/repo
   method the CV pipeline's `POST /label-unknown-symbol` flow already
   writes to — `source="vlm_taught"` distinguishes provenance).
2. Saves the example crop under `CROP_DIR`.
3. Computes a shape signature from the crop (same mechanism as
   `POST /label-unknown-symbol`) so a visually similar symbol is
   auto-resolved next time instead of asking a human twice.

## Switching database backends

Set `DATABASE_TYPE` (mssql|postgres|mysql|mongo|neo4j) and the matching
connection string in `.env`, or call `POST /connect-db` at runtime to
hot-swap the active backend without restarting the process.

## Duplicate tag finder

`GET /tags/duplicates?project_id=...` (omit `project_id` to scan the whole
database) returns every tag that appears on more than one instrument/
equipment entity — a standalone utility per the original spec, not tied to
any single extraction job.

## Database migrations

Schema is managed by Alembic, generated from `app/models/orm.py` (the one
source of truth — never hand-edit a migration's table shape out of sync
with the ORM). Targets mssql and postgres (mysql works through the same
SQLAlchemy engine but isn't part of the project's own testing).

```bash
alembic upgrade head                              # apply, using DATABASE_URL from .env
DATABASE_URL="postgresql+psycopg2://..." alembic upgrade head   # or target explicitly
alembic revision --autogenerate -m "add xyz column"              # after changing orm.py
```

`migrations/versions/0001_initial_schema.py` creates every table in the
"minimum tables" list (projects, pages, symbols, instruments, equipment,
lines, relationships, symbol_dictionary, extraction_jobs, unknown_symbols)
with indexes on every tag/lookup field (`tag`, `line_number`,
`isa_type_code`, `category_name`, the relationship endpoint columns, etc.)
and the FKs described in the ORM.

## Training the CV symbol detector

`symbol_detector.py` expects a fine-tuned YOLOv8 `.pt` file — it ships with
none (see that file's docstring). To fine-tune one from your own labeled
P&ID pages:

```bash
python training/prepare_yolo_dataset.py \
    --images-dir ./raw/images --labels-dir ./raw/labels \
    --symbol-dict ./raw/symbol_dictionary.json \
    --output-dir ./yolo_dataset \
    --augmentations-per-image 4 --val-split 0.15 --test-split 0.1

yolo detect train data=./yolo_dataset/data.yaml model=yolov8n.pt epochs=100
```

Augmentation is tuned for line-art engineering drawings, not natural
photos: small-angle rotation only (no flips — text/bubbles are only
meaningful upright), light perspective warp, scan/photocopy-style noise,
and a custom line-thickness jitter that simulates different plotter/scan
stroke widths. See the script's module docstring for why each of those
choices matters for P&ID symbols specifically.

## Training the relationship GNN

`gnn_model.py`'s GNN refinement pass (`USE_GNN=true`) also ships untrained
by default — the rule-based engine (`rule_based_relations.py`) is the
primary path. To train one once you've accumulated human-in-the-loop
symbol labels and expert-corrected rule-based relationship output:

```bash
python -m app.services.gnn.train_gnn \
    --dataset ./gnn_training_data.json \
    --output ./models_weights/pid_relations_gnn.pt \
    --epochs 100 --val-split 0.15
```

See `train_gnn.py`'s docstring for the dataset JSON shape. Node/edge
feature encoding is shared between training and inference via
`relationship_gnn_model.py` so the two can't drift apart. Either path
(rule-based or GNN) can be turned into a persistable graph with
`gnn_model.infer_graph(...)` → `graph_export.graph_to_relationship_records(...)`,
which returns dicts shaped for `RelationshipEdge` rows.

## Known gaps / next steps if you take this to production

- No auth on any endpoint — add an API key or OAuth dependency before
  exposing this beyond localhost.
- `find_duplicate_tags` doesn't include `lines` on the Mongo/Neo4j backends
  yet (SQL backend also currently checks instruments + equipment only —
  extend if line-number collisions matter to you).
- No automatic retry/backoff on VLM API calls.
- No model-versioning story for the CV/GNN weights once you do train them.
- Detectron2 install is not pip-only (see requirements.txt comment) — needs
  a build step if you pick that framework over YOLO.
