# Photo Vault

<p align="center"><img src="icon-256.png" width="96" alt="Photo Vault icon"></p>

[![CI](https://github.com/AmineChr54/PhotoVault/actions/workflows/ci.yml/badge.svg)](https://github.com/AmineChr54/PhotoVault/actions/workflows/ci.yml)

Windows desktop app to de-duplicate, organize, health-check and back up a personal photo library, with an optional
on-device AI layer (search by description, people, tags and events). Python 3.13 + PySide6.
**Your photos never leave your machine.**

## Features
- **Scan**: incremental index of one or more library folders (SQLite).
- **Duplicates**: exact (xxh3) and similar (dHash) groups, re-verified byte-for-byte before anything moves.
- **Organize**: date-based folders (EXIF, then MP4 header, then filename, then mtime), previewed first.
- **Backup**: mirror plus a dated safety archive, every copy hash-verified.
- **Import**, **Health check** (corrupt/zero-byte files), **Quarantine**, **Browse**, **Stats**, and an **Activity log** with undo.
- **AI (optional, runs locally)**: semantic photo **search** in your own words, **people** (face grouping you name),
  automatic **tags** (things, events, people) you can accept or reject, and **events** grouped by time and place.
  Enable it from the AI pages with one click ("Enable AI features", needs Python 3.13 and ~1.5 GB once).
- **Built-in help**: the full user guide lives in the app (press **F1**), plus an **About** dialog.
- **Look and feel**: dark and light themes with a black-and-indigo look, line icons, and **zoom with Ctrl +, Ctrl -, Ctrl 0**
  (70-200 %, remembered).

## Install and run
- **Download:** grab the zip from the [Releases](https://github.com/AmineChr54/PhotoVault/releases) page, unzip, run `PhotoVault.exe`.
- **From source:**
```
pip install -c constraints.txt -r requirements.txt            # app
pip install -c constraints.txt -r requirements-ai.txt         # optional: AI search / people / tags
python run.pyw
```
- **Build the exe yourself:** `powershell -File build.ps1` -> `dist\PhotoVault\PhotoVault.exe` (+ Desktop shortcut).

On first run the app shows a welcome notice: open **Settings**, choose your library folders, the organized-photos
folder and the quarantine folder, then save. New to the app? Press **F1** or read the [user guide](docs/USER_GUIDE.md).

Settings and the library index live in `%LOCALAPPDATA%\PhotoVault` (`settings.json`, `library.db`); override with the
`PHOTOVAULT_HOME` environment variable. AI state (`ai.db`, models, face crops) lives in `data/`
(`%LOCALAPPDATA%\PhotoVault\ai` for the packaged exe).

## Safety rules
- Nothing is deleted directly: files go to the quarantine folder, and only "Send to Recycle Bin" removes them.
- Duplicates are re-checked byte-for-byte right before moving.
- Organize and quarantine moves are logged in SQLite and can be undone (Activity log).
- Backup = mirror + safety archive: files removed/changed on the source are moved to `<target>\_Deleted_Archive\<date>`
  on the backup disk, never deleted. Every copy is hash-verified.

## Development
```
pip install -c constraints.txt -r requirements-dev.txt  # pinned, known-good versions (also used by CI/release)
ruff check .
python -m pytest -q -p no:cacheprovider tests
python tests/ui_smoke.py <out_dir>         # opens every page on a sandbox library, saves screenshots
```
Layout: `photovault/core/` (logic, no UI), `photovault/ui/` (pages; long jobs via `ui.common.Tasks`),
`photovault/ai/` (AI subsystem). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## User guide
Step-by-step instructions for every page: [docs/USER_GUIDE.md](docs/USER_GUIDE.md) (also in the app: **F1**).

## Keyboard shortcuts
| Keys | Action |
|---|---|
| Ctrl + `+` / Ctrl + `=` | Zoom in |
| Ctrl + `-` | Zoom out |
| Ctrl + `0` | Reset zoom |
| F1 | Open Help |

## Credits and licences
AI search uses Google's SigLIP 2 (via ONNX Runtime). Face detection and recognition use InsightFace `buffalo_l`
(SCRFD + ArcFace), which is licensed for non-commercial research use only: fine for a personal library, but check the
terms before distributing the app commercially.

## Project workflow
Work is tracked in GitHub Issues (P0-P3), done by AI agents on `claude/*` branches, auto-merged on green CI, and released
by tag. See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md) and the
[human test checklist](docs/HUMAN_TEST_CHECKLIST.md) for what only a person can verify.

## Updates and privacy
The app asks `api.github.com` for the latest release at most once a day (switch off in Settings); nothing else is sent. Updates are verified against the SHA-256 published with each release.

## License
MIT, see [LICENSE](LICENSE). Optional AI models have their own licences (see the About dialog and the section above).
