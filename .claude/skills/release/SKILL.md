---
name: release
description: Cut a Photo Vault release - version bump, changelog, PR, tag vX.Y.Z; GitHub Actions builds and publishes the exe zip. Use when asked to release or ship.
argument-hint: [version, e.g. 0.2.0]
---

Delegate to the `release-manager` agent with the requested version (or let it infer one). Tagging and pushing a tag publishes a public build, so confirm the version and the human-test list with the user before pushing the tag. Report the release URL and unresolved `needs-human-test` items when done.
