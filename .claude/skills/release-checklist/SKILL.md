---
name: release-checklist
description: Regenerate docs/HUMAN_TEST_CHECKLIST.md for the upcoming release - the list of things only a human can test. Use before a release or after risky changes.
---

1. Read `docs/HUMAN_TEST_CHECKLIST.md` (the standing checklist) and the merged PRs/changelog since the last tag.
2. Add a "Release vX.Y.Z" section at the top: for every changed area, concrete steps a human performs on a real machine, with the expected result. Always include the standing items that were touched (scan real library, backup to external disk + restore, undo after organize, Recycle Bin, AI model download, exe on a clean machine).
3. Link each item to its PR/issue; make sure each has a `needs-human-test` issue (use `file-issue`).
4. Do not tick any box yourself.
