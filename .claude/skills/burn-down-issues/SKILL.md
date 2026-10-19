---
name: burn-down-issues
description: Take the most important open GitHub issues on AmineChr54/PhotoVault and solve them in parallel with a few delegated agents, token-economically. Use for "work through the open issues", "tackle the top issues", "burn down the backlog".
argument-hint: [max issues, default 6]
---

# Burn down the top issues in parallel

Goal: solve as many high-value `ai-ready` issues as the budget allows, fast, without inflating context.

1. **Survey** (one call):
   `gh issue list -R AmineChr54/PhotoVault --state open --limit 50 --json number,title,labels --jq '.[] | "#\(.number) \(.title) [\(.labels|map(.name)|join(","))]"'`
2. **Pick** by priority `P0-critical` > `P1-high` > `P2-medium` > `P3-low`, cheap bugs first. Only `ai-ready` issues.
   Skip `needs-human-test` ones (real library, external disk, visuals, model download, clean-machine exe) and say so.
   Skip issues that already have an open PR.
3. **Batch into agents, the sweet spot is 3 (max 4):**
   - Group by `area:*` so PRs don't touch the same files (core / ui / ai / build+docs). Related small issues share one agent.
   - Two big issues in the same files go in different batches; the second after the first merges.
   - Never one agent for everything (slow); never one agent per tiny issue (each re-reads the codebase = tokens).
4. **Launch in one message** with `isolation: "worktree"`, `subagent_type: "general-purpose"`. Each prompt contains: the issue
   numbers (`gh issue view N`), "read CLAUDE.md first", branch `claude/<issue#>-<slug>`, "run `ruff check .` and
   `python -m pytest -q -p no:cacheprovider tests`; if `photovault/core/` changed add tests and run the `safety-reviewer`
   agent", `Closes #N` in the PR, the commit/PR attribution lines from the session reminder, "be economical: don't read
   files you don't need", "never touch a real photo library", and "final report: 5 lines max (PR URL, what was done, what
   needs a human check)". Agents only open PRs.
5. **Monitor usage**: note the token total before launch and after each report; estimate cost per agent from the delta and
   decide how many more issues fit. Post a running tally (issues done, tokens used, remaining).
6. **Merge automatically, without asking**: repo has auto-merge + delete-branch-on-merge on, and `main` requires the CI
   check `test`. When an agent reports a PR run `gh pr merge N --auto --squash` (the `claude/*` automerge workflow also does
   this). Merge PRs touching the same files one at a time; rebase the next first. Then `git pull` main. If CI is red, send
   the failure back to that agent with SendMessage rather than starting a new one.
7. **Loop until done**: run as `/loop` in dynamic mode (no interval). Agent completions wake the loop, so `ScheduleWakeup`
   is only a fallback heartbeat (~1500 s). Each tick: read new reports, merge green PRs, post the tally, launch the next
   batch if there's room. Loop prompt:
   `/loop Continue the burn-down-issues skill on AmineChr54/PhotoVault: launch the next batch of top open ai-ready issues via agents, merge finished PRs (gh pr merge --auto --squash), report token tally, stop when no suitable issues remain.`
   Stop (`ScheduleWakeup` with `stop: true`) when none remain or the usage limit is hit.
8. **Wrap up**: relay each PR URL, then list everything left for a human (`needs-human-test` issues, new items added to
   `docs/HUMAN_TEST_CHECKLIST.md`).

Hard rules: never push to `main`, never skip CI/hooks, never weaken a safety invariant in CLAUDE.md to make a fix easier.
