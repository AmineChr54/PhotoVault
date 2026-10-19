# Photo Vault

Windows desktop app (Python 3.13 + PySide6) to de-duplicate, organize, health-check and back up a personal photo library.
Settings and index live in `%LOCALAPPDATA%\PhotoVault` (`PHOTOVAULT_HOME` overrides). Version: `photovault/__init__.py`.

## Commands
- Run: `python run.pyw`
- Tests: `python -m pytest -q -p no:cacheprovider tests`
- Lint: `ruff check .`
- UI smoke (real window on a sandbox library, screenshots of every page): `python tests/ui_smoke.py <out_dir>`
- Build exe locally: `powershell -File build.ps1` (runs tests first; also makes a Desktop shortcut)
- Install deps: `pip install -c constraints.txt -r requirements-dev.txt`

## Architecture
- `photovault/core/` - all logic, **no Qt imports**: scanner, dates, dupes, organize, backup, importer, health, quarantine, journal, stats, config, db, hashing.
- `photovault/ui/` - PySide6 pages (`pages_a/b/c/ai.py`); long jobs run on a background thread via `ui.common.Tasks`.
- `photovault/ai/` - optional AI subsystem (indexer, search, people/faces, model client/server). State in `data/`.
- `tests/test_core.py` - pytest on generated folder trees. `tests/ui_smoke.py` - UI screenshots.

## Safety invariants (never break these - they protect the user's photos)
1. Nothing is deleted directly. Files go to quarantine; only "Send to Recycle Bin" removes them.
2. Duplicates are re-verified byte-for-byte immediately before any move.
3. Every organize/quarantine move is journaled in SQLite and undoable.
4. Backup = mirror + `_Deleted_Archive\<date>` safety archive; every copy is hash-verified; nothing on the target is deleted.
Any change to `photovault/core/` needs a test and a `safety-reviewer` pass. Never test against the user's real library.

## Workflow (AI-autonomous)
- Work is tracked in GitHub Issues. Priorities: **P0** can lose/corrupt photos or break undo; **P1** crash / main flow broken; **P2** degraded UX; **P3** polish/ideas. Labels: `P0-critical`..`P3-low`, `bug`, `feature`, `needs-human-test`, `ai-ready`, `area:core|ui|ai|build`.
- Never push to `main`. Branch `claude/<issue#>-<slug>` -> PR (`Closes #n`) -> CI green -> auto-squash-merge.
- Everything the AI cannot verify (real libraries, external disks, visual feel, model downloads, clean-machine exe) goes in `docs/HUMAN_TEST_CHECKLIST.md` and gets a `needs-human-test` issue.
- Releases: `/release` bumps the version, updates `CHANGELOG.md`, tags `vX.Y.Z`; GitHub Actions builds and publishes the exe zip.
- Skills: `/test-app`, `/file-issue`, `/burn-down-issues`, `/release`, `/release-checklist`. Agents: `safety-reviewer`, `ui-verifier`, `issue-triager`, `release-manager`.

## Conventions
- Match the surrounding code style. Keep core UI-free. Add/adjust tests with every behavior change; update `CHANGELOG.md` (Unreleased) for user-visible changes.
