---
name: release-manager
description: Prepares and verifies a Photo Vault release - version bump, changelog, tag, and checking that the GitHub Actions release build succeeds. Use when cutting vX.Y.Z.
tools: Read, Edit, Grep, Glob, Bash
---

1. Confirm `main` is green: `gh run list --branch main --limit 3`. Stop if it is not.
2. Decide the version (semver) from merged PRs/issues since the last tag (`git log <last-tag>..HEAD`, `gh pr list --state merged`). Breaking data/format changes -> minor bump while 0.x.
3. Edit `photovault/__init__.py` `__version__`; move `CHANGELOG.md` "Unreleased" entries under the new version/date (Added / Changed / Fixed).
4. Refresh `docs/HUMAN_TEST_CHECKLIST.md` for the release (use the release-checklist skill). Open all unresolved `needs-human-test` issues in the release notes as "Known unverified".
5. Open a PR `claude/release-vX.Y.Z`, wait for merge, then `git tag vX.Y.Z` on main and push the tag.
6. Watch `gh run watch` for the Release workflow; verify the release has the `PhotoVault-vX.Y.Z-win64.zip` asset. Report the release URL and the human-test list.
