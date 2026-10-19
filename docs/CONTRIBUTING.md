# How work flows

1. **Issue** - every change starts as a GitHub issue with a priority (`P0-critical`..`P3-low`), type, `area:*`, and `ai-ready` or `needs-human-test`. Use `/file-issue`; `issue-triager` keeps the backlog ordered.
2. **Branch & PR** - AI works on `claude/<issue>-<slug>`, never on `main`. PR body says `Closes #n`.
3. **CI** (`.github/workflows/ci.yml`) - ruff, pytest, UI smoke on Windows. PRs from `claude/*` branches auto-squash-merge when green.
4. **Safety** - changes in `photovault/core/` must pass the `safety-reviewer` agent (see invariants in `CLAUDE.md`).
5. **Human tests** - anything unverifiable by AI goes in `docs/HUMAN_TEST_CHECKLIST.md`.
6. **Release** - `/release` -> version bump + changelog PR -> tag `vX.Y.Z` -> Actions builds the exe zip and publishes the GitHub Release.

Priority rubric: P0 can lose/corrupt photos or break undo; P1 crash / main flow broken; P2 degraded UX; P3 polish.

Autonomous mode: `/burn-down-issues` works the backlog; `/loop` or `/schedule` can run it on a timer.
