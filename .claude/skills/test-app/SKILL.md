---
name: test-app
description: Run Photo Vault's full automated verification - lint, pytest and the UI smoke screenshots - and summarize results. Use before opening a PR or after any change.
---

1. `ruff check .`
2. `python -m pytest -q -p no:cacheprovider tests`
3. `QT_QPA_PLATFORM=offscreen python tests/ui_smoke.py <scratchpad>/ui_out` (use PowerShell `$env:QT_QPA_PLATFORM='offscreen'` on Windows). 
4. If UI code changed, delegate screenshot review to the `ui-verifier` agent. If `photovault/core/` changed, delegate to `safety-reviewer`.
5. Report pass/fail per step with exact failing output. Then list anything a human must still verify (see `docs/HUMAN_TEST_CHECKLIST.md`) - do not claim it is tested.

Never point tests or the smoke script at a real photo library; they use generated sandbox data (set `PHOTOVAULT_HOME` to a temp dir if in doubt).
