# Architecture

```
run.pyw -> photovault.app.main()
photovault/
  core/   UI-free logic (no Qt): scanner, dates, dupes, organize, backup, importer,
          health, quarantine, journal, stats, config, db, hashing
  ui/     PySide6 pages; long jobs on a background thread via ui.common.Tasks
  ai/     optional: indexer, search, people/faces, model client/server (state in data/)
tests/    test_core.py (pytest, generated trees), test_ai.py (fakes), ui_smoke.py (page screenshots)
tools/    make_icon.py
```

State: `%LOCALAPPDATA%\PhotoVault\{settings.json,library.db}` (override `PHOTOVAULT_HOME`). SQLite holds the index and the move journal (undo).
Duplicates: xxh3 for exact, dHash for similar; byte-for-byte re-check before any move.
Dates: EXIF -> MP4 header -> filename -> mtime.
Safety model: see `CLAUDE.md`.

## AI subsystem (`photovault/ai/`)
Optional. The GUI never imports numpy/onnxruntime for AI; heavy work runs in a separate worker process.

**Process model**
- `client.AiClient` (GUI side, stdlib only) lazily spawns `data\venv\Scripts\python.exe -m photovault.ai.serve` (`PYTHONPATH` = project, `HF_HOME` = `data\hf`, no console window). `available` is false when that venv python is missing.
- `serve.py` is a persistent worker: one JSON object per line on stdin (`{"id", "cmd", ...}`), one per line on stdout. It keeps the embedding matrix (`search.Index`) and the SigLIP text model loaded. It prints `{"event":"ready"}` once up (the client waits up to 120 s).
- Commands: `search` (parses person names out of the query, encodes the remaining text, ranks), `stats`, `reload`, `recluster`, `assign`, `cancel`, `index`. Replies carry the request `id` plus `ok` or `error`.
- `index` returns immediately and runs `indexer.run` on a thread; progress arrives as id-less events (`listing`, `start`, `progress`, `done`, `index_error`, `index_finished`). The client keeps the last 50 (`last_progress()`). Only one index run at a time; `cancel` sets a flag checked between photos.
- `worker.py` is the same indexer as a one-shot CLI: `python -m photovault.ai.worker index [--limit N] [--sample-every N] [--no-faces] [--cpu] [--workers N]`.

**Indexing (`indexer.py`)**: `list_photos` walks the library roots plus `paths.EXTRA_ROOTS`, skipping the quarantine folder, dot-folders, `.thumbnails`, and folders whose category is screenshots/gaming. Each new or changed file (`seen` table, by path + size + mtime) is read once: xxh3-128 hash, decode (max 1600 px), SigLIP embedding (batches of 16), SCRFD faces + ArcFace embeddings (faces under 28 px dropped), thumbnail, face crops. Identical content that is already embedded only records the path. Unreadable files are logged as `photo.status='error'`. Originals are only read, never modified. Models (`models.py`, ONNX Runtime; DirectML GPU if available, else CPU) are imported only in the worker.

**Search (`search.py`)**: cosine similarity of the query vector against L2-normalised float16 embeddings, optionally filtered by people (a photo must contain all named, non-rejected people) and year. With no text, newest first.

**People (`people.py`)**: SQL-only helpers (name/reject/merge/assign, `parse_query`) are numpy-free for the GUI. `recluster` runs HDBSCAN over unnamed faces only (named faces keep their person); `assign_known` gives unnamed faces to the closest named person (cosine >= 0.42, vote of the 5 nearest).

**`data/` layout** (`paths.py`; `PHOTOVAULT_AI_DATA` overrides; default `<project>/data`, frozen builds use `PHOTOVAULT_PROJECT`)
```
data/
  ai.db                    SQLite (WAL): photo, seen, embedding, person, face, meta; keyed by content hash
  thumbs/<hash>.jpg        384 px thumbnails
  facecrops/<face id>.jpg  144 px face crops
  models/siglip2/, models/faces/   ONNX models
  hf/                      Hugging Face cache
  venv/                    worker virtualenv (Scripts\python.exe)
```
Tests: `tests/test_ai.py` uses synthetic embeddings and fake models with all state under tmp dirs; it skips if numpy/scikit-learn/xxhash/Pillow are missing.
