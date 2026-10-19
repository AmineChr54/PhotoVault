---
name: file-issue
description: File a well-formed, labeled GitHub issue (priority P0-P3, area, bug/feature, ai-ready/needs-human-test) for Photo Vault. Use when a bug, idea or follow-up comes up.
argument-hint: <short description>
---

1. Search first: `gh issue list --state all --search "<keywords>"`. If a duplicate exists, comment on it instead.
2. Pick priority with the rubric in `.claude/agents/issue-triager.md` (P0 = can lose photos or break undo).
3. Create it: `gh issue create --title "..." --body "..." --label "P2-medium,bug,area:core,ai-ready"`. Body: what happened/expected, repro or acceptance criteria, relevant files (`path:line`).
4. Add `needs-human-test` instead of `ai-ready` when it needs a real library, external disk, visual judgment or model download.
5. Return the issue URL.
