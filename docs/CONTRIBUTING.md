# How work flows

1. **Issue** - every change starts as a GitHub issue with a priority (`P0-critical`..`P3-low`), type, `area:*`, and `ai-ready` or `needs-human-test`. Use `/file-issue`; `issue-triager` keeps the backlog ordered.
2. **Branch & PR** - AI works on `claude/<issue>-<slug>`, never on `main`. PR body says `Closes #n`.
3. **CI** (`.github/workflows/ci.yml`) - ruff, pytest, UI smoke on Windows. PRs from `claude/*` branches auto-squash-merge when green.
4. **Safety** - changes in `photovault/core/` must pass the `safety-reviewer` agent (see invariants in `CLAUDE.md`).
5. **Human tests** - anything unverifiable by AI goes in `docs/HUMAN_TEST_CHECKLIST.md`.
6. **Release** - `/release` -> version bump + changelog PR -> tag `vX.Y.Z` -> Actions builds the exe zip and publishes the GitHub Release.

Priority rubric: P0 can lose/corrupt photos or break undo; P1 crash / main flow broken; P2 degraded UX; P3 polish.

Autonomous mode: `/burn-down-issues` works the backlog; `/loop` or `/schedule` can run it on a timer.

# Getting started as a collaborator

1. **Clone and install**: `git clone https://github.com/AmineChr54/PhotoVault.git`, then `pip install -c constraints.txt -r requirements-dev.txt` (Python 3.13, Windows).
2. **Check it works**: `ruff check .` and `python -m pytest -q -p no:cacheprovider tests`. Run the app with `python run.pyw`.
3. **Pick an issue** (`ai-ready` ones are well-scoped) and comment that you are taking it. Branch from `main` as `claude/<issue#>-<slug>` for AI-assisted work (the auto-merge workflow only acts on that prefix) or `<yourname>/<issue#>-<slug>` for a human-driven PR, and open a PR with `Closes #n`.
4. **Using Claude Code?** Opening the repo folder loads `CLAUDE.md` and the shared agents/skills in `.claude/` automatically: `/test-app`, `/file-issue`, `/burn-down-issues`, `/release`, `/release-checklist`, `/update-local`, and the `safety-reviewer` agent for anything in `photovault/core/`. Personal settings go in `.claude/settings.local.json` (git-ignored).
5. **Never test against a real photo library.** Tests build temporary folder trees; `python tests/ui_smoke.py <out_dir>` uses a generated sandbox.
6. **Safety first**: the four invariants in `CLAUDE.md` are not negotiable. Core changes need a test and a safety review.
7. Update `CHANGELOG.md` (Unreleased) for user-visible changes and add anything only a human can verify to `docs/HUMAN_TEST_CHECKLIST.md`.
