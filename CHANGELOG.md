# Changelog

All notable changes. Format: Added / Changed / Fixed. Versions follow semver; tags are `vX.Y.Z`.

## Unreleased
### Added
- Timeline & Places page (#4, first slice): browse the AI index by year > month > event (with counts and a cover thumbnail) and by rounded GPS cell (about 100 km, 10 km or 1 km), then open the selection in Browse. Read-only, offline, no place-name lookup or map picture yet; shows a hint until the AI index exists.
- In-app Help page (#28): the user guide and a What's new section render inside the app (sidebar > Help, or F1 anywhere). New About dialog (sidebar footer or Help page) with version, GitHub link, privacy note and AI model credits (including InsightFace's non-commercial licence). The first-run notice links to Help. The guide and changelog are bundled into the exe.

### Changed
- The window now opens sized to fit your screen (it was larger than a 1440px-wide screen) and remembers its size and position (#14). The Duplicates page no longer forces a minimum window width wider than the screen.
- Open-source release: MIT licence, no hard-coded personal paths (dialogs start in your Pictures folder, restores go to `Pictures\_Restored`, no extra AI index roots), README screenshots removed.
- New app icon: an indigo camera aperture on a near-black tile (#26). README refreshed with screenshots, shortcuts and credits.
- New Umbra-style look (#27): black/indigo dark theme and matching light theme, hairline borders, Segoe UI Variable, outline line icons and an accent-bar active item in the sidebar, brand block with the app icon and version. Status colours and charts follow the theme.
- First run no longer uses hard-coded folders: fresh installs default to the user's Pictures folder with no backup target, and show a first-run notice on Dashboard and Settings until Settings is saved. Existing settings.json files are untouched. Backup refuses to run until a backup folder is chosen.
- Added docs/USER_GUIDE.md (linked from the README).

### Fixed
- "Enable AI features" did nothing when clicked (#1); it now starts the AI setup, and a start failure is shown instead of leaving the button stuck.
- AI Index page crashed on open when the AI environment was already set up (#35).
- CI: UI smoke no longer hangs headless (Search page shows an inline "AI not set up" message instead of a modal dialog).

### Added
- UI zoom (#27): Ctrl+= / Ctrl+- / Ctrl+0 scale the whole interface from 70% to 200%; saved as `ui_zoom` in settings.
- AI: Tags page (tags by kind with counts, accept/reject auto tags with scores, bulk "accept all above", manual add/remove, rename and compute events, group-by-event list) and tag filter chips in Search (AND semantics; tag names in the query also match).
- AI: automatic 'thing' tags (beach, dog, food, ...). A curated vocabulary (`photovault/ai/vocab.py`) is scored against the SigLIP image embedding during indexing (per-label threshold, top 5); `python -m photovault.ai.worker retag` re-scores existing photos. Manual tags and rejections are never overwritten.
- AI auto events: photos are grouped into events by time gaps (>7 h) and GPS jumps (>50 km), named by date range plus top tag or rounded place; new `events` worker command. Renamed events are kept on recompute.
- AI index: `tag` / `photo_tag` tables, photo GPS (`lat`/`lon`) and `taken_ts` (best date, so photos without EXIF dates still have one); existing `ai.db` files are upgraded in place. Re-index to fill GPS/date for old photos.
- First-run AI setup (#4): AI pages show an "Enable AI features" button that creates the worker venv (needs an installed Python 3.13), installs `requirements-ai.txt` with `constraints.txt` and downloads the models with progress, cancel and resume. The exe ships without AI dependencies and stores AI data in `%LOCALAPPDATA%\PhotoVault\ai`.
- `constraints.txt` pins dependency versions for CI and release builds.
- Project framework: CI, auto-merge, release workflow, issue/PR templates, Claude agents and skills, docs.
- Tests for the AI subsystem (store, search ranking, people clustering, indexer with fake models) and AI worker / data layout documentation.

## 0.1.0
- Initial tracked version of Photo Vault (scan, duplicates, organize, backup, import, health, quarantine, AI search/people).
